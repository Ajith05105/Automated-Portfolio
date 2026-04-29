"""
Runs the full portfolio pipeline for every attendee in attendees.csv.

Pipeline per attendee (concurrent, semaphore-capped):
    1. Fetch CV plain text from the attendee's Google Doc (Drive API).
    2. Generate portfolio HTML via Stitch — only LLM step, reuses the
       build_stitch_agent factory from automated_portfolio_builder.agent.
    3. Deploy to Netlify by shelling out to the netlify CLI.
    4. Email the live URL + HTML attachment to the attendee (Gmail SMTP).

Required env vars:
    GOOGLE_SERVICE_ACCOUNT_JSON  Path to a Google Cloud service account JSON.
                                 Used to fetch each attendee's CV from Drive.
                                 Service account needs read access to every
                                 CV doc (share the Drive folder with it).
    STITCH_API_KEY               Google Stitch API key — passed by the Stitch
                                 agent's MCP toolset.
    NETLIFY_AUTH_TOKEN           Netlify personal access token — passed to
                                 `netlify deploy --auth=...`.
    GMAIL_USER                   Gmail address to send portfolio emails from.
    GMAIL_APP_PASSWORD           Gmail app password (requires 2FA enabled).
    GOOGLE_GENAI_USE_VERTEXAI    Set to 1 to use Vertex AI for the Stitch
                                 agent's Gemini calls (recommended).
    GOOGLE_CLOUD_PROJECT         Vertex AI project ID.
    GOOGLE_CLOUD_LOCATION        Vertex AI region (e.g. us-central1).

Usage:
    python run.py
    python run.py path/to/attendees.csv
"""

import asyncio
import csv
import os
import re
import smtplib
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage

from dotenv import load_dotenv
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from google.oauth2 import service_account
from googleapiclient.discovery import build

from automated_portfolio_builder.agent import build_stitch_agent

load_dotenv()

CONCURRENCY = 5
APP_NAME = 'portfolio-runner'
DRIVE_SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
NETLIFY_DEPLOY_TIMEOUT = 300
URL_RE = re.compile(r'https://[a-z0-9-]+\.netlify\.app')


# ── Drive ────────────────────────────────────────────────────────────────────

async def fetch_cv(file_id: str) -> str:
    def _fetch() -> str:
        creds = service_account.Credentials.from_service_account_file(
            os.environ['GOOGLE_SERVICE_ACCOUNT_JSON'], scopes=DRIVE_SCOPES,
        )
        svc = build('drive', 'v3', credentials=creds, cache_discovery=False)
        raw = svc.files().export(fileId=file_id, mimeType='text/plain').execute()
        return raw.decode('utf-8') if isinstance(raw, bytes) else str(raw)
    return await asyncio.to_thread(_fetch)


# ── Stitch (via the build_stitch_agent factory) ──────────────────────────────

async def generate_portfolio_html(cv_text: str, attendee_id: str) -> str:
    """Runs the Stitch agent against a fresh session and returns raw HTML."""
    session_service = InMemorySessionService()
    agent = build_stitch_agent()
    runner = Runner(agent=agent, app_name=APP_NAME, session_service=session_service)

    session_id = f'stitch-{attendee_id}'
    user_id = attendee_id
    await session_service.create_session(
        app_name=APP_NAME,
        user_id=user_id,
        session_id=session_id,
        state={'cv_content': cv_text},
    )

    trigger = types.Content(role='user', parts=[types.Part(text='run')])
    async for _ in runner.run_async(
        user_id=user_id, session_id=session_id, new_message=trigger,
    ):
        pass

    session = await session_service.get_session(
        app_name=APP_NAME, user_id=user_id, session_id=session_id,
    )
    raw = (session.state if session else {}).get('generated_html', '').strip()
    if not raw:
        raise RuntimeError('Stitch agent returned empty generated_html')

    if raw.startswith('http'):
        def _fetch_url() -> str:
            with urllib.request.urlopen(raw, timeout=60) as resp:
                return resp.read().decode('utf-8')
        return await asyncio.to_thread(_fetch_url)
    if '<' not in raw:
        raise RuntimeError(f'Stitch returned neither URL nor HTML: {raw[:200]!r}')
    return raw


# ── Netlify deploy via CLI ───────────────────────────────────────────────────

def slugify(value: str) -> str:
    value = (value or '').lower().strip()
    value = re.sub(r'[^a-z0-9]+', '-', value)
    return value.strip('-') or 'attendee'


