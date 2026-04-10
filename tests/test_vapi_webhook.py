"""
Tests for Vapi webhook handling and transcript storage.

These tests use SQLite in-memory (via conftest) so they run without
a real Postgres or Vapi account.
"""

import hashlib
import hmac
import json
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.call import Call, CallMode, CallStatus
from app.models.transcript import TranscriptEntry, TranscriptRole
from app.repositories.call_repo import CallRepository
from app.repositories.transcript_repo import TranscriptRepository
from app.services.call_service import CallService


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_webhook_payload(event_type: str, vapi_call_id: str, **extra) -> dict:
    """Build a minimal Vapi webhook payload."""
    message = {"type": event_type, "call": {"id": vapi_call_id}, **extra}
    return {"message": message}


async def _create_call_with_vapi_id(
    session: AsyncSession, vapi_id: str
) -> Call:
    """Insert a Call into the DB with a known vapi_call_id for webhook correlation."""
    call = Call(
        phone="+79991234567",
        mode=CallMode.VAPI,
        status=CallStatus.IN_PROGRESS,
        vapi_call_id=vapi_id,
    )
    repo = CallRepository(Call, session)
    await repo.save(call)
    return call


# ── Webhook endpoint tests ─────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_webhook_returns_200_for_unknown_event(client: AsyncClient) -> None:
    """Unknown event types must not crash the system."""
    payload = {"message": {"type": "some-future-event-type", "call": {"id": "vapi-123"}}}
    resp = await client.post("/webhooks/vapi", json=payload)
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.anyio
async def test_webhook_status_update_ringing(
    client: AsyncClient, session: AsyncSession
) -> None:
    """status-update(ringing) should move call to RINGING."""
    vapi_id = f"vapi-{uuid.uuid4()}"
    call = await _create_call_with_vapi_id(session, vapi_id)
    call.status = CallStatus.QUEUED
    await session.commit()

    payload = _make_webhook_payload("status-update", vapi_id, status="ringing")
    resp = await client.post("/webhooks/vapi", json=payload)
    assert resp.status_code == 200

    # Refresh from DB
    await session.refresh(call)
    assert call.status == CallStatus.RINGING


@pytest.mark.anyio
async def test_webhook_status_update_in_progress(
    client: AsyncClient, session: AsyncSession
) -> None:
    vapi_id = f"vapi-{uuid.uuid4()}"
    call = await _create_call_with_vapi_id(session, vapi_id)
    call.status = CallStatus.RINGING
    await session.commit()

    payload = _make_webhook_payload("status-update", vapi_id, status="in-progress")
    resp = await client.post("/webhooks/vapi", json=payload)
    assert resp.status_code == 200

    await session.refresh(call)
    assert call.status == CallStatus.IN_PROGRESS


