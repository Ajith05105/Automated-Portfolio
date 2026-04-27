# Automated Portfolio Builder

An AI-powered pipeline that reads attendee CVs from Google Drive, generates
styled portfolio websites with Google Stitch, deploys them to Netlify, and
emails the live URL back to each attendee — all in parallel batches via
Google ADK.

## How It Works

For each attendee in the input CSV, a 4-stage `SequentialAgent` pipeline runs:

1. **CVFetcherAgent** — pulls CV content from a Google Doc via Google Drive MCP
2. **SiteBuilderAgent** — generates portfolio HTML via Google Stitch MCP
3. **DeployerAgent** — creates a Netlify site and deploys via Netlify MCP
4. **DeliveryAgent** — emails the live URL + attached HTML to the attendee

Pipelines are batched (5 at a time, configurable) and run concurrently
within each batch via `asyncio.gather`. Each batch's results are appended
to a Google Sheet for tracking.

## Prerequisites

- Python 3.11+
- Node.js 18+
- Git

## Setup

### 1. Create a virtual environment

```
python -m venv .venv
source .venv/bin/activate
```

### 2. Install dependencies

```
pip install -r requirements.txt
```

## Configure environment variables and Google Cloud

### 1. Install gcloud
```
brew install google-cloud-sdk
```

### 2. Login with the email added to Google Cloud
```
gcloud auth application-default login
```

### 3. Set quota project
```
gcloud auth application-default set-quota-project portfolio-builder-494211
```

### 4. Configure env
```
cp .env.example .env
```
Then fill in the values — see the sections below for what each one needs.

---

## Set up Google Stitch MCP

### 1. Get a Stitch API key
Go to [stitch.withgoogle.com/settings](https://stitch.withgoogle.com/settings) → create an API key.

### 2. Add it to your .env
```
STITCH_API_KEY=your_api_key_here
```
No CLI login needed — the key is passed directly in the MCP request headers.

---

## Set up Netlify MCP

### 1. Authenticate
Run this in your terminal:
```
npx netlify-cli login
```
This opens your browser — sign in with the club Netlify account. The token is stored locally at `~/.netlify/config.json` and is used automatically by the MCP on every run.

---

## Set up Google Drive MCP

### 1. Set up OAuth
Get the `gcp-oauth.keys.json` file from Ajith and place it at:
```
~/.config/google-drive-mcp/gcp-oauth.keys.json
```

### 2. Authenticate (use the club Google account)
```
npx @piotr-agier/google-drive-mcp auth
```
This opens your browser — sign in with the same Google account that owns the Drive folder where attendees upload their CVs.

---

## Set up Gmail delivery

The delivery agent uses Gmail SMTP with an app password (cleaner than wiring up another MCP for a one-off send).

### 1. Enable 2FA on the club Gmail account
Required to generate an app password.

### 2. Generate an app password
Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) → create a password for "Mail".

### 3. Add to .env
```
GMAIL_USER=club-account@gmail.com
GMAIL_APP_PASSWORD=your-16-char-app-password
```

---

## Set up the results tracking sheet

### 1. Create a Google Sheet
Add a header row in the first tab:
```
timestamp | attendee_name | attendee_email | site_name | deployed_url | delivery_status | error
```

### 2. Share it with the club account
Use the same account you authenticated `gcloud` with — the orchestrator writes to the sheet via Application Default Credentials.

### 3. Copy the sheet ID into .env
The ID is the part of the URL between `/d/` and `/edit`:
```
RESULTS_SHEET_ID=1AbCdEf...
```

---

## Run the pipeline

### 1. Prepare the attendees CSV
Edit `attendees.csv` (or pass a custom path). Required columns:
```
first_name,last_name,email,file_id
```
The `file_id` is the Google Doc ID for that attendee's CV. Each doc must be readable by the club Google account.

### 2. Run the orchestrator
```
python -m automated_portfolio_builder.orchestrator
```
Or with a custom CSV:
```
python -m automated_portfolio_builder.orchestrator path/to/attendees.csv
```

### Single-attendee dev mode
If you just want to test against one attendee using the ADK web UI:
```
adk web
```
Then open `http://localhost:8000`. You'll need to manually seed session state with `attendee_name`, `attendee_email`, `file_id`, and `site_slug` for the agents' `{var}` references to resolve.

---

## Project Structure

```
AUTOMATED-PORTFOLIO/
├── automated_portfolio_builder/
│   ├── __init__.py
│   ├── agent.py           # The 4 LlmAgents + SequentialAgent pipeline
│   ├── tools.py           # Shared FunctionTools (write_portfolio_to_temp, save_deployment_metadata, send_portfolio_email)
│   ├── orchestrator.py    # CSV loader, batched session runner, result aggregator
│   └── sheets.py          # Google Sheets results writer
├── attendees.csv          # Input: list of attendees + their CV file_ids
├── .env / .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

## State flow per attendee

```
Initial state (set by orchestrator):
  attendee_name, attendee_email, file_id, site_slug

CVFetcherAgent      writes  cv_content
SiteBuilderAgent    writes  generated_html
DeployerAgent       writes  deployment = {site_name, site_id, deployed_url, deployed_at}
DeliveryAgent       writes  delivery_status = "sent" | "failed"
```

After the pipeline finishes, the orchestrator reads the session and appends a row to the tracking sheet.

## Rate limiting

Default `BATCH_SIZE = 5` (in `orchestrator.py`). If you start hitting 429s on Vertex AI or Stitch, drop it to 3.

## CV Template

Each attendee's Google Doc should follow this template:

```
Name:
Title:
About:
Skills:
Project 1 Name:
Project 1 Description:
Project 1 Link:
Experience:
Contact Email:
LinkedIn:
GitHub:

Style: [minimal / bold / creative / corporate / playful]
Theme: [dark / light / auto]
Primary Color: [e.g. teal, purple, orange, or hex code]
Font Preference: [modern / classic / technical / none]
Layout: [single-page / multi-page]
Section Order: [e.g. Hero, About, Experience, Skills, Projects, Contact]
Tone: [professional / casual / creative]
Inspiration: [optional — e.g. "like stripe.com" or "clean and typographic"]
Extra Notes: [anything else — e.g. "no animations", "lots of whitespace", "bold headings"]
```

## Team

Built by GDGC Auckland for the NFC workshop.
