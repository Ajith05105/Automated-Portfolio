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

from .agent import root_agent
from .scan_drive import scan_folder
from .sheets import append_results

load_dotenv()

APP_NAME = 'portfolio-builder'
BATCH_SIZE = 5


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
    runner: Runner,
    session_service: InMemorySessionService,
    attendee: dict,
) -> dict:
    """Executes the full pipeline for a single attendee, returns a result row."""
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

    await session_service.create_session(
        app_name=APP_NAME,
        user_id=user_id,
        session_id=session_id,
        state=initial_state,
    )

    trigger = types.Content(role='user', parts=[types.Part(text='run')])
    error = None

    try:
        async for _ in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=trigger,
        ):
            pass  # consume events; final state is committed to the session
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
    runner = Runner(
        agent=root_agent,
        app_name=APP_NAME,
        session_service=session_service,
    )

    total_batches = math.ceil(len(attendees) / BATCH_SIZE)
    all_results: list[dict] = []

    for batch_index in range(total_batches):
        start = batch_index * BATCH_SIZE
        batch = attendees[start:start + BATCH_SIZE]
        print(f'Batch {batch_index + 1}/{total_batches} — {len(batch)} attendees')

        batch_results = await asyncio.gather(
            *[run_pipeline_for_attendee(runner, session_service, a) for a in batch],
            return_exceptions=False,
        )
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
