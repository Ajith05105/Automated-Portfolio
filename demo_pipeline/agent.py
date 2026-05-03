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
from google.genai import types

from demo_pipeline.tools import send_portfolio_email, write_portfolio_to_temp, save_cv_structured

load_dotenv()


# ── Agent 1: Fetch CV from Google Drive ───────────────────────────────────────

_cv_doc_id = os.environ.get('CV_GOOGLE_DOC_ID', '')

cv_fetcher_agent = LlmAgent(
    model='gemini-2.5-flash',
    name='cv_fetcher_agent',
    generate_content_config=types.GenerateContentConfig(
        temperature=0.1,
    ),
    instruction=f"""
    You are a CV fetcher and parser. You have access to exactly these tools:
    getGoogleDocContent, save_cv_structured.
    Do NOT call any other tool. Do NOT call deploy_to_netlify or any Netlify tool.

    Follow these steps exactly in order, then STOP:

    1. Call getGoogleDocContent with file ID: {_cv_doc_id}
    2. Parse the returned document content into structured fields.
    3. Call save_cv_structured with the parsed fields to store the CV in session state.
    4. After save_cv_structured returns successfully, respond with a single short
       confirmation sentence like "CV for [name] parsed and saved." Then STOP.

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
    - Your job ends after the confirmation sentence. Do not proceed further.
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

    STRICT RULES — NO HALLUCINATION, NO INVENTED LINKS:
    1. Only use the data in cv_content from session state. If a field is empty
       or missing, omit that section entirely.
    2. Do NOT invent names, job titles, companies, skills, projects, or any
       other text content.
    3. Do NOT use placeholder text like "Your Name", "Company Name", or
       "example.com".
    4. NEVER create any links other than:
       - <a href="mailto:cv_content.email"> (only if cv_content.email is present)
       - <a href="cv_content.github"> (only if cv_content.github is present)
       - <a href="cv_content.linkedin"> (only if cv_content.linkedin is present)
       - Project URLs from cv_content.projects (only if a project has a non-empty url)
    5. For projects: only add an <a href="..."> tag if the project entry has a
       non-empty "url" field. If a project has no URL, render it as text only —
       DO NOT link it to GitHub search, "#", "javascript:void(0)", or any made-up URL.
    6. Footer must contain ONLY the email/GitHub/LinkedIn links above —
       no Twitter, Instagram, Dribbble, Medium, Dev.to, or any other social accounts.

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
       - Projects: name + description as cards. Only wrap a card in <a href="...">
         if that project's url field is non-empty. Never invent project URLs.
       - Experience: company, role, duration, description as cards
       - Education: institution, degree, duration as a clean section
       - Contact: ONLY email/GitHub/LinkedIn from cv_content as icon links in the
         footer. Never add Twitter, Instagram, Dribbble, Medium, or any other
         social account that is not in cv_content.

       Produce a COMPLETE single-page HTML file with all CSS inlined in <style>.
       No external CSS frameworks. No placeholder text. Use only real content
       from the structured CV data.

    4. Call generate_screen_from_text with the project ID, the prompt above,
       and modelId set to GEMINI_3_1_PRO. Wait for the screen to finish.

    5. Call get_screen with the project ID. Inspect the response carefully:
       - If htmlCode field contains HTML starting with <!DOCTYPE — return that
         string directly
       - Otherwise, find the URL from contribution.usercontent.google.com and
         return that URL exactly as-is
       - NEVER return any URL from lh3.googleusercontent.com — that is a PNG
         image preview, not HTML

    6. Return ONLY one of the following — no explanation, no markdown, no extra text:
       - The raw HTML string starting with <!DOCTYPE html>, OR
       - The contribution.usercontent.google.com download URL
       The next step will fetch the URL automatically if needed.
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

    4. Call the write_portfolio_to_temp tool directly. Pass the value of
       portfolio_html from session state as the html_content argument.
       Do NOT write Python code — invoke the tool as a function call.
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
