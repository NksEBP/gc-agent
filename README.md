# GC Agent (Gmail + Calendar LangGraph Assistant)

An automated assistant that triages Gmail messages and coordinates meetings via Google Calendar using a LangGraph workflow and OpenAI. It can:

- Detect meeting requests and book calendar events (or suggest alternatives).
- Recognize meeting confirmations and send calendar invites.
- Analyze urgency and create short professional draft replies for urgent emails.
- Mark processed emails with a custom Gmail label to avoid re-processing.
- Optionally send Slack notifications when actions are taken.

## Features

- **Gmail triage**: Reads recent unread emails from `INBOX` and ignores `no-reply` senders.
- **Policy-aware responses**: Uses document retrieval to generate responses based on company policies
- **Datetime detection**: Parses dates/times from email content and checks calendar availability.
- **Auto-booking or alternatives**: Books a Calendar event if free; otherwise suggests next available time slots.
- **Confirmation handling**: Detects confirmation replies and finalizes the meeting.
- **Urgency analysis**: Uses an LLM to classify emails as `urgent` vs `not urgent`.
- **Draft generation**: Generates a short professional draft for urgent emails, incorporating policy context when relevant.
- **Idempotency**: Marks emails with an `ai-processed` label to prevent duplicates.
- **Slack notifications (optional)**: Posts brief status updates to Slack via webhook.

## Tech Stack

- Python, LangGraph, LangChain OpenAI
- Google APIs: Gmail, Calendar
- Document retrieval with semantic search
- `python-dateutil`, `dotenv`, `requests`

Key files:

- `main.py` – Full workflow, Gmail/Calendar helpers, and LangGraph nodes
- `policies/` – Directory containing policy documents for context-aware responses
- `requirements.txt` – Python dependencies
- `credentials.json` – Google OAuth client (you provide)
- `token.json` – Generated after the first OAuth flow
- `.env` – Environment variables (you provide)

## Prerequisites

- Python 3.10+ recommended
- A Google Cloud project with OAuth 2.0 Client ID (Desktop) for Gmail + Calendar
- OpenAI API key (for LLM)
- Optional: Slack Incoming Webhook URL

## Setup

1. Create and activate a virtual environment

- macOS/Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
```

- Windows (PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

2. Install dependencies

```bash
pip install -r requirements.txt
```

3. Configure environment variables
   Create a `.env` file in the project root:

```env
# Required
OPENAI_API_KEY=sk-...

# Optional (Slack notifications)
ENABLE_SLACK=true
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/XXX/YYY/ZZZ
```

4. Configure Google OAuth credentials

- In Google Cloud Console, enable APIs:
  - Gmail API
  - Google Calendar API
- Create OAuth Client ID (Application type: Desktop app)
- Download the JSON and save it as `credentials.json` in the project root
- On first run, a browser window will open to authorize and will generate `token.json`

## Configuration

Key configuration constants live in `main_langgraph.py`:

- `SCOPES` – Gmail read/compose/modify + Calendar access
- `LLM_MODEL` – default `gpt-4-turbo` (can be changed)
- Timezone handling:
  - `LOCAL_OFFSET` / `LOCAL_TZ` currently set for Nepal (UTC+05:45)
  - Calendar events use `Asia/Kathmandu`
  - Adjust these for your locale if needed

Other tweakables:

- In `get_emails(...)` you can pass `max_results` to control the Gmail API page size. The current entrypoint calls `get_emails(gmail_service)` without a limit, so it fetches a single page using the API's default page size (no pagination implemented).

## How it works (Workflow)

The workflow is implemented with LangGraph in `create_email_workflow()` using four nodes, with policy-aware response generation:

- `datetime_detection_node(state)`

  - Skips `no-reply` senders
  - Extracts a datetime from the email body
  - Checks calendar availability and either:
    - Books the meeting and sends an AI-crafted confirmation reply, or
    - Suggests alternative time slots via email
  - Marks the email as `ai-processed`

- `meeting_confirmation_node(state)`

  - Detects confirmation replies (keywords/times)
  - Creates a calendar event at the confirmed time and replies with confirmation
  - Marks as `ai-processed`

- `urgency_analysis_node(state)`

  - Classifies the email as `urgent` or `not urgent` via LLM
  - Skips if already handled by calendar nodes or `no-reply`

- `draft_creation_node(state)`
  - For urgent emails, generates a short professional draft and saves it in Gmail drafts
  - Marks as `ai-processed`

Conditional edges ensure the graph proceeds only as needed and stops when done.

## Running the project

From the project root, after activating your venv and setting up `.env` and `credentials.json`:

```bash
python main.py
```

## Policy Documents

The agent can be customized by adding policy documents in the `policies/` directory. These documents will be used to provide context-aware responses. The system will automatically load all `.md` files from this directory and use them to inform its responses.

To add a new policy:
1. Create a new Markdown (`.md`) file in the `policies/` directory
2. The system will automatically index it and use it for context in responses

On first run, complete the Google OAuth flow in your browser. The script will then:

- Fetch one page of unread emails (Gmail API default page size)
- Process each through the workflow
- Print actions to the console and notify Slack (if enabled)

## Testing tips

- Send yourself an email with a meeting request like: "Can we meet on August 25 at 4:30 PM?"
- Reply with confirmations like: "Yes, that works" or "Let's go with the second option" to test confirmation parsing.
- Try an urgent message to see a draft appear in Gmail drafts.

## Permissions and data

- The app requests Gmail read/compose/modify and Calendar access.
- Processed emails get a hidden label `ai-processed` for idempotency.
- The app never replies to `no-reply` style addresses.

## Troubleshooting

- Missing OpenAI key: ensure `OPENAI_API_KEY` is set in `.env`.
- Google auth issues: delete `token.json` and re-run to re-auth.
- Timezone mismatches: adjust `LOCAL_OFFSET`, `LOCAL_TZ`, and the event time zone.
- Slack not posting: set `ENABLE_SLACK=true` and a valid `SLACK_WEBHOOK_URL`.
- Package problems: re-run `pip install -r requirements.txt` in your venv.

## Limitations

- Datetime extraction is heuristic and may misinterpret ambiguous text.
- Only checks/suggests availability from the primary calendar.
- Currently processes only a single page of unread emails (no pagination across multiple pages).

## License

MIT License. See the `LICENSE` file for details.
