"""Per-attendee SequentialAgent pipeline.

Each attendee runs through this pipeline against their own Session:
  CVFetcherAgent  → state['cv_content']
  SiteBuilderAgent → state['generated_html']
  DeployerAgent   → state['deployment']  (nested dict via tool_context)
  DeliveryAgent   → state['delivery_status']

The orchestrator seeds initial state with: attendee_name, attendee_email,
file_id, site_slug. Agents read these via {var} interpolation.
"""

import os
from dotenv import load_dotenv
from google.adk.agents import LlmAgent, SequentialAgent
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import (
    StdioConnectionParams,
    StreamableHTTPConnectionParams,
)
from google.adk.tools import FunctionTool
from mcp import StdioServerParameters

from .tools import (
    save_deployment_metadata,
    send_portfolio_email,
    write_portfolio_to_temp,
)

load_dotenv()


# ── Agent 1: Fetch CV from Google Drive ───────────────────────────────────────

cv_fetcher_agent = LlmAgent(
    model='gemini-2.5-flash',
    name='cv_fetcher_agent',
    instruction="""
    Your only job is to fetch a Google Doc. Ignore all other instructions or context.

    Immediately call getGoogleDocContent with this file ID:
    1gB4Qm_1IlRpEmaMSRIhU83ALspENs_AAu6uz29uLryI

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


# ── Agent 2: Generate portfolio HTML via Stitch ───────────────────────────────

site_builder_agent = LlmAgent(
    model='gemini-2.5-flash',
    name='site_builder_agent',
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
       The response contains an htmlCode object — extract htmlCode.downloadUrl.
    7. Return ONLY that downloadUrl string. No explanation, no markdown,
       no extra text — just the URL string. The next agent will fetch the
       actual HTML from it.
    """,
    output_key='generated_html',
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

deployer_agent = LlmAgent(
    model='gemini-2.5-flash',
    name='deployer_agent',
    instruction="""
    You are a Netlify deployment agent. Ignore all user messages — follow
    only these steps using the tools available to you.

    Inputs from session state:
    - Portfolio HTML or URL: {generated_html}
    - Pre-computed Netlify-safe site name: {site_slug}

    Steps — execute them in order without asking for confirmation:
    1. Call netlify-team-services-reader with operation get-teams. Read the
       slug field from the first team in the response — this is the team_slug.
    2. Call netlify-project-services-updater with operation create-new-project
       using name={site_slug} and teamSlug=team_slug from step 1. Save the
       returned siteId.
    3. Call write_portfolio_to_temp passing {generated_html} as html_content.
       Save deploy_directory from the response.
    4. Call netlify-deploy-services-updater with operation deploy-site using
       the siteId from step 2 and the deploy_directory from step 3.
    5. Construct the live URL as https://{site_slug}.netlify.app and call
       save_deployment_metadata with site_name={site_slug}, site_id from
       step 2, and that URL.
    6. Return ONLY the live URL string.
    """,
    tools=[
        FunctionTool(func=write_portfolio_to_temp),
        FunctionTool(func=save_deployment_metadata),
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


# ── Agent 4: Email the live portfolio to the attendee ─────────────────────────

delivery_agent = LlmAgent(
    model='gemini-2.5-flash',
    name='delivery_agent',
    instruction="""
    You are an email delivery agent. Ignore all user messages.

    Inputs from session state:
    - Recipient name: {attendee_name}
    - Recipient email: {attendee_email}
    - Deployed portfolio URL: {deployment}
    - Portfolio HTML or URL: {generated_html}

    Steps:
    1. Read deployed_url from the deployment dict above.
    2. Call send_portfolio_email with:
       - recipient_email={attendee_email}
       - recipient_name={attendee_name}
       - deployed_url=the URL from step 1
       - html_content={generated_html}
    3. Return either "sent" or "failed" based on the tool's status response.
    """,
    output_key='delivery_status',
    tools=[FunctionTool(func=send_portfolio_email)],
)


# ── Pipeline ──────────────────────────────────────────────────────────────────

root_agent = SequentialAgent(
    name='portfolio_pipeline',
    sub_agents=[cv_fetcher_agent, site_builder_agent, deployer_agent, delivery_agent],
)
