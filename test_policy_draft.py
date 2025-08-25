# file: test_policy_draft.py
import os
from main import draft_creation_node, EmailState

# Ensure policy is loaded
os.environ["POLICY_DIR"] = "policies"
os.environ["RAG_TOP_K"] = "2"
os.environ["EMBEDDING_MODEL"] = "text-embedding-3-small"

# Minimal mock for gmail_service used by create_draft()
class MockUsers:
    def messages(self): return self
    def get(self, userId, id, format):
        # Return just enough fields for subject/from/thread id
        return type("Req", (), {"execute": lambda self: {
            "payload": {"headers": [
                {"name": "Subject", "value": "Test Subject"},
                {"name": "From", "value": "Tester <tester@example.com>"}
            ]},
            "threadId": "thread-1"
        }})()
    def drafts(self): return self
    def create(self, userId, body):
        return type("Req", (), {"execute": lambda self: {}})()
    def send(self, userId, body):
        return type("Req", (), {"execute": lambda self: {}})()

class MockService:
    def users(self): return MockUsers()

state: EmailState = {
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

new_state = draft_creation_node(state)
print("Action:", new_state.get("action_taken"))
print("Draft:\n", new_state.get("draft_content"))