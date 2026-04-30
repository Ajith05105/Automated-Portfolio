"""Production batch runner for the Automated Portfolio Builder.

Reads approved attendees from a Google Sheet, generates a portfolio site for each
via the Stitch MCP (the only LlmAgent / MCP integration in this script), deploys
the result to Netlify via the Netlify REST API, and emails the live URL to the
attendee. Updates the sheet row with the deploy URL and delivery status as soon
as each attendee finishes — not at the end of the run.

Concurrency is throttled by an asyncio.Semaphore (default 20). Stitch calls
retry automatically on 429 RESOURCE_EXHAUSTED via HttpRetryOptions.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import smtplib
import urllib.error
import urllib.request
import uuid
from email.message import EmailMessage

import gspread
from dotenv import load_dotenv
from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from google.genai import types
from google.oauth2.service_account import Credentials


load_dotenv()

APP_NAME = 'production_run'
USER_ID = 'pipeline'
SHEET_SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
NETLIFY_API = 'https://api.netlify.com/api/v1'


# ── Stitch agent (the only LlmAgent / MCP integration) ───────────────────────


def _build_design_mandate(style: dict) -> str:
    style_name = (style.get('style_name') or '').strip()
    primary    = style.get('primary_color') or '#7c3aed'
    custom     = (style.get('custom_prompt') or '').strip()

    mandates = {
        'Midnight Hacker': f"""       DESIGN MANDATE — Midnight Hacker (dark terminal aesthetic):
       - Background: near-black (#0a0c12 / #0f0f14), {primary} (indigo) as the sole accent
       - Hero: full-viewport, name in huge monospace font (96px+), a blinking cursor
         animation after the name, subtle scanline or dot-grid texture, {primary} glow
         aurora behind the heading — never flat black
       - Typography: JetBrains Mono or Fira Code for ALL text — headings AND body.
         This is a terminal site, everything is monospace
       - Cards (Experience, Projects): dark glass panels with a {primary} left-border,
         faint inner glow, code-comment style labels (// Experience, // Projects),
         slight lift + intensified border glow on hover
       - Skills: rendered as terminal tags — `skill` style pill badges with {primary}
         background at low opacity and {primary} text, monospace font
       - Animations: sections fade-in with a typewriter or glitch effect on headings;
         scroll-triggered slide-up on cards
       - Nav: sticky, frosted dark glass, {primary} underline on active item
       - Contact: a terminal-style button `> contact_me()` with {primary} glow on hover
       - Colour palette: #0a0c12 bg, {primary} accent, #e2e8f0 text""",

        'Ocean Breeze': f"""       DESIGN MANDATE — Ocean Breeze (light, airy, professional):
       - Background: clean white (#ffffff) or soft off-white (#f8fafc), sky-blue
         {primary} as the accent — calm, confident, professional
       - Hero: full-viewport, name in large clean sans-serif (Inter or Plus Jakarta Sans,
         80px+), a short punchy tagline, soft animated gradient wash of {primary} at low
         opacity behind the text — never plain white, a subtle wave or mesh SVG pattern
       - Typography: Inter or Plus Jakarta Sans — bold headings (700–800 weight),
         clean regular body text, high contrast dark-on-light
       - Cards: crisp white with a subtle drop shadow (0 4px 24px rgba(0,0,0,0.08)),
         {primary} left-border accent (4px), gentle lift on hover (translateY(-4px) + stronger shadow)
       - Skills: bold pill badges filled with {primary} at 15% opacity, {primary} text,
         {primary} border — clean and readable
       - Animations: smooth scroll-triggered fade-up on every section
       - Nav: sticky white with border-bottom, background goes fully opaque on scroll
       - Contact: bold filled button in {primary} with white text, hover darkens slightly
       - Colour palette: #ffffff bg, {primary} accent, #0f172a text""",

        'Forest Minimal': f"""       DESIGN MANDATE — Forest Minimal (light, geometric, design-forward):
       - Background: soft white (#ffffff) or warm off-white (#f9fafb), emerald {primary}
         as accent — understated, elegant, lots of whitespace
       - Hero: full-viewport, name in a bold geometric sans-serif (Syne or Space Grotesk,
         88px+), minimal layout with generous negative space, a subtle geometric SVG
         pattern or thin-line grid in {primary} at 8% opacity — no loud gradients
       - Typography: Syne or Space Grotesk for headings (geometric, modern), Inter for
         body — strong typographic hierarchy, minimal decoration
       - Cards: very clean — white, thin 1px {primary} border, no heavy shadows,
         a barely-there background tint on hover, text-first layout
       - Skills: minimal outlined pill badges — 1px {primary} border, {primary} text,
         transparent fill — refined and spacious
       - Animations: subtle fade-in only, no bouncy effects — restraint is the aesthetic
       - Nav: minimal, thin border-bottom, clean links with {primary} underline on hover
       - Contact: outlined button, {primary} border and text, fills {primary} on hover
       - Colour palette: #f9fafb bg, {primary} accent, #111827 text""",

        'Sunset Creative': f"""       DESIGN MANDATE — Sunset Creative (dark, bold, loud, unforgettable):
       - Background: deep dark (#0c0a09 / #1a0a00), {primary} (orange) as the explosive
         accent — bold, energetic, maximum visual impact
       - Hero: full-viewport, name in MASSIVE bold display font (Bebas Neue or Barlow
         Condensed, 120px+), all-caps or mixed-weight treatment, a dramatic gradient
         background blending {primary} through red (#ef4444) to near-black —
         like a burning sunset. Add large decorative abstract shapes or blobs in {primary}
       - Typography: Bebas Neue or Barlow Condensed for headings (huge, condensed, bold),
         Inter or DM Sans for body — extreme size contrast between headings and body
       - Cards (Experience, Projects): dark semi-transparent panels with a vivid {primary}
         gradient top-border (3px), strong lift + {primary} shadow glow on hover
       - Skills: bold chunky pill badges with {primary} fill, dark text — high energy
       - Animations: bold scale-up + fade-in on scroll; hero text has a dramatic entrance
         (slide up + fade); hover states are intense
       - Nav: dark, semi-transparent, {primary} active indicator
       - Contact: big bold filled button in {primary} gradient, hover scale + glow pulse
       - Colour palette: #0c0a09 bg, {primary} → #ef4444 gradient accent, #f5f5f4 text""",

        'Corporate Clean': f"""       DESIGN MANDATE — Corporate Clean (light, structured, boardroom-ready):
       - Background: crisp pure white (#ffffff), {primary} (blue) as a refined accent —
         sharp, structured, authoritative
       - Hero: full-viewport, name in an elegant serif font (Playfair Display or
         Merriweather, 80px+), a professional subtitle/tagline below, a clean geometric
         section divider, very subtle {primary} watermark or thin-line grid in background
       - Typography: Playfair Display or Merriweather for headings (serif, authoritative),
         Inter or Source Sans Pro for body — classic editorial hierarchy
       - Cards: clean white with a thin {primary} top-border (3px), structured layout,
         subtle shadow (0 2px 16px rgba(0,0,0,0.06)), restrained hover effect
       - Skills: professional pill badges — {primary} fill at 10%, {primary} border,
         {primary} text — refined and consistent
       - Animations: minimal — clean fade-in only, no bouncing or scaling
       - Nav: clean white sticky nav with {primary} active underline, border-bottom divider
       - Contact: structured button with {primary} fill, white text, slight hover darken
       - Colour palette: #ffffff bg, {primary} accent, #1e293b text""",
    }

    mandate = mandates.get(style_name)
    if not mandate:
        theme = (style.get('theme') or 'dark').strip().lower()
        if theme == 'light':
            mandate = f"""       DESIGN MANDATE:
       - Light airy aesthetic: clean white base, {primary} as accent
       - Hero: full-viewport, name 80px+, animated soft gradient, never plain white
       - Cards: white with drop shadow and {primary} left-border accent, hover lift
       - Skills as bold pill badges filled with {primary}
       - Scroll-triggered fade-in animations, sticky nav, mobile responsive"""
        else:
            mandate = f"""       DESIGN MANDATE:
       - Dark sleek aesthetic: near-black base (#0f0f14), {primary} as accent
       - Hero: full-viewport, name 96px+, aurora glow effect using {primary}
       - Cards: dark glass-morphism, {primary} border glow, hover intensifies glow
       - Skills as glowing pill badges with {primary} accent
       - Scroll-triggered fade-in animations, frosted sticky nav, mobile responsive"""

    if custom:
        mandate += f"\n\n       ADDITIONAL CUSTOM DIRECTION FROM CANDIDATE:\n       {custom}"
    return mandate


def _build_contact_links_block(email: str, github_url: str, linkedin_url: str, website_url: str) -> str:
    lines = [
        'CONTACT / SOCIAL LINKS — use these EXACT href values in the HTML, no placeholders, no "#":',
    ]
    if email:
        lines.append(f'  - Email:    <a href="mailto:{email}">{email}</a>')
    if github_url:
        lines.append(f'  - GitHub:   <a href="{github_url}">{github_url}</a>')
    if linkedin_url:
        lines.append(f'  - LinkedIn: <a href="{linkedin_url}">{linkedin_url}</a>')
    if website_url:
        lines.append(f'  - Website:  <a href="{website_url}">{website_url}</a>')
    lines.append('  Omit any link where the URL is empty. Never output href="#" or href="url".')
    return '\n'.join(lines)


def _build_stitch_instruction(cv: dict, style: dict) -> str:
    name = cv.get('name') or 'Anonymous'
    summary = cv.get('summary') or ''
    skills = cv.get('skills') or []
    projects = json.dumps(cv.get('projects') or [], indent=2)
    experience = json.dumps(cv.get('experience') or [], indent=2)
    education = json.dumps(cv.get('education') or [], indent=2)
    email = cv.get('email') or ''
    github_url = cv.get('github_url') or ''
    linkedin_url = cv.get('linkedin_url') or ''
    website_url = cv.get('website_url') or ''
    mandate = _build_design_mandate(style)
    contact_block = _build_contact_links_block(email, github_url, linkedin_url, website_url)

    return f"""You are a world-class UI design automation agent. Follow only these steps.

STRICT RULE — NO HALLUCINATION:
Only use the data provided below. If a field is empty, omit that section entirely.
Do NOT invent names, job titles, companies, skills, projects, links, or any other
information. Do NOT use placeholder text like "Your Name", "Company Name", or
"example.com". If a section has no data, leave it out of the design completely.

CV DATA (already structured — use these values directly):
- name: {name}
- summary: {summary}
- skills: {skills}
- experience: {experience}
- education: {education}
- projects: {projects}

{contact_block}

Steps — execute in order without asking for confirmation:

1. Use "{name}" as the person's name.

2. Call create_project with title "{name}'s Portfolio".

3. Build a visually stunning design prompt combining all CV highlights with
   the following mandatory design direction. DO NOT water this down:

{mandate}

4. SECTIONS TO INCLUDE:
   - Hero: "{name}" as the main heading with a punchy tagline from the summary
   - About: expanded bio from the summary
   - Experience: cards for each role — company, title, duration, description
   - Education: institution, degree, duration
   - Skills: glowing pill badges for every skill listed
   - Projects: cards with name, description; clickable if a project URL exists
   - Contact/Footer: social links using the EXACT href values from the contact block above

5. Call generate_screen_from_text with the project ID, the full prompt, and
   modelId set to GEMINI_3_FLASH. Wait for the screen to finish.

6. Call get_screen with the project ID. The response contains :
   - A download URL starting with http — return the URL exactly as-is
   Return ONLY the  download URL. No commentary, no markdown.
"""


def _build_stitch_agent(cv: dict, style: dict) -> LlmAgent:
    instruction = _build_stitch_instruction(cv, style)

    def instruction_provider(_ctx) -> str:
        return instruction

    return LlmAgent(
        model='gemini-2.5-flash',
        name='stitch_agent',
        generate_content_config=types.GenerateContentConfig(
            temperature=0.4,
            http_options=types.HttpOptions(
                retry_options=types.HttpRetryOptions(
                    initial_delay=2,
                    attempts=5,
                ),
            ),
        ),
        instruction=instruction_provider,
        tools=[
            McpToolset(
                connection_params=StreamableHTTPConnectionParams(
                    url='https://stitch.googleapis.com/mcp',
                    headers={'X-Goog-Api-Key': os.environ.get('STITCH_API_KEY', '')},
                ),
                tool_filter=['create_project', 'generate_screen_from_text', 'get_screen'],
            ),
        ],
    )


def _find_html_url(value) -> str | None:
    """Recursively search a structure for a contribution.usercontent.google.com URL."""
    if isinstance(value, str):
        if 'contribution.usercontent.google.com' in value and value.startswith('http'):
            return value
        return None
    if isinstance(value, dict):
        for v in value.values():
            found = _find_html_url(v)
            if found:
                return found
    if isinstance(value, list):
        for v in value:
            found = _find_html_url(v)
            if found:
                return found
    return None


def _find_html_string(value) -> str | None:
    """Recursively search a structure for raw HTML starting with <!DOCTYPE."""
    if isinstance(value, str):
        if '<!DOCTYPE' in value.upper():
            return value
        return None
    if isinstance(value, dict):
        for v in value.values():
            found = _find_html_string(v)
            if found:
                return found
    if isinstance(value, list):
        for v in value:
            found = _find_html_string(v)
            if found:
                return found
    return None


async def run_stitch_agent(cv: dict, style: dict) -> str:
    agent = _build_stitch_agent(cv, style)
    session_service = InMemorySessionService()
    session_id = f"session-{uuid.uuid4()}"
    await session_service.create_session(
        app_name=APP_NAME, user_id=USER_ID, session_id=session_id,
    )
    runner = Runner(app_name=APP_NAME, agent=agent, session_service=session_service)

    html_string = None
    download_url = None
    final_text = ''

    user_msg = types.Content(role='user', parts=[types.Part(text='Generate the portfolio.')])
    async for event in runner.run_async(
        user_id=USER_ID, session_id=session_id, new_message=user_msg,
    ):
        if not (event.content and event.content.parts):
            continue

        for part in event.content.parts:
            # Pull HTML / URL straight from the get_screen tool response
            func_resp = getattr(part, 'function_response', None)
            if func_resp:
                response = getattr(func_resp, 'response', None)
                if response is not None:
                    if not html_string:
                        html_string = _find_html_string(response)
                    if not download_url:
                        download_url = _find_html_url(response)

            # Fallback: model's final text
            if event.is_final_response():
                text = getattr(part, 'text', None)
                if text and not final_text:
                    final_text = text

    if html_string:
        print(f"[STITCH] Got HTML directly from tool response ({len(html_string)} chars)")
        return html_string.strip()

    if download_url:
        print(f"[STITCH] Got download URL from tool response: {download_url[:80]}...")
        return await asyncio.to_thread(_fetch_url, download_url)

    # Last resort: whatever the model said
    result = final_text.strip()
    print(f"[STITCH] Falling back to model output: {result[:120]!r}")
    if result.startswith('http') and 'lh3.googleusercontent.com' in result:
        raise RuntimeError(
            'Stitch only returned an image preview URL. Re-run this attendee.'
        )
    if result.startswith('http'):
        return await asyncio.to_thread(_fetch_url, result)
    return result


def _fetch_url(url: str) -> str:
    req = urllib.request.Request(
        url, headers={
            'User-Agent': 'Mozilla/5.0 (portfolio-runner)',
            'Accept': 'text/html,application/xhtml+xml,*/*',
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode('utf-8')


# ── Netlify deploy (REST API — no CLI, no MCP) ───────────────────────────────


def _load_netlify_token() -> str:
    env_token = os.environ.get('NETLIFY_AUTH_TOKEN', '').strip()
    if env_token:
        return env_token

    candidate_paths = [
        os.path.expanduser('~/Library/Preferences/netlify/config.json'),  # macOS
        os.path.expanduser('~/.config/netlify/config.json'),              # Linux (XDG)
        os.path.expanduser('~/.netlify/config.json'),                     # legacy
        os.path.join(os.environ.get('APPDATA', ''), 'netlify', 'config.json'),  # Windows
    ]

    for config_path in candidate_paths:
        if not config_path or not os.path.exists(config_path):
            continue
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            users = config.get('users', {})
            if not users:
                continue
            active_id = config.get('userId')
            if active_id and active_id in users:
                user = users[active_id]
            else:
                user = next(iter(users.values()))
            token = user.get('auth', {}).get('token', '')
            if token:
                return token
        except (OSError, json.JSONDecodeError, StopIteration):
            continue

    raise RuntimeError(
        'No Netlify auth token found. Set NETLIFY_AUTH_TOKEN or run "netlify login".'
    )


def _slugify_site_name(name: str) -> str:
    base = re.sub(r'[^a-z0-9]+', '-', (name or '').lower()).strip('-') or 'attendee'
    suffix = uuid.uuid4().hex[:6]
    site = f"{base}-{suffix}"
    if not re.match(r'^[a-z0-9-]+$', site):
        raise ValueError(f"Invalid site name derived from {name!r}: {site!r}")
    return site


def _netlify_request_sync(
    token: str,
    method: str,
    path: str,
    *,
    json_body=None,
    raw_body: bytes | None = None,
    extra_headers: dict | None = None,
) -> str:
    import time
    url = f"{NETLIFY_API}{path}"
    headers = {'Authorization': f'Bearer {token}'}
    data: bytes | None = None

    if json_body is not None:
        data = json.dumps(json_body).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    elif raw_body is not None:
        data = raw_body
        headers.setdefault('Content-Type', 'application/octet-stream')

    if extra_headers:
        headers.update(extra_headers)

    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.read().decode('utf-8')
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8', errors='replace')
            if e.code == 429 and attempt < 4:
                wait = 2 ** attempt * 5
                print(f"[NETLIFY] Rate limited, retrying in {wait}s...")
                time.sleep(wait)
                continue
            raise RuntimeError(f"Netlify {method} {path} failed ({e.code}): {body}") from e
    raise RuntimeError(f"Netlify {method} {path} failed after 5 attempts")


async def _netlify_request(token, method, path, **kwargs) -> str:
    return await asyncio.to_thread(_netlify_request_sync, token, method, path, **kwargs)


async def deploy_to_netlify(html: str, name: str, token: str, account_slug: str) -> str:
    if html.strip().lower().startswith('http'):
        html = await asyncio.to_thread(_fetch_url, html.strip())

    site_name = _slugify_site_name(name)
    site_resp = await _netlify_request(
        token, 'POST', f'/{account_slug}/sites',
        json_body={'name': site_name},
    )
    site = json.loads(site_resp)
    site_id = site.get('id') or site.get('site_id')
    if not site_id:
        raise RuntimeError(f"No site_id returned by Netlify: {site_resp}")

    html_bytes = html.encode('utf-8')
    sha1 = hashlib.sha1(html_bytes).hexdigest()

    deploy_resp = await _netlify_request(
        token, 'POST', f'/sites/{site_id}/deploys',
        json_body={'files': {'/index.html': sha1}, 'draft': False},
    )
    deploy = json.loads(deploy_resp)
    deploy_id = deploy['id']
    required = deploy.get('required', [])

    if sha1 in required:
        await _netlify_request(
            token, 'PUT', f'/deploys/{deploy_id}/files/index.html',
            raw_body=html_bytes,
        )

    return f"https://{site_name}.netlify.app"


# ── Email delivery (smtplib in thread executor) ──────────────────────────────


async def send_portfolio_email(name: str, email: str, deploy_url: str, html: str) -> str:
    smtp_user = os.environ.get('GMAIL_USER', '')
    smtp_pass = os.environ.get('GMAIL_APP_PASSWORD', '')
    if not smtp_user or not smtp_pass:
        print(f"[EMAIL ERROR] {name}: GMAIL_USER / GMAIL_APP_PASSWORD not set")
        return 'failed'

    parts = [p for p in re.split(r'\s+', (name or '').strip()) if p]
    slug = '-'.join(re.sub(r'[^a-z0-9]+', '', p.lower()) for p in parts) or 'attendee'
    attachment_filename = f"{slug}-portfolio.html"

    body_html = (
        f"<p>Hi {name},</p>"
        f"<p>Your portfolio is live! Check it out here:<br>"
        f"<a href=\"{deploy_url}\">{deploy_url}</a></p>"
        f"<p>You can write this URL to your NFC tag during the Makers Club session.</p>"
        f"<p>See you at the workshop!<br>— GDGC Auckland Team</p>"
    )
    body_text = (
        f"Hi {name},\n\n"
        f"Your portfolio is live! Check it out here:\n{deploy_url}\n\n"
        f"You can write this URL to your NFC tag during the Makers Club session.\n\n"
        f"See you at the workshop!\n— GDGC Auckland Team\n"
    )

    def _send_sync() -> None:
        msg = EmailMessage()
        msg['Subject'] = 'Your GDGC Auckland Portfolio is Live 🎉'
        msg['From'] = smtp_user
        msg['To'] = email
        msg.set_content(body_text)
        msg.add_alternative(body_html, subtype='html')
        msg.add_attachment(
            html.encode('utf-8'),
            maintype='text',
            subtype='html',
            filename=attachment_filename,
        )
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(smtp_user, smtp_pass)
            smtp.send_message(msg)

    try:
        await asyncio.to_thread(_send_sync)
        return 'sent'
    except Exception as e:
        print(f"[EMAIL ERROR] {name} ({email}): {e}")
        return 'failed'


# ── Per-attendee orchestration ───────────────────────────────────────────────


def _parse_json_field(value) -> list:
    if not value:
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError):
        return []


def _parse_row(row: dict) -> tuple[dict, dict]:
    skills_raw = row.get('skills', '')
    if isinstance(skills_raw, list):
        skills = [str(s).strip() for s in skills_raw if str(s).strip()]
    else:
        skills = [s.strip() for s in str(skills_raw).split(',') if s.strip()]

    cv_content = {
        'name': str(row.get('name', '')).strip(),
        'email': str(row.get('email', '')).strip(),
        'phone': str(row.get('phone', '')).strip(),
        'summary': str(row.get('summary', '')).strip(),
        'experience': _parse_json_field(row.get('experience', '')),
        'education': _parse_json_field(row.get('education', '')),
        'skills': skills,
        'projects': _parse_json_field(row.get('projects', '')),
        'github_url': str(row.get('github_url', '')).strip(),
        'linkedin_url': str(row.get('linkedin_url', '')).strip(),
        'website_url': str(row.get('website_url', '')).strip(),
    }
    style = {
        'style_name': str(row.get('style_name', '')).strip(),
        'theme': str(row.get('theme', 'dark')).strip(),
        'primary_color': str(row.get('primary_color', '#7c3aed')).strip(),
        'font_preference': str(row.get('font_preference', 'modern sans-serif')).strip(),
        'custom_prompt': str(row.get('custom_prompt', '')).strip(),
    }
    return cv_content, style


async def run_pipeline_for_attendee(
    row: dict,
    row_index: int,
    sheet,
    semaphore: asyncio.Semaphore,
    netlify_token: str,
    netlify_slug: str,
) -> dict:
    async with semaphore:
        name = str(row.get('name', 'Unknown')).strip() or 'Unknown'
        email = str(row.get('email', '')).strip()
        deploy_url = ''
        delivery_status = 'failed'

        try:
            cv_content, style = _parse_row(row)
            html = await run_stitch_agent(cv_content, style)
            if not html:
                raise RuntimeError('Stitch returned empty response.')
            deploy_url = await deploy_to_netlify(html, name, netlify_token, netlify_slug)
            delivery_status = await send_portfolio_email(name, email, deploy_url, html)
        except Exception as e:
            print(f"[FAILED] {name}: {e}")

        try:
            await asyncio.to_thread(
                sheet.update,
                [[deploy_url, delivery_status]],
                f"S{row_index}:T{row_index}",
            )
        except Exception as e:
            print(f"[SHEET WRITE FAILED] {name} row {row_index}: {e}")

        print(f"[DONE] {name} → {deploy_url or 'no-url'} → {delivery_status}")
        return {'name': name, 'deploy_url': deploy_url, 'delivery_status': delivery_status}


# ── Sheet IO + main ──────────────────────────────────────────────────────────


def _open_sheet():
    sheet_id = os.environ.get('GOOGLE_SHEET_ID', '').strip()
    json_path = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON', '').strip()
    if not sheet_id or not json_path:
        raise SystemExit('GOOGLE_SHEET_ID and GOOGLE_SERVICE_ACCOUNT_JSON must be set.')
    creds = Credentials.from_service_account_file(json_path, scopes=SHEET_SCOPES)
    gc = gspread.authorize(creds)
    return gc.open_by_key(sheet_id).sheet1


def _read_rows(sheet) -> list[dict]:
    values = sheet.get_all_values()
    if not values:
        return []
    headers = values[0]
    return [dict(zip(headers, row)) for row in values[1:] if any(row)]


async def main() -> None:
    sheet = _open_sheet()
    rows = _read_rows(sheet)
    approved = [
        (row, idx + 2)
        for idx, row in enumerate(rows)
        if str(row.get('status', '')).strip().lower() == 'approved'
    ]

    if not approved:
        print('No approved rows found. Exiting cleanly.')
        return

    # Fetch Netlify token + team slug once — reused by every attendee
    netlify_token = _load_netlify_token()
    accounts = json.loads(await _netlify_request(netlify_token, 'GET', '/accounts'))
    if not accounts:
        raise SystemExit('No Netlify accounts found for the current user.')
    netlify_slug = accounts[0]['slug']
    print(f"Netlify team: {netlify_slug}")

    batch_size = max(1, int(os.environ.get('BATCH_SIZE', '5')))
    batch_delay = float(os.environ.get('BATCH_DELAY', '15'))
    print(f"Found {len(approved)} approved rows. Batches of {batch_size}, {batch_delay}s between batches.")

    semaphore = asyncio.Semaphore(batch_size)
    results = []
    for i in range(0, len(approved), batch_size):
        batch = approved[i:i + batch_size]
        if i > 0:
            print(f"[BATCH] Waiting {batch_delay}s before next batch...")
            await asyncio.sleep(batch_delay)
        batch_results = await asyncio.gather(
            *[
                run_pipeline_for_attendee(row, idx, sheet, semaphore, netlify_token, netlify_slug)
                for row, idx in batch
            ],
            return_exceptions=True,
        )
        results.extend(batch_results)

    succeeded = 0
    failed = 0
    for r in results:
        if isinstance(r, Exception):
            print(f"[BATCH ERROR] {r}")
            failed += 1
        elif r.get('delivery_status') == 'sent':
            succeeded += 1
        else:
            failed += 1
    print(f"Pipeline complete. {succeeded} deployed successfully. {failed} failed.")


if __name__ == '__main__':
    asyncio.run(main())