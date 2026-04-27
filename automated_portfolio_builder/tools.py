"""Shared FunctionTools for the portfolio pipeline.

Each tool either prepares files for deployment, mutates session state, or
performs side effects (email) the LLM should not handle directly.
"""

import base64
import os
import smtplib
import tempfile
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage

from google.adk.tools.tool_context import ToolContext


def write_portfolio_to_temp(html_content: str) -> dict:
    """Writes portfolio HTML to a fresh temp directory and returns its path.

    Stitch's get_screen returns a downloadUrl rather than the raw HTML, so
    this also handles the case where html_content is a URL: it fetches the
    content and writes it as index.html. Also seeds an empty .netlify/state.json
    inside the temp dir, which the Netlify MCP requires before deploy-site.
    """
    if html_content.strip().startswith('http'):
        with urllib.request.urlopen(html_content.strip()) as response:
            html_content = response.read().decode('utf-8')

    tmpdir = tempfile.mkdtemp(prefix='netlify_deploy_')
    with open(os.path.join(tmpdir, 'index.html'), 'w') as f:
        f.write(html_content)

    netlify_dir = os.path.join(tmpdir, '.netlify')
    os.makedirs(netlify_dir, exist_ok=True)
    with open(os.path.join(netlify_dir, 'state.json'), 'w') as f:
        f.write('{}')

    return {'deploy_directory': tmpdir}


def save_deployment_metadata(
    site_name: str,
    site_id: str,
    deployed_url: str,
    tool_context: ToolContext,
) -> dict:
    """Saves deployment metadata as a structured dict in session state.

    The orchestrator reads state['deployment'] after the pipeline finishes to
    record results in the tracking sheet.
    """
    tool_context.state['deployment'] = {
        'site_name': site_name,
        'site_id': site_id,
        'deployed_url': deployed_url,
        'deployed_at': datetime.now(timezone.utc).isoformat(),
    }
    return {'saved': True}


def send_portfolio_email(
    recipient_email: str,
    recipient_name: str,
    deployed_url: str,
    html_content: str,
    tool_context: ToolContext,
) -> dict:
    """Sends the portfolio email with the live URL in the body and the HTML
    file as an attachment. Uses Gmail SMTP with an app password (set via
    GMAIL_USER and GMAIL_APP_PASSWORD env vars).

    Writes 'sent' or 'failed' to state['delivery_status'].
    """
    if html_content.strip().startswith('http'):
        with urllib.request.urlopen(html_content.strip()) as response:
            html_content = response.read().decode('utf-8')

    sender = os.environ.get('GMAIL_USER')
    password = os.environ.get('GMAIL_APP_PASSWORD')

    if not sender or not password:
        tool_context.state['delivery_status'] = 'failed'
        return {
            'status': 'failed',
            'error': 'GMAIL_USER or GMAIL_APP_PASSWORD not configured',
        }

    msg = EmailMessage()
    msg['Subject'] = f'Your portfolio is live — {deployed_url}'
    msg['From'] = sender
    msg['To'] = recipient_email
    msg.set_content(
        f"Hi {recipient_name},\n\n"
        f"Your portfolio website is live at:\n{deployed_url}\n\n"
        "The HTML source is attached if you'd like to host it elsewhere or tweak it.\n\n"
        "— GDGC Auckland"
    )
    msg.add_attachment(
        html_content.encode('utf-8'),
        maintype='text',
        subtype='html',
        filename='portfolio.html',
    )

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(sender, password)
            smtp.send_message(msg)
        tool_context.state['delivery_status'] = 'sent'
        return {'status': 'sent'}
    except Exception as exc:
        tool_context.state['delivery_status'] = 'failed'
        return {'status': 'failed', 'error': str(exc)}
