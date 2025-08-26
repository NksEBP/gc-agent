"""Tests for meeting confirmation functionality."""
import os
import sys
from pathlib import Path

# Add the project root to the Python path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import patch, MagicMock, call
from datetime import datetime, timedelta, timezone
from main import meeting_confirmation_node, is_meeting_confirmation_reply, extract_confirmed_meeting_time, create_calendar_event, send_reply, mark_email_as_processed, get_user_timezone

@pytest.fixture
def mock_meeting_dependencies():
    """Mock dependencies for meeting confirmation tests."""
    with patch('main.is_meeting_confirmation_reply', return_value=True) as mock_confirm_reply, \
         patch('main.extract_confirmed_meeting_time') as mock_extract_time, \
         patch('main.create_calendar_event') as mock_create_event, \
         patch('main.send_reply') as mock_send_reply, \
         patch('main.mark_email_as_processed') as mock_mark_processed, \
         patch('main.get_user_timezone') as mock_get_timezone:
        
        # Default mock values
        mock_extract_time.return_value = datetime.now(timezone.utc).replace(hour=14, minute=0) + timedelta(days=1)
        mock_create_event.return_value = {'id': 'event123', 'htmlLink': 'http://example.com/event/123'}
        mock_get_timezone.return_value = ('Asia/Kathmandu', timezone(timedelta(hours=5, minutes=45)))
        
        yield {
            'confirm_reply': mock_confirm_reply,
            'extract_time': mock_extract_time,
            'create_event': mock_create_event,
            'send_reply': mock_send_reply,
            'mark_processed': mock_mark_processed,
            'get_timezone': mock_get_timezone
        }

@pytest.fixture
def create_meeting_state():
    """Create a test state for meeting confirmation tests."""
    def _create_meeting_state(conflict=False, email_content=None, datetime_detected=None):
        # Create mock calendar service
        mock_calendar = MagicMock()
        mock_events = MagicMock()
        mock_list = MagicMock()
        
        mock_calendar.events.return_value = mock_events
        mock_events.list.return_value = mock_list
        
        if conflict:
            mock_list.execute.return_value = {
                'items': [{
                    'start': {'dateTime': '2025-08-27T14:00:00+05:45'},
                    'end': {'dateTime': '2025-08-27T15:00:00+05:45'},
                    'summary': 'Existing Meeting'
                }]
            }
        else:
            mock_list.execute.return_value = {'items': []}
        
        # Default email content
        if email_content is None:
            email_content = {
                'id': 'test-email-123',
                'threadId': 't-test-1',
                'subject': 'Test Meeting',
                'from': 'test@example.com',
                'body': 'Let\'s meet tomorrow at 2 PM',
                'to': 'me@example.com'
            }
        
        # Create test state
        state = {
            'email': email_content,
            'calendar_events': [],
            'calendar_service': mock_calendar,
            'gmail_service': MagicMock(),
            'service': MagicMock(),
            'user_tz_str': 'Asia/Kathmandu',
            'user_tzinfo': timezone(timedelta(hours=5, minutes=45)),
            'counters': {'booked': 0, 'suggested': 0, 'drafted': 0, 'processed': 0},
            'action_taken': '',
            'meeting_confirmed': False,
            'messages': []
        }
        
        if datetime_detected:
            state['datetime_detected'] = datetime_detected
            
        if conflict:
            state['calendar_events'] = [{
                'start': {'dateTime': '2025-08-27T14:00:00+05:45'},
                'end': {'dateTime': '2025-08-27T15:00:00+05:45'},
                'summary': 'Existing Meeting'
            }]
            
        return state
    
    return _create_meeting_state

def test_meeting_confirmation_no_conflict(create_meeting_state, mock_meeting_dependencies):
    """Test meeting confirmation with no scheduling conflicts."""
    # Arrange
    state = create_meeting_state(conflict=False)
    
    # Act
    result = meeting_confirmation_node(state)
    
    # Assert
    assert result.get('meeting_confirmed') is True, "Meeting should be confirmed"
    assert result.get('action_taken') == "meeting_confirmed", "Should indicate meeting was confirmed"
    
    # Verify mocks were called as expected
    mock_meeting_dependencies['create_event'].assert_called_once()
    mock_meeting_dependencies['send_reply'].assert_called_once()
    mock_meeting_dependencies['mark_processed'].assert_called_once()

def test_meeting_confirmation_with_conflict(create_meeting_state, mock_meeting_dependencies):
    """Test meeting confirmation when there's a scheduling conflict."""
    # Arrange
    conflict_time = datetime.now(timezone.utc).replace(hour=14, minute=0) + timedelta(days=1)
    mock_meeting_dependencies['extract_time'].return_value = conflict_time
    
    # Create state with conflict
    state = create_meeting_state(conflict=True)
    
    # Act
    result = meeting_confirmation_node(state)
    
    # Assert
    assert result.get('meeting_confirmed') is False, "Meeting should not be confirmed due to conflict"
    assert result.get('action_taken') == "conflict_detected", "Should detect conflict"
    assert 'calendar_events' in result and len(result['calendar_events']) > 0, "Should show conflicting events"
    
    # Verify no calendar event was created for conflicting time
    mock_meeting_dependencies['create_event'].assert_not_called()
    mock_meeting_dependencies['send_reply'].assert_not_called()
    mock_meeting_dependencies['mark_processed'].assert_not_called()

