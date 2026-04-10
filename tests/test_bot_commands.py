"""
Unit tests for Telegram bot command handlers.

Uses mocked API client (bot.client functions) to test handler logic
without real HTTP calls or a Telegram connection.

Covers:
- /call: happy path, missing phone arg, API error
- /active: empty list, non-empty list, API error
- /listen: happy path, missing arg, 404 from API
- /steer: happy path, missing args, 404, 422
- /stop: happy path, missing arg, 404
"""
from __future__ import annotations

from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_message(text: str, user_id: int = 123456) -> MagicMock:
    """Build a minimal aiogram Message mock."""
    msg = MagicMock()
    msg.text = text
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.answer = AsyncMock()
    return msg


def _stub_call(call_id: str = "abc-123", phone: str = "+79991234567") -> dict:
    return {
        "id": call_id,
        "phone": phone,
        "status": "QUEUED",
        "mode": "auto",
        "vapi_call_id": None,
        "mango_call_id": None,
        "created_at": "2026-04-01T12:00:00Z",
        "completed_at": None,
        "summary": None,
        "sentiment": None,
        "transcript_entries": [],
    }


def _stub_card(call_id: str = "abc-123") -> dict:
    return {
        "id": call_id,
        "phone": "+79991234567",
        "status": "IN_PROGRESS",
        "mode": "auto",
        "is_active": True,
        "duration_seconds": 30,
        "summary": None,
        "sentiment": None,
        "last_instruction": None,
        "transcript_tail": [],
        "created_at": "2026-04-01T12:00:00Z",
        "completed_at": None,
    }


# ── /call ─────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_cmd_call_happy_path():
    """/call +79991234567 → creates call, shows card."""
    from bot.handlers.commands import cmd_call

    msg = _make_message("/call +79991234567")

    with patch("bot.handlers.commands.create_call", new=AsyncMock(return_value=_stub_call())), \
         patch("bot.handlers.commands.get_call_card", new=AsyncMock(return_value=_stub_card())):
        await cmd_call(msg)

    msg.answer.assert_called_once()
    call_args = msg.answer.call_args
    assert call_args is not None


@pytest.mark.anyio
async def test_cmd_call_no_phone():
    """/call (no arg) → asks for phone number."""
    from bot.handlers.commands import cmd_call

    msg = _make_message("/call")
    await cmd_call(msg)

    msg.answer.assert_called_once()
    assert "номер" in msg.answer.call_args[0][0].lower() or "code" in msg.answer.call_args[0][0].lower()


@pytest.mark.anyio
async def test_cmd_call_api_error():
    """/call with API error → shows error message."""
    from bot.client import ApiError
    from bot.handlers.commands import cmd_call

    msg = _make_message("/call +79991234567")
    with patch(
        "bot.handlers.commands.create_call",
        new=AsyncMock(side_effect=ApiError(422, "Invalid phone")),
    ):
        await cmd_call(msg)

    msg.answer.assert_called_once()
    assert "❌" in msg.answer.call_args[0][0]


# ── /active ───────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_cmd_active_empty():
    """/active with no calls → 'no active calls' message."""
    from bot.handlers.commands import cmd_active

    msg = _make_message("/active")
    with patch(
        "bot.handlers.commands.get_active_calls",
        new=AsyncMock(return_value={"items": [], "total": 0}),
    ):
        await cmd_active(msg)

    msg.answer.assert_called_once()
    assert "нет" in msg.answer.call_args[0][0].lower()


@pytest.mark.anyio
async def test_cmd_active_with_calls():
    """/active with 2 calls → shows list."""
    from bot.handlers.commands import cmd_active

    msg = _make_message("/active")
    calls = [
        {"id": "id1", "phone": "+79991234561", "status": "IN_PROGRESS"},
        {"id": "id2", "phone": "+79991234562", "status": "RINGING"},
    ]
    with patch(
        "bot.handlers.commands.get_active_calls",
        new=AsyncMock(return_value={"items": calls, "total": 2}),
    ):
        await cmd_active(msg)

    msg.answer.assert_called_once()
    text = msg.answer.call_args[0][0]
    assert "2" in text


@pytest.mark.anyio
async def test_cmd_active_api_error():
    """/active with API error → shows error."""
    from bot.client import ApiError
    from bot.handlers.commands import cmd_active

    msg = _make_message("/active")
    with patch(
        "bot.handlers.commands.get_active_calls",
        new=AsyncMock(side_effect=ApiError(503, "Service down")),
    ):
        await cmd_active(msg)

    assert "❌" in msg.answer.call_args[0][0]


