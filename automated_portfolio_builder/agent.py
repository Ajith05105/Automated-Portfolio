from google.adk.agents import LlmAgent, SequentialAgent
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from mcp import StdioServerParameters

# Agent 1 — Fetch CV from Drive
cv_fetcher_agent = LlmAgent(
    model='gemini-2.5-flash',
    name='cv_fetcher_agent',
    instruction="""
    You are a Google Drive content fetcher.
    Use your tools to read the Google Doc with this file ID: 1LOzH7vCbz2ZsIwGGN4y5uGE05B_kZgS8MFP4PCOCfoA
    Return only the raw text content of the document.
    """,
    output_key="cv_content",
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
            ]
        )
    ],
)

# Agent 2 — Generate HTML portfolio
site_builder_agent = LlmAgent(
    model='gemini-2.5-flash',
    name='site_builder_agent',
    instruction="""
        You are a portfolio website generator.
        Using the following CV content:
        {cv_content}

        Generate a COMPLETE single file HTML portfolio website. 
        The file must include:
        - <!DOCTYPE html>
        - <html> tag
        - <head> tag with:
            - <meta charset="UTF-8">
            - <meta name="viewport" content="width=device-width, initial-scale=1.0">
            - <title> tag with the person's name
            - Tailwind CSS CDN: <script src="https://cdn.tailwindcss.com"></script>
            - Google Fonts link tag
        - <body> tag with all content

        Return only raw HTML starting with <!DOCTYPE html>. 
        No markdown, no code fences, no explanation.
        """,
    output_key="generated_html",
)

# Sequential pipeline
root_agent = SequentialAgent(
    name="portfolio_pipeline",
    sub_agents=[cv_fetcher_agent, site_builder_agent],
)