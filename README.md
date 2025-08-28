# GC Agent (Gmail + Calendar LangGraph Assistant)

An automated assistant that triages Gmail messages and coordinates meetings via Google Calendar using a LangGraph workflow with OpenAI. It features a multi-agent architecture for handling different aspects of email processing:

- **Calendar Management**: Automatically detects meeting requests, books available slots, or suggests alternatives
- **Smart Triage**: Analyzes email urgency and content to prioritize responses
- **Policy-Aware**: Uses document retrieval to generate responses based on company policies
- **Multi-Agent Workflow**: Dedicated agents for calendar management, confirmation handling, triage, and drafting
- **Idempotent Processing**: Tracks processed emails to avoid duplicates

## Features

### Core Functionality

- **Multi-Agent Architecture**: Specialized agents for different tasks (Calendar, Confirmation, Triage, Drafting)
- **Policy-Aware Responses**: Semantic search over policy documents to inform responses
- **Smart Email Processing**: Automatically categorizes and processes different types of emails

### Calendar Management

- **Meeting Detection**: Identifies meeting requests in emails
- **Auto-Booking**: Books available time slots directly to Google Calendar
- **Smart Suggestions**: Proposes alternative times when requested slots are unavailable
- **Confirmation Handling**: Recognizes and processes meeting confirmations

### Email Processing

- **Urgency Classification**: LLM-powered classification of email urgency
- **Draft Generation**: Creates professional draft responses for urgent emails
- **No-Reply Handling**: Automatically processes and ignores no-reply emails
- **Duplicate Prevention**: Uses Gmail labels to track processed emails

### Integration & Notifications

- **Google Services**: Seamless integration with Gmail and Google Calendar
- **Slack Notifications**: Optional real-time updates on actions taken
- **Logging**: Structured JSON logging for monitoring and debugging

## Tech Stack

- **Python 3.10+** with LangGraph for workflow orchestration
- **OpenAI** for LLM and embeddings
- **Google APIs** for Gmail and Calendar integration
- **Key Dependencies**: `python-dateutil`, `requests`, `python-dotenv`, `google-api-python-client`

Key files:

- `main.py` – Full workflow, Gmail/Calendar helpers, and LangGraph nodes
- `policies/` – Directory containing policy documents for context-aware responses
- `requirements.txt` – Python dependencies
- `credentials.json` – Google OAuth client (you provide)
- `token.json` – Generated after the first OAuth flow
- `.env` – Environment variables (you provide)

## Prerequisites

### System Requirements

- Python 3.10 or higher
- Git (for version control)
- Google Cloud Platform account
- OpenAI API key

### Google Cloud Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project and enable Gmail & Calendar APIs
3. Configure OAuth consent screen with these scopes:
   - `.../auth/gmail.readonly`
   - `.../auth/gmail.compose`
   - `.../auth/gmail.modify`
   - `.../auth/calendar`
4. Create OAuth 2.0 Client ID (Desktop app) and download as `credentials.json`

### Environment Setup

1. Clone the repository:

   ```bash
   git clone https://github.com/yourusername/gc-agent.git
   cd gc-agent
   ```

2. Create and activate a virtual environment:

   ```bash
   # macOS/Linux
   python3 -m venv .venv
   source .venv/bin/activate

   # Windows
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   ```

3. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

4. Configure environment variables:
   Create a `.env` file in the project root with the following variables:

   ```env
   # Required
   OPENAI_API_KEY=your_openai_api_key_here

   # Optional: Slack Webhook for notifications
   ENABLE_SLACK=false
   SLACK_WEBHOOK_URL=your_slack_webhook_url

   # Optional: Customize model settings
   # LLM_MODEL=gpt-4-turbo
   # EMBEDDING_MODEL=text-embedding-3-small

   # Optional: Timezone (fallback if not detected from Google Calendar)
   # USER_TZ=America/New_York
   ```

5. First Run:
   - Make sure `credentials.json` is in the project root
   - Run the application:
     ```bash
     python main.py
     ```
   - A browser window will open for Google authentication
   - After authorization, a `token.json` file will be created
   - The application is now ready to process emails

## Configuration

Key configuration constants live in `main.py`:

- `SCOPES` – Gmail read/compose/modify + Calendar access
- `LLM_MODEL` – default `gpt-4-turbo` (can be changed)
- Timezone handling (automatically detected in this order):
  1. Uses the timezone from your Google Calendar settings
  2. Falls back to the `USER_TZ` environment variable if set (e.g., 'America/New_York')
  3. Defaults to 'Asia/Kathmandu' if neither is available

Other tweakables:

- In `get_emails(...)` you can pass `max_results` to control the Gmail API page size. The current entrypoint calls `get_emails(gmail_service)` without a limit, so it fetches a single page using the API's default page size (no pagination implemented).

## How It Works

The application uses a multi-agent architecture built with LangGraph, where specialized agents handle different aspects of email processing. The workflow is defined in `create_email_workflow()` and consists of the following agents:

### 1. Calendar Agent

- **Purpose**: Handle meeting-related emails
- **Key Functions**:
  - Skips `no-reply` senders automatically
  - Extracts date/time information using advanced NLP
  - Checks calendar availability in real-time
  - For meeting requests:
    - Books the slot if available
    - Suggests alternative times if slot is taken
  - Generates professional, policy-aware responses
  - Marks emails as processed

### 2. Confirmation Agent

- **Purpose**: Process meeting confirmations
- **Key Functions**:
  - Detects confirmation patterns in replies
  - Parses and validates meeting times
  - Creates calendar events for confirmed meetings
  - Sends confirmation emails with event details
  - Handles timezone conversion automatically

