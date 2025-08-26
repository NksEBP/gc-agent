"""Tests for urgency analysis functionality."""
import os
import sys
from pathlib import Path

# Add the project root to the Python path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from main import urgency_analysis_node, EmailState

# Use pytest fixtures from conftest.py

def run_urgency_test(create_test_state, email_subject, email_body, expected_urgency, from_email="test@example.com"):
    """Helper function to run urgency analysis test"""
    # Arrange
    state = create_test_state(email_content={
        "subject": email_subject, 
        "body": email_body,
        "from": from_email
    }, urgency_level=expected_urgency)
    
    # Act
    new_state = urgency_analysis_node(state)
    
    # Print results
    print(f"\n{'='*50}")
    print(f"Test: {email_subject}")
    print("-" * 50)
    print(f"Expected Urgency: {expected_urgency}")
    print(f"Detected Urgency: {new_state['urgency_result']}")
    print(f"Action Taken: {new_state.get('action_taken', 'None')}")
    if new_state.get('draft_content'):
        print("\nDraft Content:")
        print(new_state['draft_content'])
    
    return new_state

def test_urgent_email(create_test_state):
    """Test that urgent emails are properly identified."""
    new_state = run_urgency_test(
        create_test_state,
        "URGENT: Server Down",
        "Our production server is down and we're losing thousands of dollars every minute. "
        "Please help us fix this immediately!",
        "urgent"
    )
    assert new_state['urgency_result'] == "urgent"

def test_non_urgent_email(create_test_state):
    """Test that non-urgent emails are properly identified."""
    new_state = run_urgency_test(
        create_test_state,
        "Follow up on our meeting",
        "Hi, I was just following up on our discussion last week. "
        "Let me know when you have a chance to review the proposal.",
        "not urgent"
    )
    assert new_state['urgency_result'] == "not urgent"

def test_time_sensitive_email(create_test_state):
    """Test that time-sensitive emails are properly identified."""
    new_state = run_urgency_test(
        create_test_state,
        "Meeting Request for Next Week",
        "Would you be available for a quick call next Tuesday at 2 PM "
        "to discuss the project timeline?",
        "not urgent"
    )
    assert new_state['urgency_result'] == "not urgent"

def test_high_priority_email(create_test_state):
    """Test that high priority emails are properly identified."""
    new_state = run_urgency_test(
        create_test_state,
        "IMPORTANT: Action Required - Security Update",
        "Please complete the mandatory security training by the end of this week. "
        "This is required for compliance.",
        "urgent"
    )
    assert new_state['urgency_result'] == "urgent"
