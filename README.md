# Automated Portfolio Builder

An AI-powered pipeline that reads a CV from Google Drive and generates a 
deployed portfolio website using Google ADK, Gemini, and Firebase.

## How It Works

1. Agent 1 fetches CV content from a Google Doc via Google Drive MCP
2. Agent 2 generates a styled HTML portfolio using Gemini
3. Agent 3 deploys the site to Firebase Hosting and returns a live URL

## Prerequisites

- Python 3.11+
- Node.js 18+
- Git

## Setup


### 1. Create a virtual environment

python -m venv .venv
source .venv/bin/activate

### 2. Install dependencies

pip install -r requirements.txt

### 3. Configure environment variables and google cloud

# Install gcloud
brew install google-cloud-sdk

# Login with the email added to google cloud
gcloud auth application-default login 

# Set quota project
gcloud auth application-default set-quota-project portfolio-builder-494211

# configure env 
cp .env.example .env





### 4. Set up Google Drive MCP

Get the `gcp-oauth.keys.json` file from Ajith and place it at:

~/.config/google-drive-mcp/gcp-oauth.keys.json

Then authenticate:

npx @piotr-agier/google-drive-mcp auth

This opens your browser — sign in with your Google account.

### 5. Run the agent


In Terminal 1— start the ADK web interface:

adk web

Then open your browser at http://localhost:8000

## Project Structure

Automated-Portfolio/
├── agent.py          # Agent definitions
├── tools.py          # Custom tools (exit_loop, firebase deploy)
├── .env              # Environment variables (not committed)
├── .env.example      # Environment variable template
├── requirements.txt  # Python dependencies
└── README.md

## CV Template

Attendees should fill out the Google Doc template with the following fields:

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

## Team

Built by GDGC Auckland for the Tap to Deploy workshop.