"""
StubTransferEngine — development and testing no-op implementation.

Behaviour:
  - initiate_manager_call: logs the attempt, returns a synthetic external_id
  - play_whisper: logs the whisper text
  - bridge_calls: logs both call leg IDs
  - mark_manager_temporarily_unavailable: logs, does NOT persist to DB

The stub simulates an immediate manager answer — no timeouts, no retries.
This is intentional so that unit tests run synchronously without asyncio.sleep.

For testing "manager doesn't answer" scenarios, use FailingTransferEngine
defined in tests/conftest.py, which raises TransferError from initiate_manager_call.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from app.core.logging import get_logger
from app.integrations.transfer_engine.base import AbstractTransferEngine, ManagerCallResult
from app.models.transfer import TransferStatus

if TYPE_CHECKING:
    from app.models.call import Call
    from app.models.manager import Manager

log = get_logger(__name__)


class StubTransferEngine(AbstractTransferEngine):
    """No-op transfer engine. Every call succeeds immediately."""

    async def initiate_manager_call(
        self,
        manager: "Manager",
        call: "Call",
        whisper_text: str,
    ) -> ManagerCallResult:
        stub_id = f"stub-mgr-{manager.id}-{call.id}"
        log.info(
            "stub_transfer.initiate_manager_call",
            manager_id=str(manager.id),
            manager_name=manager.name,
            manager_phone=manager.phone,
            call_id=str(call.id),
            whisper_preview=whisper_text[:60] if whisper_text else "",
            stub_id=stub_id,
        )
        return ManagerCallResult(
            external_id=stub_id,
            status=TransferStatus.CALLING_MANAGER.value,
            metadata={"engine": "stub"},
        )

    async def play_whisper(
        self,
        manager_call_id: str,
        whisper_text: str,
    ) -> None:
        log.info(
            "stub_transfer.play_whisper",
            manager_call_id=manager_call_id,
            whisper_text=whisper_text,
            char_count=len(whisper_text),
        )

    async def bridge_calls(
        self,
        manager_call_id: str,
        customer_call_id: str,
    ) -> None:
        log.info(
            "stub_transfer.bridge_calls",
            manager_call_id=manager_call_id,
            customer_call_id=customer_call_id,
        )

    async def mark_manager_temporarily_unavailable(
        self,
        manager_id: uuid.UUID,
    ) -> None:
        # Stub: log only. Real engines would persist is_available=False
        # and schedule a Celery task to restore after cooldown.
        log.info(
            "stub_transfer.mark_unavailable",
            manager_id=str(manager_id),
            note="stub does not persist availability changes",
        )

    async def terminate_manager_call(self, manager_call_id: str) -> None:
        log.info(
            "stub_transfer.terminate_manager_call",
            manager_call_id=manager_call_id,
            note="stub no-op",
        )
