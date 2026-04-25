import os
from dotenv import load_dotenv
from google.adk.agents import LlmAgent, SequentialAgent
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import (
    StdioConnectionParams,
    StreamableHTTPConnectionParams,
)
from mcp import StdioServerParameters

load_dotenv()

# ── Agent 1: Fetch CV from Google Drive ───────────────────────────────────────

cv_fetcher_agent = LlmAgent(
    model='gemini-2.5-flash',
    name='cv_fetcher_agent',
    instruction="""
    You are a Google Drive content fetcher.
    Google Doc File ID: 1r65N1IzrjW6vy0bH4-iGmu2UGL7d8enjfeUUqB4eznM
    The document contains two sections:
    1. CV content — personal info, experience, skills, projects, contact details
    2. A PORTFOLIO DESIGN section — style preferences like theme, colors, layout, tone

    Return the entire document as-is, preserving both sections.
    Do not summarize, interpret, or omit anything.
    """,
    output_key='cv_content',
    tools=[
        McpToolset(
            connection_params=StdioConnectionParams(
                server_params=StdioServerParameters(
                    command='npx',
                    args=['-y', '@piotr-agier/google-drive-mcp'],
                ),
            ),
            tool_filter=[
                'readGoogleDoc',
                'getGoogleDocContent',
                'listFolder',
                'getDocumentInfo',
            ],
        )
    ],
)

# ── Agent 2: Create Stitch project and generate portfolio screens ──────────────

stitch_agent = LlmAgent(
    model='gemini-2.5-flash',
    name='stitch_agent',
    instruction="""
    You are a UI design automation agent using Google Stitch.

    You have access to the full CV and design preferences here:
    {cv_content}

    Your job:
    1. Extract the person's full name from the CV content.
    2. Call create_project with the title "[Name]'s Portfolio".
    3. Parse the PORTFOLIO DESIGN section for style preferences (theme, colors,
       fonts, layout, tone, inspiration, extra notes).
    4. Build a detailed design prompt that combines:
       - Key CV highlights: name, role/tagline, 2-3 sentence bio, top skills,
         notable projects (name + one-line description), work experience summary,
         contact details (email, GitHub, LinkedIn)
       - All design preferences from the PORTFOLIO DESIGN section verbatim
       - Instruction to produce a complete single-page portfolio website with
         sections: Hero, About, Experience, Skills, Projects, Contact
    5. Call generate_screen_from_text with the project ID, the prompt, and
       modelId set to GEMINI_3_1_PRO. Wait for the screen to be generated.
    6. Call get_screen to retrieve the generated screen and extract its htmlCode.
    7. Return ONLY the raw HTML content starting with <!DOCTYPE html>, with no
       additional text, comments, or explanation.
    """,
    output_key='portfolio_html',
    tools=[
        McpToolset(
            connection_params=StreamableHTTPConnectionParams(
                url='https://stitch.googleapis.com/mcp',
                headers={'X-Goog-Api-Key': os.environ.get('STITCH_API_KEY', '')},
            ),
            tool_filter=[
                'create_project',
                'generate_screen_from_text',
                'get_screen',
            ],
        )
    ],
)

root_agent = SequentialAgent(
    name='portfolio_pipeline',
    sub_agents=[cv_fetcher_agent, stitch_agent],
)