async def deploy_to_netlify(html: str, site_slug: str) -> str:
    def _write_html(path: str) -> None:
        with open(os.path.join(path, 'index.html'), 'w') as f:
            f.write(html)

    tmpdir = tempfile.mkdtemp(prefix='netlify_deploy_')
    await asyncio.to_thread(_write_html, tmpdir)

    proc = await asyncio.create_subprocess_exec(
        'netlify', 'deploy',
        f'--dir={tmpdir}',
        '--prod',
        f'--auth={os.environ["NETLIFY_AUTH_TOKEN"]}',
        f'--name={site_slug}',
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(
            proc.communicate(), timeout=NETLIFY_DEPLOY_TIMEOUT,
        )
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError('netlify deploy timed out after 5 minutes')

    output = stdout.decode('utf-8', errors='replace') if stdout else ''
    if proc.returncode != 0:
        raise RuntimeError(f'netlify deploy failed (code {proc.returncode}):\n{output}')

    match = URL_RE.search(output)
    return match.group(0) if match else f'https://{site_slug}.netlify.app'


# ── Email ────────────────────────────────────────────────────────────────────

async def send_email(recipient_name: str, recipient_email: str,
                     deployed_url: str, html: str) -> None:
    def _send() -> None:
        msg = EmailMessage()
        msg['Subject'] = f'Your portfolio is live — {deployed_url}'
        msg['From'] = os.environ['GMAIL_USER']
        msg['To'] = recipient_email
        msg.set_content(
            f"Hi {recipient_name},\n\n"
            f"Your portfolio website is live at:\n{deployed_url}\n\n"
            "The HTML source is attached if you'd like to host it elsewhere "
            "or tweak it.\n\n— GDGC Auckland"
        )
        msg.add_attachment(
            html.encode('utf-8'),
            maintype='text', subtype='html',
            filename='portfolio.html',
        )
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(os.environ['GMAIL_USER'], os.environ['GMAIL_APP_PASSWORD'])
            smtp.send_message(msg)
    await asyncio.to_thread(_send)


# ── Pipeline ─────────────────────────────────────────────────────────────────

async def run_pipeline(attendee: dict, semaphore: asyncio.Semaphore) -> dict:
    full_name = f"{attendee['first_name']} {attendee['last_name']}".strip()
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')
    site_slug = (
        f'portfolio-{slugify(attendee["first_name"])}-'
        f'{slugify(attendee["last_name"])}-{timestamp}'
    )

    result = {
        'first_name': attendee['first_name'],
        'last_name': attendee['last_name'],
        'email': attendee['email'],
        'deployed_url': '',
        'status': 'failed',
        'error': '',
    }

    async with semaphore:
        try:
            print(f'  [{full_name}] fetching CV ...')
            cv_text = await fetch_cv(attendee['file_id'])

            print(f'  [{full_name}] generating portfolio via Stitch ...')
            html = await generate_portfolio_html(cv_text, site_slug)

            print(f'  [{full_name}] deploying to Netlify ...')
            deployed_url = await deploy_to_netlify(html, site_slug)
            result['deployed_url'] = deployed_url

            print(f'  [{full_name}] emailing ...')
            await send_email(full_name, attendee['email'], deployed_url, html)

            result['status'] = 'sent'
            print(f'  ✓ {full_name}  {deployed_url}')
        except Exception as exc:
            result['error'] = str(exc)
            print(f'  ✗ {full_name}  {exc}')

    return result


def load_attendees(path: str) -> list[dict]:
    with open(path, newline='') as f:
        return list(csv.DictReader(f))


def write_results(rows: list[dict], path: str) -> None:
    cols = ['first_name', 'last_name', 'email', 'deployed_url', 'status', 'error']
    with open(path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=cols)
        writer.writeheader()
        writer.writerows(rows)


async def main(csv_path: str) -> None:
    attendees = load_attendees(csv_path)
    if not attendees:
        print('No attendees found.')
        return

    print(
        f'Running pipeline for {len(attendees)} attendees '
        f'(concurrency={CONCURRENCY}) ...\n'
    )
    semaphore = asyncio.Semaphore(CONCURRENCY)

    raw = await asyncio.gather(
        *[run_pipeline(a, semaphore) for a in attendees],
        return_exceptions=True,
    )

    results: list[dict] = []
    for attendee, r in zip(attendees, raw):
        if isinstance(r, dict):
            results.append(r)
        else:
            results.append({
                'first_name': attendee['first_name'],
                'last_name': attendee['last_name'],
                'email': attendee['email'],
                'deployed_url': '',
                'status': 'failed',
                'error': str(r),
            })

    write_results(results, 'results.csv')
    sent = sum(1 for r in results if r['status'] == 'sent')
    print(f'\nDone. {sent}/{len(results)} succeeded. Results in results.csv.')


if __name__ == '__main__':
    csv_path = sys.argv[1] if len(sys.argv) > 1 else 'attendees.csv'
    if not os.path.exists(csv_path):
        print(f'attendees CSV not found: {csv_path}')
        sys.exit(1)
    asyncio.run(main(csv_path))
