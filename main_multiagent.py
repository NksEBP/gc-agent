import os
from typing import Any
from datetime import datetime

from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI

# Reuse helpers, types, and logging from the single-agent implementation
from main import (
    EmailState,
    get_credentials,
    get_gmail_service,
    get_calendar_service,
    get_user_timezone,
    get_emails,
    mark_email_as_processed,
    create_draft,
    send_reply,
    extract_datetime_from_text,
    is_no_reply,
    extract_confirmed_meeting_time,
    check_calendar_availability,
    _log,
    _log_main,
    _notify_slack,
    retrieve_policy_context,
)

# Separate models per agent (can be tuned independently)
TRIAGE_MODEL = os.getenv("TRIAGE_MODEL", "gpt-4o-mini")
DRAFT_MODEL = os.getenv("DRAFT_MODEL", "gpt-4-turbo")
CALENDAR_MODEL = os.getenv("CALENDAR_MODEL", "gpt-4o-mini")

llm_triage = ChatOpenAI(model=TRIAGE_MODEL, temperature=0.2)
llm_draft = ChatOpenAI(model=DRAFT_MODEL, temperature=0.3)
llm_calendar = ChatOpenAI(model=CALENDAR_MODEL, temperature=0.2)


# ===============================
# Multi-Agent Nodes (Sub-Agents)
# ===============================

