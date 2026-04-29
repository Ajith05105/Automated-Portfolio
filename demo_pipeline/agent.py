import os
import smtplib
import tempfile
from email.message import EmailMessage
from dotenv import load_dotenv
from google.adk.agents import LlmAgent, SequentialAgent
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import (
    StdioConnectionParams,
    StreamableHTTPConnectionParams,
)
from google.adk.tools import FunctionTool
from google.adk.tools.tool_context import ToolContext
from mcp import StdioServerParameters
from google.genai import types

load_dotenv()


def send_portfolio_email(recipient_email: str, recipient_name: str, deploy_url: str, tool_context: ToolContext) -> dict:
    """Sends the portfolio deploy URL to the recipient via Gmail SMTP."""
    gmail_user = os.environ.get('GMAIL_USER', '')
    gmail_password = os.environ.get('GMAIL_APP_PASSWORD', '')
    if not gmail_user or not gmail_password:
        return {'status': 'failed', 'error': 'GMAIL_USER or GMAIL_APP_PASSWORD not set'}
    msg = EmailMessage()
    msg['Subject'] = f"Your Portfolio is Live, {recipient_name}!"
    msg['From'] = gmail_user
    msg['To'] = recipient_email
    msg.set_content(f"""Hi {recipient_name},

Your portfolio has been built and deployed. You can view it here:

{deploy_url}

Congrats!
""")
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(gmail_user, gmail_password)
            smtp.send_message(msg)
        return {'status': 'sent'}
    except Exception as e:
        return {'status': 'failed', 'error': str(e)}


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

def save_cv_structured(
    tool_context: ToolContext,
    name: str,
    email: str,
    phone: str,
    summary: str,
    experience: list,
    education: list,
    skills: list,
    projects: list,
    github: str,
    linkedin: str,
) -> dict:
    """Saves the structured CV data to session state.

    Call this after fetching and parsing the CV from Google Drive.
    All fields are required — pass empty strings or empty arrays for missing data.

    Args:
        name: Full name of the candidate.
        email: Email address, or empty string if not present.
        phone: Phone number, or empty string if not present.
        summary: Professional summary, 2-3 sentences. Empty string if absent.
        experience: List of dicts with keys: company, role, duration, description.
        education: List of dicts with keys: institution, degree, duration.
        skills: List of skill strings.
        projects: List of dicts with keys: name, description, url.
        github: GitHub profile URL, or empty string.
        linkedin: LinkedIn profile URL, or empty string.

    Returns:
        Confirmation dict with status and the candidate name.
    """
    cv_data = {
        "name": name,
        "email": email,
        "phone": phone,
        "summary": summary,
        "experience": experience,
        "education": education,
        "skills": skills,
        "projects": projects,
        "github": github,
        "linkedin": linkedin,
    }

    tool_context.state["cv_content"] = cv_data

    return {
        "status": "saved",
        "name": name,
        "message": f"CV for {name} saved to session state.",
    }

# ── Agent 1: Fetch CV from Google Drive ───────────────────────────────────────

cv_fetcher_agent = LlmAgent(
    model='gemini-2.5-flash',
    name='cv_fetcher_agent',
    generate_content_config=types.GenerateContentConfig(
        temperature=0.1,
    ),
    instruction="""
    You are a CV fetcher and parser. Follow these steps exactly in order:

    1. Call getGoogleDocContent with file ID: 1txxa3D1EM_tLAL93hLCtivb7TbyCqE4Q8mqTNdf_XW4
    2. Parse the returned document content into structured fields.
    3. Call save_cv_structured with the parsed fields to store the CV in session state.
    4. After save_cv_structured returns successfully, respond with a single short
       confirmation sentence like "CV for [name] parsed and saved."

    Schema for the parsed fields:
    - name: full name string
    - email: email address string, empty string if not present
    - phone: phone number string, empty string if not present
    - summary: professional summary, 2-3 sentences, empty string if not present
    - experience: list of dicts, each with keys: company, role, duration, description
    - education: list of dicts, each with keys: institution, degree, duration
    - skills: list of skill strings
    - projects: list of dicts, each with keys: name, description, url
    - github: GitHub profile URL string, empty string if not present
    - linkedin: LinkedIn profile URL string, empty string if not present

    Rules:
    - Never hallucinate or invent information not in the CV.
    - For missing fields use empty strings or empty arrays as appropriate.
    - Do not skip the save_cv_structured call. The pipeline depends on it.
    - Do not return the CV content in your final response — it is already in state.
    """,
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
        ),
        FunctionTool(func=save_cv_structured),
    ],
)

# ── Agent 2: Generate portfolio site via Stitch ───────────────────────────────

