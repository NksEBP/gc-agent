"""Tests for urgent draft email creation."""
import os
import sys
from pathlib import Path
import pytest

# Add project root to Python path
sys.path.insert(0, str(Path(__file__).parent.parent))

from main import draft_creation_node, EmailState

# Ensure policy is loaded
os.environ["POLICY_DIR"] = str(Path(__file__).parent.parent / "policies")
os.environ["RAG_TOP_K"] = "2"
os.environ["EMBEDDING_MODEL"] = "text-embedding-3-small"

# Minimal mock for gmail_service used by create_draft()
class MockUsers:
    def messages(self): 
        return self
        
    def get(self, userId, id, format):
        # Return just enough fields for subject/from/thread id
        return type("Req", (), {"execute": lambda self: {
            "payload": {"headers": [
                {"name": "Subject", "value": "Test Subject"},
                {"name": "From", "value": "Tester <tester@example.com>"}
            ]},
            "threadId": "thread-1"
        }})()
        
    def drafts(self): 
        return self
        
    def create(self, userId, body):
        return type("Req", (), {"execute": lambda self: {"id": "draft-123"}})()
        
    def send(self, userId, body):
        return type("Req", (), {"execute": lambda self: {"id": "msg-123"}})()

class MockService:
    def users(self): 
        return MockUsers()

@pytest.fixture
def test_state():
    """Fixture providing a test state for draft creation."""
    return {
        "email": {
            "id": "123",
            "threadId": "t1",
            "subject": "URGENT: Need help with our account",
            "from": "Client <client@example.com>",
            "body": "Hi, this is urgent. We need assistance today on our account access."
        },
        "urgency_result": "urgent",  # force urgent path
        "draft_content": "",
        "calendar_result": "",
        "datetime_detected": None,
        "meeting_confirmed": False,
        "action_taken": "",
        "messages": [],
        "processed": False,
        "gmail_service": MockService(),
        "calendar_service": None,
        "user_tz_str": "UTC",
        "user_tzinfo": None,
        "counters": {"processed": 0, "booked": 0, "suggested": 0, "drafted": 0},
        "log_seq": 0,
    }

def test_urgent_draft_creation(test_state):
    """Test that urgent drafts are properly created."""
    # Act
    new_state = draft_creation_node(test_state)
    
    # Assert
    assert new_state is not None, "Should return a state dictionary"
    assert "action_taken" in new_state, "Should update action_taken"
    assert "draft_content" in new_state, "Should include draft content"
    assert len(new_state["draft_content"]) > 0, "Draft content should not be empty"
    
    # Print for debugging
    print("\n" + "="*50)
    print("TEST: Urgent Draft Creation")
    print("-"*50)
    print(f"Action: {new_state.get('action_taken')}")
    print(f"Draft:\n{new_state.get('draft_content')}")

def test_draft_creation_without_urgency(test_state):
    """Test draft creation for non-urgent emails."""
    # Arrange
    test_state["urgency_result"] = "not_urgent"
    
    # Act
    new_state = draft_creation_node(test_state)
    
    # Assert
    assert new_state is not None
    assert "action_taken" in new_state
    # Add more assertions based on expected behavior for non-urgent emails