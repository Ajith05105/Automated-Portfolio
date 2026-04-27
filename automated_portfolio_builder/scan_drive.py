"""Scans a Google Drive folder and returns attendee rows for the pipeline.

Runs a one-shot ADK agent that calls listFolder then getGoogleDocContent on
each file, extracting Name and Contact Email from the CV template.
Reuses MCP credentials from: npx @piotr-agier/google-drive-mcp auth
"""

import json
import os
import re

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from google.genai import types
from mcp import StdioServerParameters

APP_NAME = 'drive-scanner'

_scanner_agent = LlmAgent(
    model='gemini-2.5-flash',
    name='drive_scanner_agent',
    instruction="""
    You are a Drive folder scanner. Do not respond conversationally.
    Execute these steps immediately without asking for confirmation.

    1. Call listFolder with folder_id={folder_id}. This returns a list of
       files — each has an id and a name. Save every file's id.
    2. For each file id from step 1, call getGoogleDocContent with that id.
    3. From each doc extract:
       - first_name: first word of the "Name:" field
       - last_name: last word of the "Name:" field
       - email: value of the "Contact Email:" field
       - file_id: the file's id from step 1
    4. Return ONLY a raw JSON array — no markdown, no explanation:
       [
         {{"first_name": "Alex", "last_name": "Chen", "email": "alex@example.com", "file_id": "abc123"}},
         ...
       ]
    """,
    output_key='scan_result',
    tools=[
        McpToolset(
            connection_params=StdioConnectionParams(
                server_params=StdioServerParameters(
                    command='npx',
                    args=['-y', '@piotr-agier/google-drive-mcp'],
                ),
            ),
            tool_filter=['listFolder', 'getGoogleDocContent'],
        )
    ],
)


async def scan_folder(folder_id: str) -> list[dict]:
    """Scans a Drive folder and returns a list of attendee dicts."""
    session_service = InMemorySessionService()
    runner = Runner(
        agent=_scanner_agent,
        app_name=APP_NAME,
        session_service=session_service,
    )

    await session_service.create_session(
        app_name=APP_NAME,
        user_id='scanner',
        session_id='scan-session',
        state={'folder_id': folder_id},
    )

    trigger = types.Content(role='user', parts=[types.Part(text='run')])
    async for _ in runner.run_async(
        user_id='scanner',
        session_id='scan-session',
        new_message=trigger,
    ):
        pass

    session = await session_service.get_session(
        app_name=APP_NAME, user_id='scanner', session_id='scan-session'
    )
    raw = (session.state if session else {}).get('scan_result', '[]')

    raw = re.sub(r'^```(?:json)?\s*', '', raw.strip(), flags=re.IGNORECASE)
    raw = re.sub(r'\s*```$', '', raw)

    return json.loads(raw)
