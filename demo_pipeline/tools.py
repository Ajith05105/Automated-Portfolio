import os
import smtplib
import tempfile
from email.message import EmailMessage
from google.adk.tools.tool_context import ToolContext


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

    If html_content is a contribution.usercontent.google.com URL, fetches the
    HTML from it first. Rejects lh3.googleusercontent.com (PNG image preview).
    """
    import urllib.request

    html_content = html_content.strip()

    if 'lh3.googleusercontent.com' in html_content:
        return {
            'error': 'Received an image preview URL (lh3.googleusercontent.com). '
                     'This is a PNG, not HTML. Use the contribution.usercontent.google.com URL instead.'
        }

    if html_content.startswith('http'):
        req = urllib.request.Request(html_content, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            content_type = response.headers.get('Content-Type', '')
            if 'text/html' not in content_type and 'text/plain' not in content_type:
                return {
                    'error': f'URL did not return HTML (Content-Type: {content_type}). '
                             'Pass the contribution.usercontent.google.com download URL.'
                }
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
