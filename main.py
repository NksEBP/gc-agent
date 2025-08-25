import os
import json
import base64
import re
import requests

from typing import TypedDict, Annotated, Any
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from dateutil import parser as date_parser
from email.utils import parseaddr
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


# ============================================================
# CONFIG
# ============================================================
SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.compose',
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/calendar'  # Full calendar access for creating events
]
LLM_MODEL = "gpt-4-turbo"   # or "gpt-3.5-turbo" for cheaper runs


# ============================================================
# STATE DEFINITION
# ============================================================
class EmailState(TypedDict):
    email: dict
    urgency_result: str
    draft_content: str
    calendar_result: str
    datetime_detected: datetime
    meeting_confirmed: bool
    action_taken: str
    messages: Annotated[list, add_messages]
    processed: bool
    gmail_service: Any
    calendar_service: Any
    user_tz_str: str
    user_tzinfo: timezone
    counters: dict
    log_seq: int


# ============================================================
# AUTH HELPERS
# ============================================================
def get_credentials():
    """Authenticate once and return creds for Gmail + Calendar"""
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    return creds


def get_gmail_service(creds):
    return build('gmail', 'v1', credentials=creds)


def get_calendar_service(creds):
    return build('calendar', 'v3', credentials=creds)

def get_user_timezone(calendar_service) -> tuple[str, ZoneInfo]:
    """Return user's primary Calendar timezone string and ZoneInfo.

    Fallback order:
      1) Google Calendar settings 'timezone'
      2) USER_TZ env var (e.g., 'Australia/Sydney')
      3) 'Asia/Kathmandu'
    """
    tz_str = None
    try:
        # Calendar settings API: settings.get(setting='timezone')
        setting = calendar_service.settings().get(setting='timezone').execute()
        tz_str = setting.get('value')
    except Exception:
        pass

    if not tz_str:
        tz_str = os.getenv('USER_TZ', 'Asia/Kathmandu')

    try:
        tzinfo = ZoneInfo(tz_str)
    except Exception:
        tz_str = 'Asia/Kathmandu'
        tzinfo = ZoneInfo(tz_str)

    return tz_str, tzinfo

def _log(node: str, event: str, state: EmailState, level: str = "info", **details):
    """Emit a structured JSON log with counters and a message id.

    Increments counters for events: processed, booked, suggested, drafted.
    Adds ISO-8601 timestamp and flattens nested 'details' if provided.
    """
    seq = int(state.get("log_seq", 0)) + 1
    state["log_seq"] = seq
    counters = state.get("counters") or {"processed": 0, "booked": 0, "suggested": 0, "drafted": 0}
    if event in counters:
        counters[event] = int(counters.get(event, 0)) + 1
    state["counters"] = counters

    # Flatten nested 'details' argument if present
    merged_details = {}
    if "details" in details and isinstance(details.get("details"), dict):
        merged_details.update(details.pop("details"))
    # Exclude reserved top-level keys
    reserved = {"msg_id", "node", "level", "event", "counters", "timestamp"}
    for k, v in list(details.items()):
        if k not in reserved:
            merged_details[k] = v

    tzinfo = state.get("user_tzinfo") or timezone.utc
    payload = {
        "timestamp": datetime.now(tzinfo).isoformat(),
        "msg_id": f"{node}-{seq}",
        "node": node,
        "level": level,
        "event": event,
        "counters": counters,
        "details": merged_details,
    }
    try:
        print(json.dumps(payload, default=str))
    except Exception:
        print(payload)

# Simple structured logger for main-level messages (no state/counters)
_MAIN_LOG_SEQ = 0
def _log_main(event: str, level: str = "info", **details):
    """Main-level logger with timestamp and flattened details."""
    global _MAIN_LOG_SEQ
    _MAIN_LOG_SEQ += 1

    # Flatten nested 'details' if present
    merged_details = {}
    if "details" in details and isinstance(details.get("details"), dict):
        merged_details.update(details.pop("details"))
    for k, v in list(details.items()):
        if k not in {"msg_id", "node", "level", "event", "counters", "timestamp"}:
            merged_details[k] = v

    payload = {
        "timestamp": datetime.now().astimezone().isoformat(),
        "msg_id": f"main-{_MAIN_LOG_SEQ}",
        "node": "main",
        "level": level,
        "event": event,
        "details": merged_details,
    }
    try:
        print(json.dumps(payload, default=str))
    except Exception:
        print(payload)


