"""Tests for the alternative times email generation."""
import os
import sys
from pathlib import Path

# Add the project root to the Python path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from datetime import datetime, timedelta, timezone
from main import generate_alternative_times_email, find_next_available_slots, EmailState

@pytest.fixture
def sample_email():
    """Sample email data for testing."""
    return {
        "id": "test123",
        "threadId": "thread123",
        "subject": "Test Meeting Request",
        "from": "test@example.com",
        "body": "Can we schedule a meeting tomorrow at 2 PM?"
    }

def test_alternative_times_email(mock_calendar_service, test_time, sample_email):
    """Test generating alternative time suggestions email."""
    # Set up test data
    requested_time = test_time + timedelta(days=1, hours=14)  # Tomorrow at 2 PM
    
    # Generate alternative time slots
    alternative_slots = [
        requested_time + timedelta(minutes=15),  # 2:15 PM
        requested_time + timedelta(minutes=30),  # 2:30 PM
        requested_time + timedelta(hours=1)      # 3:00 PM
    ]
    
    meeting_title = "Project Discussion"
    
    # Generate the email
    email_content = generate_alternative_times_email(
        original_email=sample_email,
        requested_time=requested_time,
        alternative_slots=alternative_slots,
        meeting_title=meeting_title
    )
    
    # Print results for debugging
    print("\n" + "=" * 80)
    print("TEST: Generate Alternative Times Email")
    print("-" * 80)
    print(f"Requested Time: {requested_time.strftime('%B %d, %Y at %I:%M %p')}")
    print("\nAlternative Time Slots:")
    for i, slot in enumerate(alternative_slots, 1):
        print(f"{i}. {slot.strftime('%A, %B %d at %I:%M %p')}")
    
    print("\nGenerated Email:" + "-" * 40)
    print(email_content)
    
    # Assertions
    assert "apologize" in email_content.lower(), "Email should include an apology"
    assert any(str(slot.hour) in email_content for slot in alternative_slots), \
        "Email should include alternative times"
    assert "let us know" in email_content.lower() or "please let me know" in email_content.lower(), \
        "Email should ask for confirmation"
    
    # Verify the format of alternative times in the email
    for slot in alternative_slots:
        # Check for time in 12-hour format (e.g., "2:15 PM")
        time_str_12h = slot.astimezone(timezone.utc).strftime('%-I:%M %p').lower()
        assert time_str_12h in email_content.lower(), \
            f"Time {time_str_12h} not found in email"

def test_find_next_available_slots(mock_calendar_service, test_time):
    """Test finding next available time slots."""
    # Set up test data
    requested_time = test_time + timedelta(days=1, hours=14)  # Tomorrow at 2 PM
    
    # Call the function
    slots = find_next_available_slots(
        calendar_service=mock_calendar_service,
        requested_time=requested_time,
        duration_minutes=60,
        num_suggestions=3,
        default_tz=timezone.utc
    )
    
    # Assertions
    assert len(slots) == 3, "Should return exactly 3 time slots"
    for slot in slots:
        assert isinstance(slot, datetime), "Each slot should be a datetime object"
        assert slot.tzinfo is not None, "Time slots should be timezone-aware"