### 3. Triage Agent

- **Purpose**: Classify email urgency
- **Key Functions**:
  - Uses LLM to analyze email content
  - Classifies emails as `urgent` or `not urgent`
  - Considers sender, subject, and content
  - Skips already processed emails
  - Passes urgent emails to Draft Agent

### 4. Draft Agent

- **Purpose**: Generate draft responses
- **Key Functions**:
  - Creates professional, context-aware drafts
  - Incorporates policy context when relevant
  - Saves drafts in Gmail (doesn't send automatically)
  - Handles different response types based on urgency
  - Marks emails as processed

### Policy Integration

- **RAG Implementation**:
  - Semantic search over policy documents
  - Dynamic context injection into responses
  - Handles policy updates automatically
  - Caches embeddings for performance

### Workflow Flow

1. Email received → Calendar Agent
2. If meeting detected → Process meeting request
3. If confirmation detected → Confirmation Agent
4. If neither → Triage Agent
5. If urgent → Draft Agent
6. Mark as processed

### Error Handling & Logging

- Structured JSON logging
- Automatic retries for transient failures
- Detailed error messages
- Slack notifications for critical actions

## Running the project

From the project root, after activating your venv and setting up `.env` and `credentials.json`:

```bash
python main.py
```

## Policy Documents

The system uses a Retrieval-Augmented Generation (RAG) approach to incorporate policy knowledge into its responses. Policies are stored as Markdown files in the `policies/` directory and are automatically indexed when the application starts.

### Policy Format

Each policy document should be a Markdown (`.md`) file with the following structure:

```markdown
# Policy Title

## Overview

Brief description of the policy and when it applies

## Guidelines

- Key points or rules
- Specific instructions
- Examples of application

## Related Policies

- Links to other related policies (if applicable)
```

### Best Practices

1. **Be Specific**: Create focused policy documents for different topics
2. **Use Clear Headings**: Helps with semantic search
3. **Keep it Concise**: Aim for 1-2 pages per policy
4. **Use Examples**: Include examples of correct application
5. **Version Control**: Track changes using Git

### Adding a New Policy

1. Create a new `.md` file in the `policies/` directory
2. Follow the format above
3. The system will automatically index the new policy on next run

### Policy Indexing

- Automatic indexing on application start
- Supports embedding model customization via `EMBEDDING_MODEL` env var
- Caches embeddings for better performance
- Handles updates to policy files automatically

### Example Policy

Create a file named `meeting_scheduling.md` in the `policies/` directory:

```markdown
# Meeting Scheduling Policy

## Overview

Guidelines for scheduling and confirming meetings

## Guidelines

- Always confirm meeting times in the recipient's timezone
- Include Google Meet links for virtual meetings
- For external meetings, include:
  - Meeting purpose
  - Agenda (if available)
  - Expected duration
- Send calendar invites for all confirmed meetings

## Response Templates

- Time proposal: "I'm available on [date] at [time]. Would that work for you?"
- Confirmation: "I've scheduled our meeting for [date] at [time]. A calendar invite has been sent."
```

### Troubleshooting

- If policies aren't being detected:
  - Check file permissions
  - Ensure files have `.md` extension
  - Check application logs for indexing errors
  - Verify the `policies/` directory exists

On first run, complete the Google OAuth flow in your browser. The script will then:

- Fetch one page of unread emails (Gmail API default page size)
- Process each through the workflow
- Print actions to the console and notify Slack (if enabled)

## Testing and Examples

### Basic Testing

1. **Meeting Request**

   - Send an email: "Can we meet tomorrow at 2 PM?"
   - The system will check your calendar and either:
     - Book the meeting if available
     - Suggest alternative times if busy

2. **Meeting Confirmation**

   - Reply to a time suggestion: "Yes, 3 PM works for me"
   - The system will create a calendar event
   - You'll receive a confirmation email

3. **Urgent Email**
   - Send an email with "URGENT" in the subject
   - Check your Gmail drafts for a response

### Advanced Testing

#### Test Calendar Integration

```python
# Example to test calendar availability
from datetime import datetime, timedelta
from dateutil.tz import gettz

# Check next week's availability
start = datetime.now(gettz('America/New_York')) + timedelta(days=7)
end = start + timedelta(hours=1)
print(f"Testing availability from {start} to {end}")
```

#### Test Policy Retrieval

```python
# Example to test policy search
from main import retrieve_policy_context

# Search for relevant policies
policies = retrieve_policy_context("How should we handle meeting rescheduling?")
for i, policy in enumerate(policies[:3], 1):
    print(f"\nPolicy {i}:")
    print(policy['text'][:200] + "...")  # Show first 200 chars
```

## Permissions and Data Security

### Required Permissions

- **Gmail**: Read, compose, and modify emails
- **Google Calendar**: View and edit events
- **Local Storage**: Store OAuth tokens and embeddings cache

### Data Handling

- **Processed Emails**: Marked with `ai-processed` label
- **No Data Storage**: Emails are processed in memory
- **Local Cache**: Only ches policy embeddings for performance

## Troubleshooting Guide

### Common Issues

#### Authentication Problems

```bash
# Re-authenticate by removing token
rm token.json
python main.py  # Will prompt for re-authentication
```

#### Missing Dependencies

```bash
# Ensure all dependencies are installed
pip install -r requirements.txt

# If using a virtual environment
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
```

### Logs and Debugging

- Check console output for JSON-formatted logs
- Enable debug mode: `export LOG_LEVEL=DEBUG`
- Look for `error` or `warning` level messages

## License

MIT License. See the `LICENSE` file for details.