# ============================================================
# GMAIL HELPERS
# ============================================================
def get_emails(service, max_results=None):
    """Retrieve unread emails that haven't been processed by AI"""
    # Get unread emails, excluding those already processed
    results = service.users().messages().list(
        userId='me',
        maxResults=max_results,
        labelIds=['INBOX', 'UNREAD'],
        q='-label:ai-processed'  # Exclude emails with our custom label
    ).execute()

    messages = results.get('messages', [])
    email_data = []

    for msg in messages:
        msg_data = service.users().messages().get(
            userId='me',
            id=msg['id'],
            format='full'
        ).execute()

        headers = {h['name']: h['value'] for h in msg_data['payload']['headers']}
        body = ""

        if 'parts' in msg_data['payload']:
            for part in msg_data['payload']['parts']:
                if part['mimeType'] == 'text/plain' and 'data' in part['body']:
                    body += base64.urlsafe_b64decode(
                        part['body']['data']).decode('utf-8')
        elif 'data' in msg_data['payload']['body']:
            body = base64.urlsafe_b64decode(
                msg_data['payload']['body']['data']).decode('utf-8')

        email_data.append({
            'id': msg['id'],
            'threadId': msg_data['threadId'],
            'subject': headers.get('Subject', 'No Subject'),
            'from': headers.get('From'),
            'body': body[:2000]  # truncate for LLM
        })

    return email_data


def mark_email_as_processed(service, email_id):
    """Mark email as processed by AI to avoid re-processing"""
    try:
        # Create custom label if it doesn't exist
        try:
            service.users().labels().create(
                userId='me',
                body={
                    'name': 'ai-processed',
                    'labelListVisibility': 'labelHide',
                    'messageListVisibility': 'hide'
                }
            ).execute()
        except Exception:
            pass  # Label already exists
        
        # Get the label ID
        labels = service.users().labels().list(userId='me').execute()
        ai_label_id = None
        for label in labels['labels']:
            if label['name'] == 'ai-processed':
                ai_label_id = label['id']
                break
        
        if ai_label_id:
            # Add the label to the email
            service.users().messages().modify(
                userId='me',
                id=email_id,
                body={'addLabelIds': [ai_label_id]}
            ).execute()
    except Exception as e:
        print(f"Warning: Could not mark email as processed: {e}")


def create_draft(service, email_id, reply_content):
    """Create draft reply for an email"""
    message = service.users().messages().get(
        userId='me',
        id=email_id,
        format='metadata'
    ).execute()

    headers = message['payload']['headers']
    subject = next((h['value']
                   for h in headers if h['name'] == 'Subject'), '')
    from_email = next((h['value']
                      for h in headers if h['name'] == 'From'), '')

    _, reply_to = parseaddr(from_email)

    # Fail-safe: never reply to no-reply style addresses
    if is_no_reply(reply_to):
        print("  - Skipping reply: recipient is a no-reply address")
        return
    
    message_body = (
        f"To: {reply_to}\r\n"
        f"Subject: Re: {subject}\r\n"
        f"\r\n{reply_content}"
    )

    raw_message = base64.urlsafe_b64encode(
        message_body.encode('utf-8')).decode('utf-8')
    draft = {
        'message': {
            'threadId': message['threadId'],
            'raw': raw_message
        }
    }

    service.users().drafts().create(
        userId='me',
        body=draft
    ).execute()


def send_reply(service, email_id, reply_content):
    """Send a direct reply to an email"""
    message = service.users().messages().get(
        userId='me',
        id=email_id,
        format='metadata'
    ).execute()

    headers = message['payload']['headers']
    subject = next((h['value']
                   for h in headers if h['name'] == 'Subject'), '')
    from_email = next((h['value']
                      for h in headers if h['name'] == 'From'), '')

    _, reply_to = parseaddr(from_email)
    # Fail-safe: never reply to no-reply style addresses
    if is_no_reply(reply_to):
        print("  - Skipping reply: recipient is a no-reply address")
        return
    
    message_body = (
        f"To: {reply_to}\r\n"
        f"Subject: Re: {subject}\r\n"
        f"\r\n{reply_content}"
    )

    raw_message = base64.urlsafe_b64encode(
        message_body.encode('utf-8')).decode('utf-8')

    send_body = {
        'raw': raw_message,
        'threadId': message['threadId']
    }

    service.users().messages().send(
        userId='me',
        body=send_body
    ).execute()


