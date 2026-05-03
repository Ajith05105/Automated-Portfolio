# Automated Portfolio Builder

An AI-powered pipeline that reads a CV from Google Drive and automatically generates and deploys a personal portfolio website.

Built with [Google ADK](https://google.github.io/adk-docs/), Gemini 2.5 Flash, [Google Stitch](https://stitch.withgoogle.com), and [Netlify](https://www.netlify.com).

---

## How It Works

The pipeline runs four agents in sequence:

1. **CV Fetcher** — reads a Google Doc CV via the Google Drive MCP and saves structured data to session state
2. **Stitch Designer** — generates a complete single-page HTML portfolio using Google Stitch
3. **Netlify Deployer** — deploys the HTML to a live Netlify site
4. **Email Delivery** — sends the live URL to the candidate via Gmail

---

## Prerequisites

- Python 3.11+
- Node.js 18+
- A Google Cloud project
- A [Google Stitch](https://stitch.withgoogle.com) API key
- A Netlify account
- A Gmail account with **2-Step Verification enabled** and an [App Password](https://myaccount.google.com/apppasswords) generated

---

## Setup

### 1. Clone and create a virtual environment

```bash
git clone https://github.com/your-org/automated-portfolio
cd automated-portfolio
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure environment variables

```bash
cp .env.example .env
```

Fill in your values in `.env`:

| Variable | Description |
|---|---|
| `GOOGLE_GENAI_USE_VERTEXAI` | Set to `1` to use Vertex AI |
| `GOOGLE_CLOUD_PROJECT` | Your GCP project ID |
| `GOOGLE_CLOUD_LOCATION` | GCP region (e.g. `us-central1`) |
| `STITCH_API_KEY` | API key from [stitch.withgoogle.com/settings](https://stitch.withgoogle.com/settings) |
| `CV_GOOGLE_DOC_ID` | File ID of the Google Doc CV (from its URL) |
| `GMAIL_USER` | Gmail address used to send emails |
| `GMAIL_APP_PASSWORD` | 16-character Gmail App Password (requires 2FA enabled) |

### 3. Authenticate with Google Cloud and enable required APIs

```bash
brew install google-cloud-sdk          # macOS; see gcloud docs for other platforms
gcloud auth application-default login
gcloud auth application-default set-quota-project YOUR_PROJECT_ID

# Enable the Stitch API
gcloud beta services mcp enable stitch.googleapis.com --project="YOUR_PROJECT_ID"

# Grant your account permission to consume GCP services
USER_EMAIL=$(gcloud config get-value account)
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
    --member="user:$USER_EMAIL" \
    --role="roles/serviceusage.serviceUsageConsumer" \
    --condition=None
```

### 4. Set up the Google Drive MCP

The pipeline reads CVs from Google Docs using the [`@piotr-agier/google-drive-mcp`](https://github.com/piotr-agier/google-drive-mcp) server.

Follow the **Google Cloud Setup** steps in that repo's README to enable the required APIs, configure the OAuth consent screen, and create OAuth credentials. If you already have a Google Cloud project, you can skip step 1 and start from **step 2 (Enable Required APIs)**.

Once you have your `gcp-oauth.keys.json`, place it at `~/.config/google-drive-mcp/gcp-oauth.keys.json`, then run:

```bash
npx @piotr-agier/google-drive-mcp auth
```

This opens a browser for you to authorise access. Credentials are saved locally and reused on subsequent runs.

### 5. Authenticate with Netlify

```bash
npx netlify-cli login
```

This saves a token locally that the pipeline reads automatically.

---

## Running the Demo Pipeline

The demo pipeline runs in the ADK web UI and is pre-configured to read a single CV from a hardcoded Google Doc ID.

```bash
adk web
```

Open [http://localhost:8000](http://localhost:8000) and type `run` to start the pipeline.

To use a different CV, set `CV_GOOGLE_DOC_ID` in your `.env` to the file ID from the Google Doc URL.

---

## Project Structure

```
automated-portfolio/
├── demo_pipeline/
│   ├── __init__.py
│   ├── agent.py        # Agent definitions (4 LlmAgents + SequentialAgent)
│   └── tools.py        # FunctionTools: save_cv_structured, write_portfolio_to_temp, send_portfolio_email
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

---

## CV Format

The Google Doc CV should contain the following fields (plain text, one per line):

```
Name:
Email:
Phone:
Summary:
Skills:
Experience (company, role, duration, description per entry):
Education (institution, degree, duration per entry):
Projects (name, description, URL per entry):
GitHub:
LinkedIn:
```

The CV Fetcher agent parses this into structured data — no special formatting required.

---

## Contributing

Pull requests are welcome. For major changes, open an issue first to discuss what you'd like to change.

---