@pytest.mark.anyio
async def test_webhook_transcript_saved(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Transcript events should create TranscriptEntry rows."""
    vapi_id = f"vapi-{uuid.uuid4()}"
    call = await _create_call_with_vapi_id(session, vapi_id)
    await session.commit()

    payload = _make_webhook_payload(
        "transcript",
        vapi_id,
        role="assistant",
        transcript="Hello, how can I help you today?",
        transcriptType="final",
    )
    resp = await client.post("/webhooks/vapi", json=payload)
    assert resp.status_code == 200

    # Check transcript was saved
    repo = TranscriptRepository(TranscriptEntry, session)
    entries = await repo.get_by_call(call.id)
    assert len(entries) == 1
    assert entries[0].role == TranscriptRole.ASSISTANT
    assert "Hello" in entries[0].text


@pytest.mark.anyio
async def test_webhook_partial_transcript_not_saved(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Partial transcript chunks should be ignored (only save 'final')."""
    vapi_id = f"vapi-{uuid.uuid4()}"
    call = await _create_call_with_vapi_id(session, vapi_id)
    await session.commit()

    payload = _make_webhook_payload(
        "transcript",
        vapi_id,
        role="user",
        transcript="Hel...",
        transcriptType="partial",
    )
    resp = await client.post("/webhooks/vapi", json=payload)
    assert resp.status_code == 200

    repo = TranscriptRepository(TranscriptEntry, session)
    entries = await repo.get_by_call(call.id)
    assert len(entries) == 0  # partial not saved


@pytest.mark.anyio
async def test_webhook_end_of_call_report_completed(
    client: AsyncClient, session: AsyncSession
) -> None:
    """end-of-call-report with normal endedReason should mark call COMPLETED."""
    vapi_id = f"vapi-{uuid.uuid4()}"
    call = await _create_call_with_vapi_id(session, vapi_id)
    await session.commit()

    payload = _make_webhook_payload(
        "end-of-call-report",
        vapi_id,
        endedReason="hangup",
        summary="Client was interested in the product",
        analysis={"successEvaluation": "positive"},
        messages=[
            {"role": "assistant", "message": "Hello!"},
            {"role": "user", "message": "Hi there"},
        ],
    )
    resp = await client.post("/webhooks/vapi", json=payload)
    assert resp.status_code == 200

    await session.refresh(call)
    assert call.status == CallStatus.COMPLETED
    assert call.summary == "Client was interested in the product"
    assert call.sentiment == "positive"
    assert call.completed_at is not None


@pytest.mark.anyio
async def test_webhook_end_of_call_report_failed(
    client: AsyncClient, session: AsyncSession
) -> None:
    """end-of-call-report with error endedReason should mark call FAILED."""
    vapi_id = f"vapi-{uuid.uuid4()}"
    call = await _create_call_with_vapi_id(session, vapi_id)
    await session.commit()

    payload = _make_webhook_payload(
        "end-of-call-report",
        vapi_id,
        endedReason="error",
    )
    resp = await client.post("/webhooks/vapi", json=payload)
    assert resp.status_code == 200

    await session.refresh(call)
    assert call.status == CallStatus.FAILED


@pytest.mark.anyio
async def test_webhook_transfer_request_sets_needs_transfer(
    client: AsyncClient, session: AsyncSession
) -> None:
    vapi_id = f"vapi-{uuid.uuid4()}"
    call = await _create_call_with_vapi_id(session, vapi_id)
    await session.commit()

    payload = _make_webhook_payload("transfer-destination-request", vapi_id)
    resp = await client.post("/webhooks/vapi", json=payload)
    assert resp.status_code == 200

    await session.refresh(call)
    assert call.status == CallStatus.NEEDS_TRANSFER


@pytest.mark.anyio
async def test_webhook_unknown_vapi_id_does_not_crash(
    client: AsyncClient,
) -> None:
    """Events for unknown Vapi IDs should be logged and stored, not crash."""
    payload = _make_webhook_payload(
        "transcript",
        "nonexistent-vapi-id",
        role="user",
        transcript="Hello",
        transcriptType="final",
    )
    resp = await client.post("/webhooks/vapi", json=payload)
    assert resp.status_code == 200  # System must not crash


@pytest.mark.anyio
async def test_get_call_includes_transcript_entries(
    client: AsyncClient, session: AsyncSession
) -> None:
    """GET /calls/{id} should return transcript_entries from DB."""
    # Create call + transcript entries directly
    call = Call(phone="+79991234567", mode=CallMode.VAPI, status=CallStatus.COMPLETED)
    call_repo = CallRepository(Call, session)
    await call_repo.save(call)

    transcript_repo = TranscriptRepository(TranscriptEntry, session)
    await transcript_repo.append(call.id, TranscriptRole.ASSISTANT, "Hello!")
    await transcript_repo.append(call.id, TranscriptRole.USER, "Hi there")
    await session.commit()

    resp = await client.get(f"/v1/calls/{call.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["transcript_entries"]) == 2
    assert data["transcript_entries"][0]["role"] == "assistant"
    assert data["transcript_entries"][0]["text"] == "Hello!"
    assert data["transcript_entries"][1]["role"] == "user"


@pytest.mark.anyio
async def test_transcript_sequence_ordering(session: AsyncSession) -> None:
    """Transcript entries should be ordered by sequence_num."""
    call = Call(phone="+79991234567", mode=CallMode.STUB if hasattr(CallMode, 'STUB') else CallMode.AUTO, status=CallStatus.IN_PROGRESS)
    call_repo = CallRepository(Call, session)
    await call_repo.save(call)

    repo = TranscriptRepository(TranscriptEntry, session)
    await repo.append(call.id, TranscriptRole.ASSISTANT, "First")
    await repo.append(call.id, TranscriptRole.USER, "Second")
    await repo.append(call.id, TranscriptRole.ASSISTANT, "Third")
    await session.commit()

    entries = await repo.get_by_call(call.id)
    assert [e.text for e in entries] == ["First", "Second", "Third"]
    assert [e.sequence_num for e in entries] == [0, 1, 2]


# ── Webhook signature security tests ────────────────────────────────────────

@pytest.mark.anyio
async def test_webhook_missing_signature_rejected_when_secret_configured(
    client: AsyncClient,
) -> None:
    """
    When VAPI_WEBHOOK_SECRET is set and request has no X-Vapi-Signature header,
    endpoint must return 401 (not process the event silently).
    """
    from unittest.mock import patch
    payload = {"message": {"type": "status-update", "call": {"id": "test-id"}}}

    with patch.object(settings, "vapi_webhook_secret", "test-secret-value"):
        resp = await client.post(
            "/webhooks/vapi",
            json=payload,
            # No X-Vapi-Signature header
        )

    assert resp.status_code == 401
    assert resp.json()["error"] == "missing_signature"


@pytest.mark.anyio
async def test_webhook_invalid_signature_rejected(client: AsyncClient) -> None:
    """Invalid signature with secret configured → 401."""
    from unittest.mock import patch
    payload = {"message": {"type": "status-update", "call": {"id": "test-id"}}}

    with patch.object(settings, "vapi_webhook_secret", "test-secret-value"):
        resp = await client.post(
            "/webhooks/vapi",
            json=payload,
            headers={"X-Vapi-Signature": "sha256=badhash"},
        )

    assert resp.status_code == 401
    assert resp.json()["error"] == "invalid_signature"


@pytest.mark.anyio
async def test_webhook_missing_signature_allowed_when_no_secret(
    client: AsyncClient,
) -> None:
    """
    When VAPI_WEBHOOK_SECRET is NOT set, requests without signature pass through.
    This preserves backward compatibility for deployments without signature validation.
    """
    from unittest.mock import patch
    payload = {"message": {"type": "status-update", "call": {"id": "no-such-call"}}}

    with patch.object(settings, "vapi_webhook_secret", ""):
        resp = await client.post(
            "/webhooks/vapi",
            json=payload,
            # No X-Vapi-Signature header — should be OK without secret
        )

    assert resp.status_code == 200