# ============================================================
# CALENDAR & TIME HELPERS
# ============================================================

# Common timezone abbreviations (esp. AU) for dateutil parsing
TZINFOS = {
    # Australia
    'AEST': timezone(timedelta(hours=10)),
    'AEDT': timezone(timedelta(hours=11)),
    'ACST': timezone(timedelta(hours=9, minutes=30)),
    'ACDT': timezone(timedelta(hours=10, minutes=30)),
    'AWST': timezone(timedelta(hours=8)),
    # US (for completeness)
    'PST': timezone(timedelta(hours=-8)),
    'PDT': timezone(timedelta(hours=-7)),
    'MST': timezone(timedelta(hours=-7)),
    'MDT': timezone(timedelta(hours=-6)),
    'CST': timezone(timedelta(hours=-6)),
    'CDT': timezone(timedelta(hours=-5)),
    'EST': timezone(timedelta(hours=-5)),
    'EDT': timezone(timedelta(hours=-4)),
    # Other commons
    'NPT': timezone(timedelta(hours=5, minutes=45)),  # Nepal
    'IST': timezone(timedelta(hours=5, minutes=30)),  # India
    'BST': timezone(timedelta(hours=1)),              # British Summer Time
    'GMT': timezone(timedelta(hours=0)),
    'UTC': timezone(timedelta(hours=0)),
}


# ============================================================
# EMAIL ADDRESS HELPERS
# ============================================================
def is_no_reply(from_header: str) -> bool:
    """Return True if the sender appears to be a no-reply style address."""
    _, sender = parseaddr(from_header or "")
    local = sender.split("@")[0].lower() if sender else ""
    patterns = [
        "no-reply",
        "noreply",
        "no_reply",
        "do-not-reply",
        "donotreply",
        "do_not_reply",
    ]
    return any(p in local for p in patterns)

def extract_datetime_from_text(text, default_tz: timezone):
    """Extract the first datetime from text, normalize AM/PM.

    - If parsed datetime has no tzinfo, localize to default_tz.
    - If parsed datetime has tzinfo, convert to default_tz.
    """
    try:
        # Normalize lowercase am/pm
        text = text.replace("am", "AM").replace("pm", "PM")
        
        # Use today's date with 00:00:00 as default to avoid inheriting current time
        today_start = datetime.now(default_tz).replace(hour=0, minute=0, second=0, microsecond=0)
        dt = date_parser.parse(text, fuzzy=True, default=today_start, tzinfos=TZINFOS)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=default_tz)
        else:
            dt = dt.astimezone(default_tz)
        return dt
    except Exception:
        return None

def is_meeting_confirmation_reply(email_body):
    """Check if email is a meeting confirmation reply"""
    confirmation_keywords = [
        "anytime is fine", "anytime is ok", "anytime works", "any time is fine",
        "first option", "second option", "third option",
        "yes, that works", "sounds good", "perfect", "confirmed",
        "i'll take", "let's go with", "book it", "schedule it"
    ]
    
    email_lower = email_body.lower()
    
    # Check for generic confirmation keywords
    if any(keyword in email_lower for keyword in confirmation_keywords):
        return True
    
    # Check for any time pattern (dynamic detection)
    time_patterns = [
        r'\d{1,2}:\d{2}\s*[ap]m',  # 4:58 pm, 5:13 pm format
        r'\d{1,2}\s*[ap]m',        # 4 pm, 5 pm format
    ]
    
    for pattern in time_patterns:
        if re.search(pattern, email_lower):
            return True
    
    return False

def extract_suggested_times_from_email_chain(email_body, default_tz: timezone):
    """Extract previously suggested meeting times from email thread"""
    suggested_times = []
    
    # Look for time patterns in the email chain
    time_patterns = [
        r'(\d{1,2}:\d{2}\s*[AP]M)',  # 4:58 PM, 5:13 PM format
        r'(\w+\s+\d{1,2},\s+\d{4}\s+at\s+\d{1,2}:\d{2}\s*[AP]M)'  # August 21, 2025 at 4:58 PM
    ]
    
    for pattern in time_patterns:
        matches = re.findall(pattern, email_body, re.IGNORECASE)
        for match in matches:
            try:
                # Try to parse each found time
                dt = extract_datetime_from_text(match, default_tz)
                if dt and dt not in suggested_times:
                    suggested_times.append(dt)
            except:
                continue
    
    return suggested_times

