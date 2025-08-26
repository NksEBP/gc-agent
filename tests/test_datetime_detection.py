"""Tests for datetime detection functionality."""
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

# Add project root to Python path
sys.path.insert(0, str(Path(__file__).parent.parent))

from main import datetime_detection_node, EmailState

# Ensure policy is loaded
os.environ["POLICY_DIR"] = str(Path(__file__).parent.parent / "policies")
os.environ["RAG_TOP_K"] = "2"
os.environ["EMBEDDING_MODEL"] = "text-embedding-3-small"

# Mock services for testing
class MockCalendar:
    def events(self): 
        return self
    def list(self, **kwargs):
        # Return empty list to simulate no conflicts
        return type("Req", (), {"execute": lambda self: {"items": []}})()
    def insert(self, **kwargs):
        return type("Req", (), {"execute": lambda self: {
            "id": "test-event-123",
            "htmlLink": "https://calendar.google.com/event/test123",
            "start": {"dateTime": "2025-08-27T14:00:00+05:45"},
            "end": {"dateTime": "2025-08-27T15:00:00+05:45"}
        }})()

class MockService:
    def events(self): 
        return MockCalendar()

def run_datetime_test(test_name, email_subject, email_body, expected_datetime_str=None):
    """Run a single datetime detection test case"""
    # Test state for datetime detection
    state: EmailState = {
        "email": {
            "id": f"test-{test_name.lower().replace(' ', '-')}",
            "threadId": f"t-dt-{test_name.lower().replace(' ', '-')}",
            "subject": email_subject,
            "from": "Test User <test@example.com>",
            "body": email_body
        },
        "urgency_result": "urgent",
        "draft_content": "",
        "calendar_result": "",
        "datetime_detected": None,
        "meeting_confirmed": False,
        "action_taken": "",
        "messages": [],
        "processed": False,
        "gmail_service": None,
        "calendar_service": MockService(),
        "user_tz_str": "Asia/Kathmandu",
        "user_tzinfo": timezone(timedelta(hours=5, minutes=45)),
        "counters": {"processed": 0, "booked": 0, "suggested": 0, "drafted": 0},
        "log_seq": 0,
    }

    # Run the test
    new_state = datetime_detection_node(state)
    
    # Print results
    print(f"\n{'='*50}")
    print(f"Test: {test_name}")
    print("-" * 50)
    print(f"Email: {email_subject}")
    print(f"Body: {email_body[:100]}...")
    
    datetime_detected = new_state.get("datetime_detected")
    if datetime_detected:
        if isinstance(datetime_detected, str):
            datetime_detected = datetime.fromisoformat(datetime_detected)
        print(f"\nDetected DateTime: {datetime_detected}")
    else:
        print("\nNo datetime detected")
    
    print(f"Action Taken: {new_state.get('action_taken', 'None')}")
    if new_state.get('draft_content'):
        print("\nDraft Content:")
        print(new_state['draft_content'])
    
    return new_state

# Test Functions
def test_specific_date_time():
    """Test detection of specific date and time."""
    result = run_datetime_test(
        "Specific Date and Time",
        "Meeting Request",
        "Can we schedule a meeting on August 30th at 2:30 PM?"
    )
    assert result.get("datetime_detected") is not None, "Should detect specific datetime"

def test_relative_time():
    """Test detection of relative time expressions."""
    result = run_datetime_test(
        "Relative Time",
        "Quick Call",
        "Let's have a call tomorrow at 11 AM to discuss the project."
    )
    
    # Test 3: Multiple time options
    run_datetime_test(
        "Multiple Time Options",
        "Project Discussion",
        "I'm available on Monday at 10 AM, Tuesday at 2 PM, or Wednesday at 4 PM. Which works best for you?"
    )
    
    # Test 4: No specific time
    run_datetime_test(
        "No Specific Time",
        "General Inquiry",
        "I'd like to discuss the project. Please let me know when you're available."
    )
    
    # Test 5: Time range
    run_datetime_test(
        "Time Range",
        "Team Meeting",
        "Let's schedule a team meeting from 3 PM to 4 PM next Monday."
    )