def calendar_agent_node(state: EmailState) -> EmailState:
    """CalendarAgent: detect datetime and attempt booking/suggestions."""
    email = state["email"]

    # Ignore no-reply senders entirely
    if is_no_reply(email.get('from', '')):
        gmail_service = state.get("gmail_service")
        try:
            if gmail_service:
                mark_email_as_processed(gmail_service, email['id'])
            state["action_taken"] = "ignored_no_reply"
        except Exception as e:
            _log("calendar_agent", "error", state, level="error", details={"exception": str(e)})
        return state

    # Determine user timezone (fetch once and cache in state)
    tz_str = state.get("user_tz_str")
    tzinfo = state.get("user_tzinfo")
    if not tz_str or not tzinfo:
        calendar_service = state.get("calendar_service")
        tz_str, tzinfo = get_user_timezone(calendar_service)
        state["user_tz_str"] = tz_str
        state["user_tzinfo"] = tzinfo

    # Use calendar LLM only if you later enhance with prompting; for now reuse extractors
    dt = extract_datetime_from_text(email['body'], tzinfo)
    if not dt:
        return state

    _log("calendar_agent", "datetime_detected", state, detected_time=dt.isoformat(), sender=email.get('from'))
    state["datetime_detected"] = dt

    # Extract attendee email from sender
    from email.utils import parseaddr
    _, attendee_email = parseaddr(email['from'])

    meeting_title = email['subject'] if email['subject'] != 'No Subject' else "Meeting"

    calendar_service = state.get("calendar_service")
    gmail_service = state.get("gmail_service")

    reply_text, status = check_calendar_availability(
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
        send_reply(gmail_service, email['id'], reply_text)
        if status == "booked":
            _log("calendar_agent", "booked", state, start_time=dt.isoformat(), attendee=attendee_email, title=meeting_title)
            _notify_slack(
                f"Booked: {meeting_title} on {dt.strftime('%B %d, %Y at %I:%M %p')} for {email['from']}."
            )
        elif status == "suggested":
            _log("calendar_agent", "suggested", state, requested_time=dt.isoformat(), attendee=attendee_email, title=meeting_title)
        else:
            _log("calendar_agent", "error", state, level="error", requested_time=dt.isoformat(), attendee=attendee_email, title=meeting_title)
        mark_email_as_processed(gmail_service, email['id'])
        _log("calendar_agent", "processed", state, email_id=email['id'])
        state["action_taken"] = "calendar_booking_completed" if status == "booked" else ("calendar_suggested" if status == "suggested" else "calendar_error")
        state["calendar_result"] = reply_text
    except Exception as e:
        _log("calendar_agent", "error", state, level="error", exception=str(e))
        state["action_taken"] = "calendar_booking_failed"

    return state


def confirmation_agent_node(state: EmailState) -> EmailState:
    """ConfirmationAgent: interpret meeting confirmations and book."""
    email = state["email"]

    # Ignore no-reply senders entirely
    if is_no_reply(email.get('from', '')):
        gmail_service = state.get("gmail_service")
        try:
            if gmail_service:
                mark_email_as_processed(gmail_service, email['id'])
            state["action_taken"] = "ignored_no_reply"
        except Exception as e:
            _log("confirmation_agent", "error", state, level="error", exception=str(e))
        return state

    # Heuristic: if email looks like a confirmation, try to extract time
    def is_meeting_confirmation_reply(email_body: str) -> bool:
        import re
        confirmation_keywords = [
            "anytime is fine", "anytime is ok", "anytime works", "any time is fine",
            "first option", "second option", "third option",
            "yes, that works", "sounds good", "perfect", "confirmed",
            "i'll take", "let's go with", "book it", "schedule it"
        ]
        text = email_body.lower()
        if any(k in text for k in confirmation_keywords):
            return True
        return bool(re.search(r"\b(\d{1,2}(:\d{2})?\s*[ap]m)\b", text))

    if not is_meeting_confirmation_reply(email['body']):
        return state

    _log("confirmation_agent", "confirmation_detected", state, sender=email.get('from'), subject=email.get('subject'))

    from email.utils import parseaddr
    _, attendee_email = parseaddr(email['from'])

    meeting_title = email['subject'] if email['subject'] != 'No Subject' else "Meeting"

    # Determine user timezone
    tz_str = state.get("user_tz_str")
    tzinfo = state.get("user_tzinfo")
    if not tz_str or not tzinfo:
        calendar_service = state.get("calendar_service")
        tz_str, tzinfo = get_user_timezone(calendar_service)
        state["user_tz_str"] = tz_str
        state["user_tzinfo"] = tzinfo

    confirmed_time = extract_confirmed_meeting_time(email['body'], tzinfo)
    if not confirmed_time:
        return state

    calendar_service = state.get("calendar_service")
    gmail_service = state.get("gmail_service")

    created = check_calendar_availability(
        calendar_service,
        confirmed_time,
        duration_minutes=60,
        attendee_email=attendee_email,
        meeting_title=meeting_title,
        original_email=email,
        time_zone=tz_str,
        default_tz=tzinfo,
    )

    # check_calendar_availability returns (reply, status)
    reply_text, status = created
    try:
        send_reply(gmail_service, email['id'], reply_text)
        if status == "booked":
            _log("confirmation_agent", "booked", state, start_time=confirmed_time.isoformat(), attendee=attendee_email, title=meeting_title)
        else:
            _log("confirmation_agent", "error", state, level="error", start_time=confirmed_time.isoformat(), attendee=attendee_email, title=meeting_title)
        mark_email_as_processed(gmail_service, email['id'])
        _log("confirmation_agent", "processed", state, email_id=email['id'])
        state["action_taken"] = "meeting_confirmed" if status == "booked" else "meeting_confirmation_failed"
        state["meeting_confirmed"] = status == "booked"
        state["calendar_result"] = reply_text
        if status == "booked":
            _notify_slack(
                f"Confirmed: {meeting_title} on {confirmed_time.strftime('%B %d, %Y at %I:%M %p')} for {email['from']}."
            )
    except Exception as e:
        _log("confirmation_agent", "error", state, level="error", exception=str(e))
        state["action_taken"] = "meeting_confirmation_failed"

    return state


def triage_agent_node(state: EmailState) -> EmailState:
    """TriageAgent: classify urgency using its own model."""
    email = state["email"]

    # If already handled, skip
    if state.get("action_taken") in ["calendar_booking_completed", "meeting_confirmed", "ignored_no_reply"]:
        return state

    # Simple LLM-based classification prompt (can be improved)
    from langchain_core.messages import SystemMessage, HumanMessage
    system = SystemMessage(content="You are an assistant that classifies email urgency as 'urgent' or 'not urgent' succinctly.")
    human = HumanMessage(content=f"Email subject: {email['subject']}\n\nEmail body:\n{email['body']}\n\nReply with exactly 'urgent' or 'not urgent'.")

    try:
        resp = llm_triage.invoke([system, human])
        result = resp.content.strip().lower()
        if result not in ("urgent", "not urgent"):
            result = "not urgent"
        state["urgency_result"] = result
        _log("triage_agent", "classified", state, result=result)
    except Exception as e:
        _log("triage_agent", "error", state, level="error", exception=str(e))

    return state


def drafting_agent_node(state: EmailState) -> EmailState:
    """DraftAgent: write draft replies for urgent emails."""
    email = state["email"]
    urgency_result = state.get("urgency_result", "")

    if not urgency_result.startswith("urgent"):
        # Mark processed for not urgent
        gmail_service = state.get("gmail_service")
        try:
            if gmail_service:
                mark_email_as_processed(gmail_service, email['id'])
            _log("draft_agent", "processed", state, email_id=email['id'])
            state["action_taken"] = "not_urgent_processed"
        except Exception as e:
            _log("draft_agent", "error", state, level="error", exception=str(e))
        return state

    from langchain_core.messages import SystemMessage, HumanMessage
    # Retrieve policy context using the same helper as single-agent flow
    query = f"Urgent reply policy for subject: {email.get('subject', '')}. Body: {email.get('body', '')[:800]}"
    try:
        top_policies = retrieve_policy_context(query)
    except Exception as e:
        top_policies = []
        _log("draft_agent", "retrieval_error", state, level="warning", exception=str(e))
    policy_context = "\n\n".join(top_policies) if top_policies else "(No policy context retrieved; follow brevity, professional tone, no sensitive info.)"

    system = SystemMessage(content="You are an executive communications specialist who crafts executive-level communications (body only)")
    prompt = f"""Write a professional, policy-compliant draft response for this urgent email.

        POLICY CONTEXT (follow strictly):
        {policy_context}

        ORIGINAL EMAIL CONTENT:
        {email['body']}

        Guidelines:
        - Acknowledge receipt and show empathy
        - Keep response under 3 sentences
        - Offer immediate next steps if needed
        - Maintain professional tone
        - Do not include sensitive information or commitments you cannot verify
        - If scheduling is referenced, propose clear next steps without overcommitting"""
    human = HumanMessage(content=prompt)

    try:
        resp = llm_draft.invoke([system, human])
        draft_content = resp.content.strip()
        gmail_service = state.get("gmail_service")
        create_draft(gmail_service, email['id'], draft_content)
        _log("draft_agent", "drafted", state, email_id=email['id'])
        if top_policies:
            _log("draft_agent", "policy_used", state, snippets=min(3, len(top_policies)))
        state["action_taken"] = "draft_created"
        state["draft_content"] = draft_content
        _notify_slack(f"Draft created for: {email.get('subject', 'No Subject')} from {email['from']}.")
    except Exception as e:
        _log("draft_agent", "error", state, level="error", exception=str(e))
        state["action_taken"] = "draft_creation_failed"

    return state


# ===============================
# Routing Conditions
# ===============================

def route_after_calendar(state: EmailState) -> str:
    action = state.get("action_taken", "")
    if action in ("calendar_booking_completed", "ignored_no_reply", "not_urgent_processed"):
        return END
    # Try confirmation agent next if not already confirmed
    return "confirmation_agent"


def route_after_confirmation(state: EmailState) -> str:
    action = state.get("action_taken", "")
    if action in ("meeting_confirmed", "ignored_no_reply", "not_urgent_processed"):
        return END
    return "triage_agent"


def route_after_triage(state: EmailState) -> str:
    urgency = state.get("urgency_result", "")
    return "draft_agent" if urgency.startswith("urgent") else END


# ===============================
# Build Multi-Agent Workflow
# ===============================

def create_multiagent_workflow():
    workflow = StateGraph(EmailState)

    workflow.add_node("calendar_agent", calendar_agent_node)
    workflow.add_node("confirmation_agent", confirmation_agent_node)
    workflow.add_node("triage_agent", triage_agent_node)
    workflow.add_node("draft_agent", drafting_agent_node)

    workflow.set_entry_point("calendar_agent")

    workflow.add_conditional_edges("calendar_agent", route_after_calendar, {
        "confirmation_agent": "confirmation_agent",
        END: END
    })

    workflow.add_conditional_edges("confirmation_agent", route_after_confirmation, {
        "triage_agent": "triage_agent",
        END: END
    })

    workflow.add_conditional_edges("triage_agent", route_after_triage, {
        "draft_agent": "draft_agent",
        END: END
    })

    workflow.add_edge("draft_agent", END)

    return workflow.compile()


# ===============================
# Main
# ===============================
if __name__ == "__main__":
    app = create_multiagent_workflow()

    creds = get_credentials()
    gmail_service = get_gmail_service(creds)
    calendar_service = get_calendar_service(creds)

    # Fetch user's calendar timezone once
    user_tz_str, user_tzinfo = get_user_timezone(calendar_service)

    emails = get_emails(gmail_service)
    _log_main("start", count=len(emails))

    for email in emails:
        _log_main("processing_email", subject=email.get('subject'), id=email.get('id'))

        initial_state: EmailState = {
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

        try:
            final_state = app.invoke(initial_state)
            _log_main("final_action", action=final_state.get('action_taken', 'none'), email_id=email.get('id'))
        except Exception as e:
            _log_main("error", level="error", error=str(e), email_id=email.get('id'))

    _log_main("done")