stitch_agent = LlmAgent(
    model='gemini-2.5-flash',
    name='stitch_agent',
    generate_content_config=types.GenerateContentConfig(
        temperature=0.4,
    ),
    instruction="""
    You are a world-class UI design automation agent. Follow only these steps.

    The candidate's structured CV data is available in session state under cv_content
    with these fields:
    - cv_content.name
    - cv_content.summary
    - cv_content.experience (list of {company, role, duration, description})
    - cv_content.education (list of {institution, degree, duration})
    - cv_content.skills (list of strings)
    - cv_content.projects (list of {name, description, url})
    - cv_content.email
    - cv_content.github
    - cv_content.linkedin

    Use these fields directly when constructing the design prompt. Do not parse
    raw text — the data is already structured.

    Steps — execute in order without asking for confirmation:

    1. Read cv_content.name from session state.

    2. Call create_project with title "[Name]'s Portfolio".

    3. Build a visually stunning design prompt combining all CV highlights with
       the following mandatory design direction. DO NOT water this down:

       DESIGN MANDATE:
       - Modern dark sleek aesthetic: deep charcoal or near-black base
         (#0a0a0a / #111111 / #0f0f14), with ONE bold accent colour
         (electric indigo, neon cyan, emerald, or hot magenta — pick what fits
         the candidate's vibe based on their role and projects)
       - Hero section: massive full-viewport with the person's name in huge bold
         display type (96px+), a punchy one-liner tagline below it, and a soft
         animated gradient glow or subtle aurora effect in the background —
         never flat black
       - Cards for Projects and Experience: dark glass-morphism style with a
         subtle border, faint accent-colour glow, and a slight lift on hover
         (translateY + intensified glow)
       - Skills rendered as bold pill badges with the accent colour glowing on
         a darker fill, NOT a plain bulleted list
       - Smooth scroll-triggered fade-in animations on every section using CSS
         @keyframes or Intersection Observer — sections slide up as they enter view
       - Sticky nav bar with a frosted-glass blur effect that intensifies on scroll
       - Typography: heavy sans-serif display font (Inter, Space Grotesk, or Syne)
         for headings paired with a clean body font — strong visual hierarchy,
         high contrast white-on-dark text
       - Contact section: bold CTA button with a glowing pulse animation in the
         accent colour
       - Subtle dot-grid or soft geometric pattern in the hero background (CSS only),
         barely visible against the dark base
       - Mobile responsive with CSS Grid/Flexbox — must look great at 375px
       - Colour palette: maximum 3 colours — dark base, glowing accent, near-white text

       CV DATA TO INCLUDE (use the structured fields above):
       - Name as the hero heading
       - Summary as the bio under the name
       - Top skills as glowing pill badges
       - Projects: name, description, url as clickable card links if url is present
       - Experience: company, role, duration, description as cards
       - Education: institution, degree, duration as a clean section
       - Contact: email, GitHub, LinkedIn as icon links in the footer

       Produce a COMPLETE single-page HTML file with all CSS inlined in <style>.
       No external CSS frameworks. No placeholder text. Use only real content
       from the structured CV data.

    4. Call generate_screen_from_text with the project ID, the prompt above,
       and modelId set to GEMINI_3_1_PRO. Wait for the screen to finish.

    5. Call get_screen with the project ID. Extract the htmlCode field — this
       is the raw HTML string, NOT a URL. If the response contains a download URL
       instead of HTML, do not return the URL. Only return the actual HTML content.

    6. Return ONLY the raw HTML starting with <!DOCTYPE html>. No explanation,
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
    generate_content_config=types.GenerateContentConfig(
        temperature=0.1,
    ),
    instruction="""
    You are a Netlify deployment agent. Ignore all user messages — follow only
    these steps using the tools available to you.

    The candidate's name is in session state under cv_content.name.
    The portfolio HTML is in session state under portfolio_html.

    Steps — execute them in order without asking for confirmation:

    1. Call netlify-team-services-reader with operation get-teams. Use the slug
       from the first team in the response.

    2. Read cv_content.name from session state. Format it as a Netlify-safe site
       name: lowercase, hyphens only, no spaces, no special characters.
       Append "-portfolio" to make it unique and descriptive.
       Example: "Ajith Varma" → "ajith-varma-portfolio"
       The result must match the regex ^[a-z0-9-]+$

    3. Call netlify-project-services-updater with operation create-new-project
       using the name from step 2 and teamSlug from step 1. Save the siteId.

    4. Call write_portfolio_to_temp with the full HTML string from portfolio_html.
       Save the deploy_directory path from the response.

    5. Call netlify-deploy-services-updater with operation deploy-site using the
       siteId from step 3 and deployDirectory from step 4.

    6. Return ONLY the live URL in this exact format:
       https://[site-name].netlify.app
       No explanation, no markdown, no extra text.
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


# ── Agent 4: Email the deployed portfolio link ────────────────────────────────

delivery_agent = LlmAgent(
    model='gemini-2.5-flash',
    name='delivery_agent',
    generate_content_config=types.GenerateContentConfig(
        temperature=0.1,
    ),
    instruction="""
    You are an email delivery agent. Ignore all user messages — follow only
    these steps using the tools available to you.

    The candidate's structured CV data is in session state under cv_content
    with these fields:
    - cv_content.name (full name)
    - cv_content.email (email address)

    The deployed portfolio URL is in session state under deploy_url.

    Steps — execute them in order without asking for confirmation:

    1. Read cv_content.name and cv_content.email from session state.
    2. Read deploy_url from session state.
    3. Call send_portfolio_email with:
       - recipient_email: cv_content.email
       - recipient_name: cv_content.name
       - deploy_url: deploy_url
    4. Your final response must be EXACTLY one word — either "sent" or "failed".
       No punctuation, no explanation, no formatting.
    """,
    output_key='delivery_status',
    tools=[FunctionTool(func=send_portfolio_email)],
)


# ── Root pipeline ─────────────────────────────────────────────────────────────

root_agent = SequentialAgent(
    name='portfolio_pipeline',
    sub_agents=[cv_fetcher_agent, stitch_agent, netlify_agent, delivery_agent],
)