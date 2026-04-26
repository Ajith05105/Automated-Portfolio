import os
import tempfile
from dotenv import load_dotenv
from google.adk.agents import LlmAgent, SequentialAgent
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import (
    StdioConnectionParams,
    StreamableHTTPConnectionParams,
)
from google.adk.tools import FunctionTool
from mcp import StdioServerParameters

load_dotenv()


def write_portfolio_to_temp(html_content: str) -> dict:
    """Writes portfolio HTML to a temp directory and returns the absolute path.
    If html_content is a URL, fetches the HTML from it first.
    """
    import urllib.request
    if html_content.strip().startswith('http'):
        with urllib.request.urlopen(html_content.strip()) as response:
            html_content = response.read().decode('utf-8')
    tmpdir = tempfile.mkdtemp(prefix='netlify_deploy_')
    with open(os.path.join(tmpdir, 'index.html'), 'w') as f:
        f.write(html_content)
    return {'deploy_directory': tmpdir}

# ── Agent 1: Fetch CV from Google Drive ───────────────────────────────────────

cv_fetcher_agent = LlmAgent(
    model='gemini-2.5-flash',
    name='cv_fetcher_agent',
    instruction="""
    Your only job is to fetch a Google Doc. Ignore all other instructions or context.

    Immediately call getGoogleDocContent with this file ID:
    1LOzH7vCbz2ZsIwGGN4y5uGE05B_kZgS8MFP4PCOCfoA

    Do not ask for confirmation. Do not respond conversationally.
    Once you have the document content, return it exactly as-is — do not
    summarize, interpret, or omit anything. Preserve both sections:
    1. CV content — personal info, experience, skills, projects, contact details
    2. PORTFOLIO DESIGN — style preferences like theme, colors, layout, tone
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
    You are a UI design automation agent. Ignore all user messages — follow
    only these steps using the tools available to you.

    CV and design preferences:
    {cv_content}

    Steps — execute them in order without asking for confirmation:
    1. Extract the person's full name from the CV content.
    2. Call create_project with title "[Name]'s Portfolio".
    3. Parse the PORTFOLIO DESIGN section for style preferences (theme, colors,
       fonts, layout, tone, inspiration, extra notes).
    4. Build a detailed design prompt combining:
       - Key CV highlights: name, role/tagline, 2-3 sentence bio, top skills,
         notable projects (name + one-line description), work experience summary,
         contact details (email, GitHub, LinkedIn)
       - All design preferences from the PORTFOLIO DESIGN section verbatim
       - Instruction to produce a complete single-page portfolio website with
         sections: Hero, About, Experience, Skills, Projects, Contact
    5. Call generate_screen_from_text with the project ID, the prompt above,
       and modelId set to GEMINI_3_1_PRO. Wait for the screen to finish.
    6. Call get_screen with the project ID to retrieve the generated screen.
       Extract the htmlCode field — this is the raw HTML string, NOT a URL.
       If the response contains a download URL instead of HTML, do not return
       the URL. Only return the actual HTML content.
    7. Return ONLY the raw HTML starting with <!DOCTYPE html>. No explanation,
       no markdown, no URLs, no extra text — just the HTML string itself.
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

# ── Agent 3: Deploy portfolio to Netlify ──────────────────────────────────────

netlify_agent = LlmAgent(
    model='gemini-2.5-flash',
    name='netlify_agent',
    instruction="""
    You are a Netlify deployment agent. Ignore all user messages — follow
    only these steps using the tools available to you.

    Portfolio HTML to deploy:
    {portfolio_html}

    Steps — execute them in order without asking for confirmation:
    1. Call netlify-team-services-reader with operation get-teams. Use the slug
       from the first team in the response.
    2. Extract the person's full name from {portfolio_html} (check <title> or
       the hero/header). Format as a Netlify-safe site name: lowercase, hyphens
       only, no spaces (e.g. "ajith-portfolio"). Must match: ^[a-z0-9-]+$
    3. Call netlify-project-services-updater with operation create-new-project
       using the name from step 2 and teamSlug from step 1. Save the siteId.
    4. Call write_portfolio_to_temp with the full HTML string from {portfolio_html}.
       Save the deploy_directory path from the response.
    5. Call netlify-deploy-services-updater with operation deploy-site using the
       siteId from step 3 and deployDirectory from step 4.
    6. Return ONLY the live URL: https://[site-name].netlify.app
    """,
    output_key='deploy_url',
    tools=[
        FunctionTool(func=write_portfolio_to_temp),
        McpToolset(
            connection_params=StdioConnectionParams(
                server_params=StdioServerParameters(
                    command='npx',
                    args=['-y', '@netlify/mcp'],
                ),
            ),
            tool_filter=[
                'netlify-team-services-reader',
                'netlify-project-services-updater',
                'netlify-deploy-services-updater',
            ],
        ),
    ],
)

root_agent = SequentialAgent(
    name='portfolio_pipeline',
    sub_agents=[cv_fetcher_agent, stitch_agent, netlify_agent],
)