def test_meeting_confirmation_with_custom_email(create_meeting_state, mock_meeting_dependencies):
    """Test meeting confirmation with custom email content."""
    # Arrange
    custom_email = {
        "subject": "Custom Meeting Request",
        "from": "custom@example.com",
        "body": "Can we schedule a meeting for tomorrow at 3 PM?",
        "id": "custom-email-123",
        "threadId": "t-custom-1"
    }
    state = create_meeting_state(conflict=False, email_content=custom_email)
    
    # Act
    result = meeting_confirmation_node(state)
    
    # Assert
    assert result.get('meeting_confirmed') is True, "Meeting should be confirmed with custom email"
    assert result.get('action_taken') == "meeting_confirmed"
    assert result['email']['from'] == "custom@example.com"
    assert result['email']['subject'] == "Custom Meeting Request"

def test_meeting_confirmation_with_custom_datetime(create_meeting_state, mock_meeting_dependencies):
    """Test meeting confirmation with a custom date and time."""
    # Arrange
    custom_time = datetime(2025, 9, 1, 14, 30, tzinfo=timezone.utc)
    mock_meeting_dependencies['extract_time'].return_value = custom_time
    
    custom_email = {
        "subject": "Meeting with Custom Time",
        "from": "datetime@example.com",
        "body": f"Let's meet on {custom_time.strftime('%Y-%m-%d at %H:%M')} for our discussion.",
        "id": "datetime-email-123",
        "threadId": "t-datetime-1"
    }
    
    state = create_meeting_state(
        conflict=False,
        email_content=custom_email,
        datetime_detected=custom_time.isoformat()
    )
    
    # Act
    result = meeting_confirmation_node(state)
    
    # Assert
    assert result.get('meeting_confirmed') is True, "Meeting should be confirmed with custom datetime"
    assert result.get('action_taken') == "meeting_confirmed"
    assert 'datetime_detected' in state, "Should have datetime_detected in state"
    
    # Verify the event was created with the correct time
    args, kwargs = mock_meeting_dependencies['create_event'].call_args
    assert args[1] == custom_time, "Should create event with the specified time"

def create_test_state(conflict=False, email_content=None, datetime_detected=None):
    """Helper to create test state with optional conflict"""
    email = email_content or {
        "id": "confirm-123",
        "threadId": "t-confirm-1",
        "subject": "Confirming our meeting",
        "from": "Krishna <krishna@example.com>",
        "body": "Yes, the proposed time works for me. Let's go with the first option at 2:00 PM. Looking forward to our meeting!"
    }
    return {
        "email": email,
        "urgency_result": "urgent",
        "draft_content": "",
        "calendar_result": None,
        "datetime_detected": datetime_detected or ("2025-08-27T14:00:00+05:45" if conflict else "2025-08-27T16:00:00+05:45"),
        "meeting_confirmed": True,
        "action_taken": "",
        "messages": [
            {"role": "assistant", "content": "I've proposed a meeting for 2025-08-27 at 2:00 PM. Please confirm if this works for you."}
        ],
        "processed": False,
        "gmail_service": MockService(),
        "calendar_service": MockService(),
        "user_tz_str": "Asia/Kathmandu",
        "user_tzinfo": timezone(timedelta(hours=5, minutes=45)),
        "counters": {"processed": 0, "booked": 0, "suggested": 0, "drafted": 0},
        "log_seq": 0,
    }

# Run test cases
if __name__ == "__main__":
    # Test 1: Successful booking (no conflict)
    state = create_test_state(conflict=False)
    print(f"\n{'='*50}")
    print(f"TEST: Successful Booking")
    print('-' * 50)
    print(f"Attempting to book: {state['datetime_detected']}")
    new_state = meeting_confirmation_node(state)
    print(f"\nAction: {new_state.get('action_taken')}")
    if new_state.get('draft_content'):
        print("\nDraft Content:")
        print(new_state['draft_content'])
    if "calendar_result" in new_state and new_state["calendar_result"]:
        if isinstance(new_state["calendar_result"], str):
            print("\nCalendar Result:")
            print(new_state["calendar_result"])
        else:
            print("\nCalendar Event Created:")
            print(f"Start: {new_state['calendar_result'].get('start', {}).get('dateTime')}")
            print(f"End: {new_state['calendar_result'].get('end', {}).get('dateTime')}")

    # Test 2: Conflict scenario
    state = create_test_state(conflict=True)
    print(f"\n{'='*50}")
    print(f"TEST: Conflict Scenario")
    print('-' * 50)
    print(f"Attempting to book: {state['datetime_detected']}")
    print("This time has a conflict with an existing meeting")
    new_state = meeting_confirmation_node(state)
    print(f"\nAction: {new_state.get('action_taken')}")
    if new_state.get('draft_content'):
        print("\nDraft Content:")
        print(new_state['draft_content'])
    if "calendar_result" in new_state and new_state["calendar_result"]:
        if isinstance(new_state["calendar_result"], str):
            print("\nCalendar Result:")
            print(new_state["calendar_result"])
        else:
            print("\nCalendar Event Created:")
            print(f"Start: {new_state['calendar_result'].get('start', {}).get('dateTime')}")
            print(f"End: {new_state['calendar_result'].get('end', {}).get('dateTime')}")