def extract_confirmed_meeting_time(email_body, default_tz: timezone):
    """Extract confirmed meeting time from reply, or return first suggested time"""
    # First try to extract specific time from the reply
    dt = extract_datetime_from_text(email_body, default_tz)
    if dt:
        return dt
    
    # Extract previously suggested times from the email chain
    suggested_times = extract_suggested_times_from_email_chain(email_body, default_tz)
    
    # If "anytime" or similar, return first suggested time or default
    anytime_keywords = ["anytime", "any time", "flexible", "whatever works"]
    if any(keyword in email_body.lower() for keyword in anytime_keywords):
        if suggested_times:
            return suggested_times[0]  # Return first suggested time
        else:
            # Fallback to next business hour
            now = datetime.now(default_tz)
            if now.hour >= 17:  # After 5 PM
                next_day = now + timedelta(days=1)
                return next_day.replace(hour=9, minute=0, second=0, microsecond=0)
            else:
                return now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    
    # Look for references to option selection (first, second, third)
    email_lower = email_body.lower()
    option_patterns = [r"first", r"second", r"third", r"1st", r"2nd", r"3rd"]
    
    for i, pattern in enumerate(option_patterns):
        if re.search(pattern, email_lower):
            if i < len(suggested_times):
                return suggested_times[i]
            break
    
    # If we found suggested times but no specific selection, return first one
    if suggested_times:
        return suggested_times[0]
    
    return None


def create_calendar_event(calendar_service, start_time, duration_minutes=60, title="Meeting", attendee_email=None, time_zone: str | None = None):
    """Create a calendar event"""
    end_time = start_time + timedelta(minutes=duration_minutes)
    
    event = {
        'summary': title,
        'start': {
            'dateTime': start_time.isoformat(),
            'timeZone': time_zone or 'UTC',
        },
        'end': {
            'dateTime': end_time.isoformat(),
            'timeZone': time_zone or 'UTC',
        },
    }
    
    if attendee_email:
        event['attendees'] = [{'email': attendee_email}]
    
    try:
        created_event = calendar_service.events().insert(
            calendarId='primary',
            body=event
        ).execute()
        return created_event
    except Exception as e:
        print(f"Error creating calendar event: {e}")
        return None