# ── /listen ───────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_cmd_listen_happy_path():
    """/listen <id> → shows card."""
    from bot.handlers.commands import cmd_listen

    msg = _make_message("/listen abc-123")
    with patch("bot.handlers.commands.get_call_card", new=AsyncMock(return_value=_stub_card())):
        await cmd_listen(msg)

    msg.answer.assert_called_once()


@pytest.mark.anyio
async def test_cmd_listen_no_arg():
    """/listen (no arg) → usage hint."""
    from bot.handlers.commands import cmd_listen

    msg = _make_message("/listen")
    await cmd_listen(msg)
    msg.answer.assert_called_once()
    assert "call_id" in msg.answer.call_args[0][0].lower() or "code" in msg.answer.call_args[0][0]


@pytest.mark.anyio
async def test_cmd_listen_not_found():
    """/listen <unknown_id> → 404 message."""
    from bot.client import ApiError
    from bot.handlers.commands import cmd_listen

    msg = _make_message("/listen unknown-id")
    with patch(
        "bot.handlers.commands.get_call_card",
        new=AsyncMock(side_effect=ApiError(404, "Not found")),
    ):
        await cmd_listen(msg)

    assert "не найден" in msg.answer.call_args[0][0].lower()


# ── /steer ────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_cmd_steer_happy_path():
    """/steer <id> <instruction> → sends instruction, shows updated card."""
    from bot.handlers.commands import cmd_steer

    msg = _make_message("/steer abc-123 Спроси про бюджет")
    with patch("bot.handlers.commands.steer_call", new=AsyncMock(return_value={})), \
         patch("bot.handlers.commands.get_call_card", new=AsyncMock(return_value=_stub_card())):
        await cmd_steer(msg)

    msg.answer.assert_called_once()


@pytest.mark.anyio
async def test_cmd_steer_missing_args():
    """/steer (missing call_id or instruction) → usage hint."""
    from bot.handlers.commands import cmd_steer

    msg = _make_message("/steer")
    await cmd_steer(msg)
    msg.answer.assert_called_once()


@pytest.mark.anyio
async def test_cmd_steer_not_found():
    """/steer on unknown call → 404 message."""
    from bot.client import ApiError
    from bot.handlers.commands import cmd_steer

    msg = _make_message("/steer unknown-id test instruction")
    with patch(
        "bot.handlers.commands.steer_call",
        new=AsyncMock(side_effect=ApiError(404, "Not found")),
    ):
        await cmd_steer(msg)

    assert "не найден" in msg.answer.call_args[0][0].lower()


@pytest.mark.anyio
async def test_cmd_steer_terminal_call():
    """/steer on terminal call (422) → error message."""
    from bot.client import ApiError
    from bot.handlers.commands import cmd_steer

    msg = _make_message("/steer abc-123 too late")
    with patch(
        "bot.handlers.commands.steer_call",
        new=AsyncMock(side_effect=ApiError(422, "Call is terminal")),
    ):
        await cmd_steer(msg)

    assert "❌" in msg.answer.call_args[0][0]


# ── /stop ─────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_cmd_stop_happy_path():
    """/stop <id> → stops call, shows updated card."""
    from bot.handlers.commands import cmd_stop

    msg = _make_message("/stop abc-123")
    stopped_card = {**_stub_card(), "status": "STOPPED", "is_active": False}
    with patch("bot.handlers.commands.stop_call", new=AsyncMock(return_value={})), \
         patch("bot.handlers.commands.get_call_card", new=AsyncMock(return_value=stopped_card)):
        await cmd_stop(msg)

    msg.answer.assert_called_once()


@pytest.mark.anyio
async def test_cmd_stop_no_arg():
    """/stop (no arg) → usage hint."""
    from bot.handlers.commands import cmd_stop

    msg = _make_message("/stop")
    await cmd_stop(msg)
    msg.answer.assert_called_once()


@pytest.mark.anyio
async def test_cmd_stop_not_found():
    """/stop on unknown call → 404 message."""
    from bot.client import ApiError
    from bot.handlers.commands import cmd_stop

    msg = _make_message("/stop unknown-id")
    with patch(
        "bot.handlers.commands.stop_call",
        new=AsyncMock(side_effect=ApiError(404, "Not found")),
    ):
        await cmd_stop(msg)

    assert "не найден" in msg.answer.call_args[0][0].lower()
