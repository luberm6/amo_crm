"""
Tests for call state transition validation.

Verifies:
- ALLOWED_TRANSITIONS is complete (every status has an entry)
- Terminal statuses have no outgoing transitions
- Active statuses can reach terminal states
- Direct transition STOPPED is reachable from all non-terminal active statuses
"""
from __future__ import annotations

import pytest

from app.models.call import (
    ALLOWED_TRANSITIONS,
    ACTIVE_STATUSES,
    TERMINAL_STATUSES,
    CallStatus,
)


def test_all_statuses_have_transition_entry():
    """Every CallStatus value appears as a key in ALLOWED_TRANSITIONS."""
    for status in CallStatus:
        assert status in ALLOWED_TRANSITIONS, (
            f"CallStatus.{status.name} is missing from ALLOWED_TRANSITIONS"
        )


def test_terminal_statuses_have_no_outgoing_transitions():
    """COMPLETED, FAILED, STOPPED cannot transition to anything."""
    for status in TERMINAL_STATUSES:
        assert ALLOWED_TRANSITIONS[status] == set(), (
            f"Terminal status {status} should have no outgoing transitions"
        )


def test_stopped_reachable_from_all_active_statuses():
    """Every active status can transition to STOPPED (operator can always stop)."""
    for status in ACTIVE_STATUSES:
        assert CallStatus.STOPPED in ALLOWED_TRANSITIONS[status], (
            f"STOPPED must be reachable from active status {status}"
        )


def test_failed_reachable_from_all_active_statuses():
    """Every active status can transition to FAILED (engine/external failure)."""
    for status in ACTIVE_STATUSES:
        assert CallStatus.FAILED in ALLOWED_TRANSITIONS[status], (
            f"FAILED must be reachable from active status {status}"
        )


def test_created_can_go_to_queued():
    """CREATED → QUEUED is a valid initial transition."""
    assert CallStatus.QUEUED in ALLOWED_TRANSITIONS[CallStatus.CREATED]


def test_in_progress_can_go_to_needs_transfer():
    """IN_PROGRESS → NEEDS_TRANSFER is required for warm transfer flow."""
    assert CallStatus.NEEDS_TRANSFER in ALLOWED_TRANSITIONS[CallStatus.IN_PROGRESS]


def test_connected_to_manager_ends_in_completed():
    """CONNECTED_TO_MANAGER → COMPLETED is the happy path for warm transfer."""
    assert CallStatus.COMPLETED in ALLOWED_TRANSITIONS[CallStatus.CONNECTED_TO_MANAGER]


def test_transfer_chain_is_connected():
    """Full warm transfer chain is connected: NEEDS_TRANSFER → ... → COMPLETED."""
    chain = [
        CallStatus.NEEDS_TRANSFER,
        CallStatus.TRANSFERRING,
        CallStatus.MANAGER_BRIEFING,
        CallStatus.CONNECTED_TO_MANAGER,
        CallStatus.COMPLETED,
    ]
    for i in range(len(chain) - 1):
        current = chain[i]
        next_status = chain[i + 1]
        assert next_status in ALLOWED_TRANSITIONS[current], (
            f"{current} → {next_status} is required for warm transfer chain"
        )


def test_no_transition_back_to_created():
    """No status can transition back to CREATED (no rewind)."""
    for status in CallStatus:
        assert CallStatus.CREATED not in ALLOWED_TRANSITIONS[status], (
            f"Cannot transition back to CREATED from {status}"
        )
