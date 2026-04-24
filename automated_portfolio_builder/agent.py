from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from mcp import StdioServerParameters

root_agent = LlmAgent(
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