def find_next_available_slots(calendar_service, requested_time, duration_minutes=60, num_suggestions=3, default_tz: timezone | None = None):
    """Find next available time slots after the requested time, avoiding conflicts"""
    available_slots = []
    current_time = requested_time
    
    # Look for slots within the next 7 days
    for _ in range(672):  # Check 672 time slots (every 15 minutes for a week)
        end_time = current_time + timedelta(minutes=duration_minutes)
        
        # Check if this slot conflicts with existing events
        events_result = calendar_service.events().list(
            calendarId='primary',
            timeMin=current_time.isoformat(),
            timeMax=end_time.isoformat(),
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        if not events:  # Slot is free
            available_slots.append(current_time)
            if len(available_slots) >= num_suggestions:
                break
        else:
            # If there's a conflict, jump to after the conflicting event ends + 15 min buffer
            for event in events:
                if 'dateTime' in event['end']:
                    event_end = datetime.fromisoformat(event['end']['dateTime'].replace('Z', '+00:00'))
                    if default_tz is not None:
                        event_end = event_end.astimezone(default_tz)
                    # Jump to 15 minutes after the event ends
                    current_time = event_end + timedelta(minutes=15)
                    break
            continue
        
        # Move to next 15-minute slot
        current_time += timedelta(minutes=15)
    
    return available_slots

# ============================================================
# NOTIFICATION HELPERS (Slack)
# ============================================================

def _notify_slack(text: str):
    webhook = os.getenv("SLACK_WEBHOOK_URL", "").strip()
    enabled = os.getenv("ENABLE_SLACK", "true").strip().lower() in ("1", "true", "yes", "on")
    if not enabled or not webhook:
        return
    try:
        requests.post(webhook, json={"text": text}, timeout=10)
    except Exception:
        pass

def generate_calendar_confirmation_email(original_email, created_event, start_time, meeting_title):
    """Generate professional calendar confirmation email using AI"""
    event_link = created_event.get('htmlLink', '') if created_event else ''
    
    prompt = f"""Write a professional calendar confirmation email based on this meeting request:

        ORIGINAL EMAIL:
        FROM: {original_email['from']}
        SUBJECT: {original_email['subject']}
        CONTENT: {original_email['body']}

        MEETING DETAILS:
        Title: {meeting_title}
        Date & Time: {start_time.strftime('%B %d, %Y at %I:%M %p')}
        Calendar Link: {event_link}

        Guidelines:
        - Confirm the meeting is scheduled
        - Reference the original request context
        - Include all meeting details
        - Provide the calendar link
        - Keep professional and friendly tone
        - Keep under 4 sentences"""

    messages = [
        SystemMessage(content="You are a calendar & meeting coordinator expert at scheduling and confirming meetings (body only)."),
        HumanMessage(content=prompt)
    ]
    
    response = llm.invoke(messages)
    return response.content.strip()

def generate_alternative_times_email(original_email, requested_time, alternative_slots, meeting_title):
    """Generate email with alternative meeting time suggestions"""
    alternatives_text = "\n".join([
        f"- {slot.strftime('%B %d, %Y at %I:%M %p')}" 
        for slot in alternative_slots
    ])
    
    prompt = f"""Write a professional email suggesting alternative meeting times:

        ORIGINAL EMAIL:
        FROM: {original_email['from']}
        SUBJECT: {original_email['subject']}
        CONTENT: {original_email['body']}

        SITUATION:
        Requested time: {requested_time.strftime('%B %d, %Y at %I:%M %p')} is not available
        Meeting title: {meeting_title}

        ALTERNATIVE TIME OPTIONS:
        {alternatives_text}

        Guidelines:
        - Apologize that requested time is not available
        - Reference the original request context
        - Suggest the alternative times clearly
        - Ask them to confirm preferred time
        - Keep professional and helpful tone
        - Keep under 5 sentences"""

    messages = [
        SystemMessage(content="You are a calendar & meeting coordinator expert at scheduling and confirming meetings (body only)."),
        HumanMessage(content=prompt)
    ]
    
    response = llm.invoke(messages)
    return response.content.strip()

def check_calendar_availability(calendar_service, start_time, duration_minutes=60, attendee_email=None, meeting_title="Meeting", original_email=None, time_zone: str | None = None, default_tz: timezone | None = None):
    """Check if a time slot is free and book it if available.

    Returns tuple: (reply_text: str, status: str) where status in {"booked","suggested","error"}
    """
    end_time = start_time + timedelta(minutes=duration_minutes)

    # Convert to RFC3339 string with local timezone
    start_iso = start_time.isoformat()
    end_iso = end_time.isoformat()

    events_result = calendar_service.events().list(
        calendarId='primary',
        timeMin=start_iso,
        timeMax=end_iso,
        singleEvents=True,
        orderBy='startTime'
    ).execute()

    events = events_result.get('items', [])
    if events:
        # Time is busy - find alternative slots and suggest them
        if original_email:
            alternative_slots = find_next_available_slots(calendar_service, start_time, duration_minutes, default_tz=default_tz)
            if alternative_slots:
                return generate_alternative_times_email(original_email, start_time, alternative_slots, meeting_title), "suggested"
            else:
                return "That time seems to be booked in my calendar. I couldn't find alternative slots in the next week, but I will get back to you with other options asap.", "suggested"
        else:
            return "That time seems to be booked in my calendar, but I will get back to you with confirmation asap.", "suggested"
    else:
        # Time is available - create the event
        created_event = create_calendar_event(
            calendar_service,
            start_time,
            duration_minutes,
            meeting_title,
            attendee_email,
            time_zone=time_zone,
        )
        
        if created_event and original_email:
            # Generate AI-powered confirmation email
            return generate_calendar_confirmation_email(original_email, created_event, start_time, meeting_title), "booked"
        elif created_event:
            event_link = created_event.get('htmlLink', '')
            return f"Perfect! I've booked that time in my calendar. Meeting scheduled for {start_time.strftime('%B %d, %Y at %I:%M %p')}. Calendar link: {event_link}", "booked"
        else:
            return "That time is available, but I had trouble creating the calendar event. I'll get back to you with confirmation asap.", "error"


# ============================================================
# LANGGRAPH NODES
# ============================================================
llm = ChatOpenAI(model=LLM_MODEL, temperature=0.3)

def datetime_detection_node(state: EmailState) -> EmailState:
    """Check if email contains datetime and handle calendar booking"""
    email = state["email"]
    
    # Ignore no-reply senders entirely
    if is_no_reply(email.get('from', '')):
        gmail_service = state.get("gmail_service")
        try:
            if gmail_service:
                mark_email_as_processed(gmail_service, email['id'])
            state["action_taken"] = "ignored_no_reply"
        except Exception as e:
            _log("datetime_detection", "error", state, level="error", details={"exception": str(e)})
        return state
    # Determine user timezone (fetch once and cache in state)
    tz_str = state.get("user_tz_str")
    tzinfo = state.get("user_tzinfo")
    if not tz_str or not tzinfo:
        calendar_service = state.get("calendar_service")
        tz_str, tzinfo = get_user_timezone(calendar_service)
        state["user_tz_str"] = tz_str
        state["user_tzinfo"] = tzinfo

    dt = extract_datetime_from_text(email['body'], tzinfo)
    
    if dt:
        _log("datetime_detection", "datetime_detected", state, details={"detected_time": dt.isoformat(), "from": email.get('from')})
        state["datetime_detected"] = dt
        
        # Extract attendee email from sender
        _, attendee_email = parseaddr(email['from'])
        
        # Use email subject as meeting title, or default
        meeting_title = email['subject'] if email['subject'] != 'No Subject' else "Meeting"
        
        # Get services from state
        calendar_service = state.get("calendar_service")
        gmail_service = state.get("gmail_service")
        
        availability_reply, availability_status = check_calendar_availability(
            calendar_service,
            dt,
            duration_minutes=60,
            attendee_email=attendee_email,
            meeting_title=meeting_title,
            original_email=email,
            time_zone=tz_str,
            default_tz=tzinfo,
        )
        
        try:
            send_reply(gmail_service, email['id'], availability_reply)
            if availability_status == "booked":
                _log("datetime_detection", "booked", state, details={"start_time": dt.isoformat(), "attendee": attendee_email, "title": meeting_title})
                # Notify Slack only on actual booking
                msg = (
                    f"Booked: {meeting_title} on {dt.strftime('%B %d, %Y at %I:%M %p')} "
                    f"for {email['from']}."
                )
                _notify_slack(msg)
            elif availability_status == "suggested":
                _log("datetime_detection", "suggested", state, details={"requested_time": dt.isoformat(), "attendee": attendee_email, "title": meeting_title})
            else:
                _log("datetime_detection", "error", state, level="error", details={"requested_time": dt.isoformat(), "attendee": attendee_email, "title": meeting_title})
            mark_email_as_processed(gmail_service, email['id'])
            _log("datetime_detection", "processed", state, details={"email_id": email['id']})
            state["action_taken"] = "calendar_booking_completed"
            state["calendar_result"] = availability_reply
        except Exception as e:
            _log("datetime_detection", "error", state, level="error", details={"exception": str(e)})
            state["action_taken"] = "calendar_booking_failed"
    
    return state

def meeting_confirmation_node(state: EmailState) -> EmailState:
    """Handle meeting confirmation replies"""
    email = state["email"]
    
    # Ignore no-reply senders entirely
    if is_no_reply(email.get('from', '')):
        gmail_service = state.get("gmail_service")
        try:
            if gmail_service:
                mark_email_as_processed(gmail_service, email['id'])
            state["action_taken"] = "ignored_no_reply"
        except Exception as e:
            print(f"  - Warning: could not mark no-reply email as processed: {e}")
        return state
    
    if is_meeting_confirmation_reply(email['body']):
        _log("meeting_confirmation", "confirmation_detected", state, details={"from": email.get('from'), "subject": email.get('subject')})
        
        # Extract attendee email from sender
        _, attendee_email = parseaddr(email['from'])
        
        # Use email subject as meeting title, or default
        meeting_title = email['subject'] if email['subject'] != 'No Subject' else "Meeting"
        
        # Determine user timezone (use cached or fetch)
        tz_str = state.get("user_tz_str")
        tzinfo = state.get("user_tzinfo")
        if not tz_str or not tzinfo:
            calendar_service = state.get("calendar_service")
            tz_str, tzinfo = get_user_timezone(calendar_service)
            state["user_tz_str"] = tz_str
            state["user_tzinfo"] = tzinfo

        # Try to extract specific time from reply, or use first suggested time
        confirmed_time = extract_confirmed_meeting_time(email['body'], tzinfo)
        
        if confirmed_time:
            # Get services from state
            calendar_service = state.get("calendar_service")
            gmail_service = state.get("gmail_service")
            
            # Create the confirmed meeting
            created_event = create_calendar_event(
                calendar_service,
                confirmed_time,
                duration_minutes=60,
                title=meeting_title,
                attendee_email=attendee_email,
                time_zone=tz_str,
            )
            
            if created_event:
                event_link = created_event.get('htmlLink', '')
                confirmation_reply = f"Thank you for confirming! I've scheduled our meeting for {confirmed_time.strftime('%B %d, %Y at %I:%M %p')}. Calendar invite sent. Link: {event_link}"
            else:
                confirmation_reply = "Thank you for confirming. I'll send you a calendar invite shortly."
            
            try:
                send_reply(gmail_service, email['id'], confirmation_reply)
                if created_event:
                    _log("meeting_confirmation", "booked", state, details={"start_time": confirmed_time.isoformat(), "attendee": attendee_email, "title": meeting_title})
                else:
                    _log("meeting_confirmation", "error", state, level="error", details={"start_time": confirmed_time.isoformat(), "attendee": attendee_email, "title": meeting_title})
                mark_email_as_processed(gmail_service, email['id'])
                _log("meeting_confirmation", "processed", state, details={"email_id": email['id']})
                state["action_taken"] = "meeting_confirmed"
                state["meeting_confirmed"] = True
                state["calendar_result"] = confirmation_reply
                # Notify Slack
                msg = (
                    f"Confirmed: {meeting_title} on {confirmed_time.strftime('%B %d, %Y at %I:%M %p')} "
                    f"for {email['from']}."
                )
                _notify_slack(msg)
            except Exception as e:
                _log("meeting_confirmation", "error", state, level="error", details={"exception": str(e)})
                state["action_taken"] = "meeting_confirmation_failed"
    
    return state

def urgency_analysis_node(state: EmailState) -> EmailState:
    """Analyze email urgency using LLM"""
    email = state["email"]
    
    # Skip if already handled by calendar nodes
    if state.get("action_taken") in ["calendar_booking_completed", "meeting_confirmed"]:
        return state
    
    # If from a no-reply address, treat as not urgent and mark processed immediately
    if is_no_reply(email.get('from', '')):
        state["urgency_result"] = "not urgent"
        gmail_service = state.get("gmail_service")
        try:
            if gmail_service:
                mark_email_as_processed(gmail_service, email['id'])
            state["action_taken"] = "not_urgent_processed"
        except Exception as e:
            print(f"  - Warning: could not mark no-reply email as processed: {e}")
        return state
    
    urgency_prompt = f"""Analyze this email for urgency:

            FROM: {email['from']}
            SUBJECT: {email['subject']}
            CONTENT:
            {email['body']}

            Respond with exactly one word: either 'urgent' or 'not urgent'."""

    messages = [
        SystemMessage(content="You are a senior email analyst expert at triaging urgent matters."),
        HumanMessage(content=urgency_prompt)
    ]
    
    response = llm.invoke(messages)
    urgency_result = response.content.strip().lower()
    
    state["urgency_result"] = urgency_result
    state["messages"] = messages + [response]
    
    # If determined not urgent, mark as processed here (since graph ends after this node)
    if not urgency_result.startswith("urgent"):
        gmail_service = state.get("gmail_service")
        try:
            if gmail_service:
                mark_email_as_processed(gmail_service, email['id'])
            _log("urgency_analysis", "processed", state, details={"email_id": email['id']})
            state["action_taken"] = "not_urgent_processed"
        except Exception as e:
            _log("urgency_analysis", "error", state, level="error", details={"exception": str(e)})
    
    return state

def draft_creation_node(state: EmailState) -> EmailState:
    """Create draft for urgent emails"""
    email = state["email"]
    urgency_result = state.get("urgency_result", "")
    
    if urgency_result.startswith("urgent"):
        _log("draft_creation", "urgent_detected", state, details={"from": email.get('from'), "subject": email.get('subject')})
        
        draft_prompt = f"""Write a professional draft response for this urgent email:

            Original email content:
            {email['body']}

            Guidelines:
            - Acknowledge receipt and show empathy
            - Keep response under 3 sentences
            - Offer immediate next steps if needed
            - Maintain professional tone"""

        messages = [
            SystemMessage(content="You are an executive communications specialist who crafts executive-level communications (body only)"),
            HumanMessage(content=draft_prompt)
        ]
        
        response = llm.invoke(messages)
        draft_content = response.content.strip()
        
        # Get services from state
        gmail_service = state.get("gmail_service")
        
        try:
            create_draft(gmail_service, email['id'], draft_content)
            _log("draft_creation", "drafted", state, details={"email_id": email['id']})
            mark_email_as_processed(gmail_service, email['id'])
            _log("draft_creation", "processed", state, details={"email_id": email['id']})
            state["action_taken"] = "draft_created"
            state["draft_content"] = draft_content
            # Notify Slack
            subject = email.get('subject', 'No Subject')
            _notify_slack(f"Draft created for: {subject} from {email['from']}.")
        except Exception as e:
            _log("draft_creation", "error", state, level="error", details={"exception": str(e)})
            state["action_taken"] = "draft_creation_failed"
    else:
        _log("draft_creation", "skipped", state, details={"reason": "not_urgent"})
        # Get services to mark as processed
        gmail_service = state.get("gmail_service")
        mark_email_as_processed(gmail_service, email['id'])
        state["action_taken"] = "not_urgent_processed"
    
    return state

def should_continue_after_datetime(state: EmailState) -> str:
    """Conditional edge: continue based on datetime detection result"""
    action_taken = state.get("action_taken", "")
    if action_taken in ("calendar_booking_completed", "ignored_no_reply", "not_urgent_processed"):
        return END
    return "meeting_confirmation"

def should_continue_after_meeting(state: EmailState) -> str:
    """Conditional edge: continue based on meeting confirmation result"""
    action_taken = state.get("action_taken", "")
    if action_taken in ("meeting_confirmed", "ignored_no_reply", "not_urgent_processed"):
        return END
    return "urgency_analysis"

def should_continue_after_urgency(state: EmailState) -> str:
    """Conditional edge: continue to draft creation if urgent, else end"""
    urgency_result = state.get("urgency_result", "")
    if urgency_result.startswith("urgent"):
        return "draft_creation"
    return END


# ============================================================
# WORKFLOW DEFINITION
# ============================================================
def create_email_workflow():
    """Create the LangGraph workflow"""
    workflow = StateGraph(EmailState)
    
    # Add nodes
    workflow.add_node("datetime_detection", datetime_detection_node)
    workflow.add_node("meeting_confirmation", meeting_confirmation_node)
    workflow.add_node("urgency_analysis", urgency_analysis_node)
    workflow.add_node("draft_creation", draft_creation_node)
    
    # Define the flow
    workflow.set_entry_point("datetime_detection")
    
    # Sequential flow with conditional edges
    workflow.add_conditional_edges(
        "datetime_detection",
        should_continue_after_datetime,
        {
            "meeting_confirmation": "meeting_confirmation",
            END: END
        }
    )

    workflow.add_conditional_edges(
        "meeting_confirmation",
        should_continue_after_meeting,
        {
            "urgency_analysis": "urgency_analysis",
            END: END
        }
    )
    
    workflow.add_conditional_edges(
        "urgency_analysis",
        should_continue_after_urgency,
        {
            "draft_creation": "draft_creation",
            END: END
        }
    )
    
    workflow.add_edge("draft_creation", END)
    
    return workflow.compile()


# ============================================================
# MAIN WORKFLOW
# ============================================================
if __name__ == "__main__":
    # Create the workflow
    app = create_email_workflow()
    
    # Get services
    creds = get_credentials()
    gmail_service = get_gmail_service(creds)
    calendar_service = get_calendar_service(creds)
    # Fetch user's calendar timezone once
    user_tz_str, user_tzinfo = get_user_timezone(calendar_service)
    
    emails = get_emails(gmail_service)
    _log_main("start", count=len(emails))

    for email in emails:
        _log_main("processing_email", subject=email.get('subject'), id=email.get('id'))
        
        # Initialize state
        initial_state = {
            "email": email,
            "urgency_result": "",
            "draft_content": "",
            "calendar_result": "",
            "datetime_detected": None,
            "meeting_confirmed": False,
            "action_taken": "",
            "messages": [],
            "processed": False,
            "gmail_service": gmail_service,
            "calendar_service": calendar_service,
            "user_tz_str": user_tz_str,
            "user_tzinfo": user_tzinfo,
            "counters": {"processed": 0, "booked": 0, "suggested": 0, "drafted": 0},
            "log_seq": 0,
        }
        
        # Run the workflow
        try:
            final_state = app.invoke(initial_state)
            _log_main("final_action", action=final_state.get('action_taken', 'none'), email_id=email.get('id'))
        except Exception as e:
            _log_main("error", level="error", error=str(e), email_id=email.get('id'))
    _log_main("done")
