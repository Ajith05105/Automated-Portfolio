"""Batched, parallel orchestration of the portfolio pipeline.

Reads attendees from a CSV, runs a SequentialAgent pipeline per attendee,
and writes results to a tracking Google Sheet after each batch completes.

Concurrency: pipelines within a batch run in parallel (asyncio.gather).
Batches are processed sequentially to bound rate-limit pressure on Vertex
and Stitch. Tune BATCH_SIZE down to 3 if 429s start appearing.

Usage:
    python -m automated_portfolio_builder.orchestrator [attendees_csv]
    # Default: ./attendees.csv
"""

import asyncio
import csv
import math
import os
import re
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from .agent import build_root_agent
from .scan_drive import scan_folder
from .sheets import append_results

load_dotenv()

APP_NAME = 'portfolio-builder'
BATCH_SIZE = 1


def slugify(value: str) -> str:
    """Lowercase, hyphen-separated, alphanumerics only — Netlify-safe."""
    value = value.lower().strip()
    value = re.sub(r'[^a-z0-9]+', '-', value)
    return value.strip('-') or 'attendee'


def load_attendees(path: str) -> list[dict]:
    """Reads attendees CSV. Required columns: first_name, last_name, email, file_id."""
    with open(path, newline='') as f:
        rows = list(csv.DictReader(f))
    required = {'first_name', 'last_name', 'email', 'file_id'}
    if rows and not required.issubset(rows[0].keys()):
        missing = required - set(rows[0].keys())
        raise ValueError(f'attendees CSV missing columns: {missing}')
    return rows


async def run_pipeline_for_attendee(
    session_service: InMemorySessionService,
    attendee: dict,
) -> dict:
    """Executes the full pipeline for a single attendee, returns a result row.

    Builds a fresh agent tree (and therefore fresh stdio MCP subprocesses)
    per attendee so concurrent sessions don't share Drive/Netlify pipes.
    """
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')
    first = slugify(attendee['first_name'])
    last = slugify(attendee['last_name'])
    session_id = f'session-{first}-{last}-{timestamp}'
    site_slug = f'portfolio-{first}-{last}-{timestamp}'
    user_id = attendee['email']
    full_name = f"{attendee['first_name']} {attendee['last_name']}".strip()

    initial_state = {
        'attendee_name': full_name,
        'attendee_email': attendee['email'],
        'file_id': attendee['file_id'],
        'site_slug': site_slug,
    }

    agent = build_root_agent()
    runner = Runner(
        agent=agent,
        app_name=APP_NAME,
        session_service=session_service,
    )

    await session_service.create_session(
        app_name=APP_NAME,
        user_id=user_id,
        session_id=session_id,
        state=initial_state,
    )

    trigger = types.Content(role='user', parts=[types.Part(text='run')])
    error = None

    try:
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=trigger,
        ):
            author = getattr(event, 'author', None)
            content = getattr(event, 'content', None)
            if author and content:
                for part in getattr(content, 'parts', []):
                    fc = getattr(part, 'function_call', None)
                    fr = getattr(part, 'function_response', None)
                    text = getattr(part, 'text', None)
                    if fc:
                        print(f'  [{full_name}] {author} → {fc.name}()')
                    elif fr:
                        print(f'  [{full_name}] {author} ← {fr.name} done')
                    elif text and event.is_final_response():
                        print(f'  [{full_name}] {author} finished')
    except Exception as exc:
        error = str(exc)

    session = await session_service.get_session(
        app_name=APP_NAME, user_id=user_id, session_id=session_id
    )
    state = session.state if session else {}
    deployment = state.get('deployment') or {}

    return {
        'attendee_name': full_name,
        'attendee_email': attendee['email'],
        'site_name': deployment.get('site_name', ''),
        'deployed_url': deployment.get('deployed_url', ''),
        'delivery_status': state.get('delivery_status', 'failed'),
        'error': error or '',
    }


async def main(csv_path: str) -> None:
    folder_id = os.environ.get('DRIVE_FOLDER_ID')
    if folder_id:
        print(f'Scanning Drive folder {folder_id} ...')
        attendees = await scan_folder(folder_id)
        if not attendees:
            print('No files found in Drive folder.')
            return
        print(f'Found {len(attendees)} attendees in Drive.')
    else:
        attendees = load_attendees(csv_path)

    if not attendees:
        print('No attendees found.')
        return

    session_service = InMemorySessionService()

    total_batches = math.ceil(len(attendees) / BATCH_SIZE)
    all_results: list[dict] = []

    for batch_index in range(total_batches):
        start = batch_index * BATCH_SIZE
        batch = attendees[start:start + BATCH_SIZE]
        print(f'Batch {batch_index + 1}/{total_batches} — {len(batch)} attendees')

        async def staggered(attendee: dict, index: int) -> dict:
            # Stagger Drive MCP starts so concurrent processes don't race
            # on the same OAuth token file at the exact same moment.
            await asyncio.sleep(index * 10)
            return await run_pipeline_for_attendee(session_service, attendee)

        raw_results = await asyncio.gather(
            *[staggered(a, i) for i, a in enumerate(batch)],
            return_exceptions=True,
        )
        batch_results = [
            r if isinstance(r, dict) else {
                'attendee_name': f"{batch[i].get('first_name', '')} {batch[i].get('last_name', '')}".strip(),
                'attendee_email': batch[i].get('email', ''),
                'site_name': '',
                'deployed_url': '',
                'delivery_status': 'failed',
                'error': str(r),
            }
            for i, r in enumerate(raw_results)
        ]
        all_results.extend(batch_results)

        try:
            append_results(batch_results)
            print(f'  ✓ wrote {len(batch_results)} rows to sheet')
        except Exception as exc:
            print(f'  ✗ sheet write failed: {exc}')

        for r in batch_results:
            status = r['delivery_status']
            url = r['deployed_url'] or '(no URL)'
            print(f'  {status:6s}  {r["attendee_email"]:40s}  {url}')

    deployed = sum(1 for r in all_results if r['deployed_url'])
    delivered = sum(1 for r in all_results if r['delivery_status'] == 'sent')
    print(f'\nDone. {deployed}/{len(all_results)} deployed, {delivered}/{len(all_results)} emailed.')


if __name__ == '__main__':
    csv_path = sys.argv[1] if len(sys.argv) > 1 else 'attendees.csv'
    if not os.path.exists(csv_path):
        print(f'attendees CSV not found: {csv_path}')
        sys.exit(1)
    asyncio.run(main(csv_path))
