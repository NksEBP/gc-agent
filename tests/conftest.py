"""Shared test fixtures and utilities."""
import os
import sys
from pathlib import Path

# Add the project root to the Python path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from datetime import datetime, timedelta, timezone

class MockEventsService:
    """Mock Google Calendar Events service for testing."""
    def list(self, **kwargs):
        """Mock events().list() method."""
        time_min = datetime.fromisoformat(kwargs['timeMin'].replace('Z', '+00:00'))
        time_max = datetime.fromisoformat(kwargs['timeMax'].replace('Z', '+00:00'))
        
        # Mock some busy times (e.g., 1-2pm and 3-4pm on the requested day)
        busy_start1 = time_min.replace(hour=13, minute=0, second=0, microsecond=0)
        busy_end1 = busy_start1 + timedelta(hours=1)
        busy_start2 = busy_start1 + timedelta(hours=2)
        busy_end2 = busy_start2 + timedelta(hours=1)
        
        # Check if current time range overlaps with busy times
        events = []
        for busy_start, busy_end in [(busy_start1, busy_end1), (busy_start2, busy_end2)]:
            if not (time_max <= busy_start or time_min >= busy_end):
                events.append({
                    'start': {'dateTime': busy_start.isoformat()},
                    'end': {'dateTime': busy_end.isoformat()}
                })
        
        # Return an object with an execute() method that returns the events
        class Result:
            def execute(self):
                return {'items': events}
                
        return Result()

class MockCalendarService:
    """Mock Google Calendar service for testing."""
    def __init__(self):
        self.events_service = MockEventsService()
    
    def events(self):
        return self.events_service

@pytest.fixture
def mock_calendar_service():
    """Fixture that provides a mock calendar service."""
    return MockCalendarService()

@pytest.fixture
def test_time():
    """Fixture that provides a fixed test time."""
    return datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)

@pytest.fixture
def original_email():
    """Fixture that provides a sample email."""
    return {
        'from': 'client@example.com',
        'subject': 'Meeting Request',
        'body': 'Hi, can we schedule a meeting for tomorrow at 2 PM?'
    }

@pytest.fixture
def mock_users():
    """Fixture that provides a mock Gmail users service."""
    class MockUsers:
        def messages(self): 
            return self
            
        def get(self, userId, id, format):
            return type("Req", (), {"execute": lambda self: {
                "payload": {"headers": [
                    {"name": "Subject", "value": "Confirming our meeting"},
                    {"name": "From", "value": "test@example.com"}
                ]}
            }})
            
        def drafts(self):
            return self
            
        def create(self, userId, body):
            return type("Req", (), {"execute": lambda self: {"id": "draft123"}})
            
        def send(self, userId, body):
            return type("Req", (), {"execute": lambda self: {"id": "message123"}})
    
    return MockUsers()

@pytest.fixture
def mock_calendar():
    """Fixture that provides a mock Calendar service."""
    class MockCalendar:
        def __init__(self):
            self.conflict_times = [
                {
                    'start': {'dateTime': '2025-08-27T14:00:00+05:45'},
                    'end': {'dateTime': '2025-08-27T15:00:00+05:45'},
                    'summary': 'Existing Meeting'
                }
            ]
            
        def events(self):
            return self
            
        def insert(self, **kwargs):
            return type("Req", (), {
                "execute": lambda self: {"id": "event123"}
            })
            
        def list(self, **kwargs):
            return type("Req", (), {
                "execute": lambda self: {"items": self.conflict_times}
            })
    
    return MockCalendar()

@pytest.fixture
def mock_service(mock_users, mock_calendar):
    """Fixture that provides a mock service with users and calendar."""
    class MockService:
        def users(self):
            return mock_users
            
        def events(self):
            return mock_calendar
    
    return MockService()

@pytest.fixture
def create_test_state():
    """Helper to create test state with optional conflict."""
    def _create_test_state(conflict=False, urgency_level="low", email_content=None):
        if email_content is None:
            email_content = {
                "subject": "Test Meeting Request",
                "body": "Can we meet tomorrow at 2 PM?",
                "from": "test@example.com",
                "to": "me@example.com"
            }
            
        state = {
            "email": {
                "id": f"test-email-{urgency_level}",
                "threadId": f"test-thread-{urgency_level}",
                **email_content
            },
            "calendar_events": [],
            "urgency_result": "",
            "draft_content": "",
            "calendar_result": ""
        }
        
        if conflict:
            state["calendar_events"] = [{
                'start': {'dateTime': '2025-08-27T14:00:00+05:45'},
                'end': {'dateTime': '2025-08-27T15:00:00+05:45'},
                'summary': 'Existing Meeting'
            }]
            
        return state
    
    return _create_test_state

@pytest.fixture
def urgent_email_content():
    """Sample urgent email content."""
    return {
        "subject": "URGENT: Server Down - Immediate Action Required",
        "body": "The production server is down and we're losing $10K per hour. Please fix ASAP!",
        "from": "alerts@example.com"
    }

@pytest.fixture
def normal_email_content():
    """Sample normal priority email content."""
    return {
        "subject": "Weekly Team Meeting",
        "body": "Hi team, just a reminder about our weekly sync tomorrow at 10 AM.",
        "from": "manager@example.com"
    }
