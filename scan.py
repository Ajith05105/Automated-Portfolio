"""
Scans a Google Drive folder and writes attendees.csv.

Required env vars:
    GOOGLE_SERVICE_ACCOUNT_JSON  Path to a Google Cloud service account JSON.
                                 The service account must have read access to
                                 the Drive folder (share the folder with the
                                 service account's email).
    DRIVE_FOLDER_ID              ID of the Drive folder containing CV docs.

Usage:
    python scan.py
"""

import csv
import os
import re
import sys

from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

load_dotenv()

SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
COLUMNS = ['first_name', 'last_name', 'email', 'github', 'linkedin', 'file_id']

FIELD_PATTERNS = {
    'name': re.compile(r'^\s*Name\s*:\s*(.+)$', re.MULTILINE | re.IGNORECASE),
    'email': re.compile(r'^\s*(?:Contact\s+)?Email\s*:\s*(.+)$', re.MULTILINE | re.IGNORECASE),
    'github': re.compile(r'^\s*GitHub\s*:\s*(.+)$', re.MULTILINE | re.IGNORECASE),
    'linkedin': re.compile(r'^\s*LinkedIn\s*:\s*(.+)$', re.MULTILINE | re.IGNORECASE),
}


def get_drive_service():
    creds = service_account.Credentials.from_service_account_file(
        os.environ['GOOGLE_SERVICE_ACCOUNT_JSON'], scopes=SCOPES,
    )
    return build('drive', 'v3', credentials=creds, cache_discovery=False)


def list_docs(service, folder_id: str) -> list[dict]:
    query = (
        f"'{folder_id}' in parents and "
        "mimeType='application/vnd.google-apps.document' and "
        "trashed=false"
    )
    docs, page_token = [], None
    while True:
        resp = service.files().list(
            q=query,
            fields='nextPageToken, files(id, name)',
            pageToken=page_token,
        ).execute()
        docs.extend(resp.get('files', []))
        page_token = resp.get('nextPageToken')
        if not page_token:
            break
    return docs


def fetch_doc_text(service, file_id: str) -> str:
    raw = service.files().export(fileId=file_id, mimeType='text/plain').execute()
    return raw.decode('utf-8') if isinstance(raw, bytes) else str(raw)


def extract(text: str, key: str) -> str:
    m = FIELD_PATTERNS[key].search(text)
    return m.group(1).strip() if m else ''


def parse_doc(text: str) -> dict:
    name = extract(text, 'name')
    parts = name.split() if name else []
    return {
        'first_name': parts[0] if parts else '',
        'last_name': parts[-1] if len(parts) > 1 else '',
        'email': extract(text, 'email'),
        'github': extract(text, 'github'),
        'linkedin': extract(text, 'linkedin'),
    }


def main() -> None:
    if not os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON'):
        print('Error: GOOGLE_SERVICE_ACCOUNT_JSON not set in .env')
        sys.exit(1)
    folder_id = os.environ.get('DRIVE_FOLDER_ID')
    if not folder_id:
        print('Error: DRIVE_FOLDER_ID not set in .env')
        sys.exit(1)

    service = get_drive_service()
    docs = list_docs(service, folder_id)
    print(f'Found {len(docs)} docs in Drive folder.')

    rows: list[dict] = []
    warnings: list[tuple[str, list[str]]] = []

    for doc in docs:
        try:
            text = fetch_doc_text(service, doc['id'])
        except Exception as exc:
            warnings.append((doc['name'], [f'fetch failed: {exc}']))
            rows.append({
                'first_name': '', 'last_name': '', 'email': '',
                'github': '', 'linkedin': '', 'file_id': doc['id'],
            })
            continue

        fields = parse_doc(text)
        missing = [k for k in ('first_name', 'email') if not fields[k]]
        if missing:
            warnings.append((doc['name'], missing))
        rows.append({**fields, 'file_id': doc['id']})

    with open('attendees.csv', 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    print(f'Wrote {len(rows)} rows to attendees.csv.')
    if warnings:
        print('\nWarnings — review attendees.csv manually:')
        for name, missing in warnings:
            print(f'  {name}: missing {missing}')


if __name__ == '__main__':
    main()
