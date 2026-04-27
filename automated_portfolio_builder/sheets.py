"""Google Sheets results writer.

Authenticates with Application Default Credentials (set up via
`gcloud auth application-default login`). The target sheet is identified
by RESULTS_SHEET_ID; the first tab is appended to. The sheet is expected
to have a header row in this order:

    timestamp | attendee_name | attendee_email | site_name | deployed_url | delivery_status | error
"""

import os
from datetime import datetime, timezone

import gspread
from google.auth import default

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive',
]


def _get_worksheet():
    sheet_id = os.environ.get('RESULTS_SHEET_ID')
    if not sheet_id:
        raise RuntimeError('RESULTS_SHEET_ID env var not set')
    creds, _ = default(scopes=SCOPES)
    client = gspread.authorize(creds)
    return client.open_by_key(sheet_id).sheet1


def append_results(results: list[dict]) -> None:
    """Appends one row per result to the tracking sheet."""
    if not results:
        return
    ws = _get_worksheet()
    now = datetime.now(timezone.utc).isoformat()
    rows = [
        [
            now,
            r.get('attendee_name', ''),
            r.get('attendee_email', ''),
            r.get('site_name', ''),
            r.get('deployed_url', ''),
            r.get('delivery_status', ''),
            r.get('error', ''),
        ]
        for r in results
    ]
    ws.append_rows(rows, value_input_option='USER_ENTERED')
