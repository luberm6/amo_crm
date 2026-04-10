from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import asyncio
import uuid
from typing import TYPE_CHECKING, Optional

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import settings
from app.core.exceptions import TransferError
from app.core.logging import get_logger
from app.integrations.telephony.mango import MangoTelephonyAdapter
from app.integrations.transfer_engine.base import AbstractTransferEngine, ManagerCallResult
from app.models.manager import Manager
from app.models.transfer import TransferStatus
from app.repositories.manager_repo import ManagerRepository

if TYPE_CHECKING:
    from app.models.call import Call
    from app.integrations.direct.session_manager import DirectSessionManager

log = get_logger(__name__)


@dataclass
class TransferProgress:
    status: str
    manager_id: Optional[str] = None
    manager_leg_id: Optional[str] = None
    customer_leg_id: Optional[str] = None
    error: Optional[str] = None
    updated_at: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "manager_id": self.manager_id,
            "manager_leg_id": self.manager_leg_id,
            "customer_leg_id": self.customer_leg_id,
            "error": self.error,
            "updated_at": self.updated_at,
        }


class MangoTransferEngine(AbstractTransferEngine):
    """
    Real warm-transfer engine via Mango control-plane.

    Lifecycle tracked per manager leg:
      selected -> calling -> answered -> whispering -> bridged
      failed states: failed_selected / failed_calling / failed_answered /
                    failed_whispering / failed_bridged
    """

    def __init__(
        self,
        telephony: MangoTelephonyAdapter,
        session_factory: async_sessionmaker,
        direct_session_manager: Optional["DirectSessionManager"] = None,
    ) -> None:
        self._telephony = telephony
        self._session_factory = session_factory
        self._direct_session_manager = direct_session_manager
        self._progress: dict[str, TransferProgress] = {}
        self._customer_leg_to_direct_session: dict[str, str] = {}
        self._restore_tasks: dict[uuid.UUID, asyncio.Task] = {}

    async def initiate_manager_call(
        self,
        manager: "Manager",
        call: "Call",
        whisper_text: str,
    ) -> ManagerCallResult:
        # Reservation guard: prevents one manager being selected by two flows.
        reserved = await self._reserve_manager(manager.id)
        if not reserved:
            raise TransferError(
                f"Manager {manager.id} is already reserved/unavailable",
                detail={"manager_id": str(manager.id)},
            )

        call_id = str(call.id)
        customer_leg_id = call.telephony_leg_id or call.vapi_call_id or str(call.id)
        if call.mango_call_id:
            self._customer_leg_to_direct_session[customer_leg_id] = call.mango_call_id
        self._set_progress(
            external_id=call_id,
            status="selected",
            manager_id=str(manager.id),
            customer_leg_id=customer_leg_id,
        )
        self._set_progress(
            external_id=customer_leg_id,
            status="selected",
            manager_id=str(manager.id),
            customer_leg_id=customer_leg_id,
        )

        try:
            self._set_progress(
                external_id=call_id,
                status="calling",
                manager_id=str(manager.id),
                customer_leg_id=customer_leg_id,
            )
            result = await self._telephony.originate_call(
                phone=manager.phone,
                metadata={
                    "call_id": call_id,
                    "role": "manager",
                },
            )
        except Exception as exc:
            self._set_failed(call_id, "failed_calling", str(exc), manager_id=str(manager.id))
            raise

        manager_leg_id = result.leg_id
        self._set_progress(
            external_id=manager_leg_id,
            status="calling",
            manager_id=str(manager.id),
            manager_leg_id=manager_leg_id,
            customer_leg_id=customer_leg_id,
        )
        self._set_progress(
            external_id=call_id,
            status="calling",
            manager_id=str(manager.id),
            manager_leg_id=manager_leg_id,
            customer_leg_id=customer_leg_id,
        )

        try:
            await self.wait_manager_answer(manager_leg_id)
        except asyncio.CancelledError:
            # Service timeout cancelled this coroutine while waiting answer.
            # Cleanup manager leg to avoid late-answer orphan calls.
            try:
                await self._telephony.terminate_leg(manager_leg_id)
            except Exception:
                pass
            self._set_failed(
                manager_leg_id,
                "failed_answered",
                "manager_wait_cancelled",
                manager_id=str(manager.id),
                manager_leg_id=manager_leg_id,
                customer_leg_id=customer_leg_id,
            )
            raise
        except Exception as exc:
            self._set_failed(
                manager_leg_id,
                "failed_answered",
                str(exc),
                manager_id=str(manager.id),
                manager_leg_id=manager_leg_id,
                customer_leg_id=customer_leg_id,
            )
            self._set_failed(
                call_id,
                "failed_answered",
                str(exc),
                manager_id=str(manager.id),
                manager_leg_id=manager_leg_id,
                customer_leg_id=customer_leg_id,
            )
            raise

        self._set_progress(
            external_id=manager_leg_id,
            status="answered",
            manager_id=str(manager.id),
            manager_leg_id=manager_leg_id,
            customer_leg_id=customer_leg_id,
        )
        self._set_progress(
            external_id=call_id,
            status="answered",
            manager_id=str(manager.id),
            manager_leg_id=manager_leg_id,
            customer_leg_id=customer_leg_id,
        )
        return ManagerCallResult(
            external_id=manager_leg_id,
            status=TransferStatus.CALLING_MANAGER.value,
            metadata={
                "provider": "mango",
                "transfer_progress": "answered",
                "manager_id": str(manager.id),
            },
        )

    async def wait_manager_answer(self, manager_call_id: str) -> None:
        await self._telephony.wait_for_answered(
            manager_call_id,
            timeout=float(settings.transfer_manager_answer_timeout),
        )

    async def play_whisper(self, manager_call_id: str, whisper_text: str) -> None:
        self._set_progress(
            external_id=manager_call_id,
            status="whispering",
        )
        try:
            await self._telephony.play_whisper(manager_call_id, whisper_text)
        except Exception as exc:
            self._set_failed(manager_call_id, "failed_whispering", str(exc))
            raise

    async def bridge_calls(self, manager_call_id: str, customer_call_id: str) -> None:
        try:
            await self._telephony.bridge_legs(customer_call_id, manager_call_id)
        except Exception as exc:
            self._set_failed(
                manager_call_id,
                "failed_bridged",
                str(exc),
                customer_leg_id=customer_call_id,
            )
            self._set_failed(
                customer_call_id,
                "failed_bridged",
                str(exc),
                manager_leg_id=manager_call_id,
                customer_leg_id=customer_call_id,
            )
            raise

        self._set_progress(
            external_id=manager_call_id,
            status="bridged",
            manager_leg_id=manager_call_id,
            customer_leg_id=customer_call_id,
        )
        self._set_progress(
            external_id=customer_call_id,
            status="bridged",
            manager_leg_id=manager_call_id,
            customer_leg_id=customer_call_id,
        )
        await self._maybe_suspend_direct_audio(customer_call_id)

    async def terminate_manager_leg(self, manager_leg_id: str) -> None:
        await self.terminate_manager_call(manager_leg_id)

    async def terminate_manager_call(self, manager_call_id: str) -> None:
        try:
            await self._telephony.terminate_leg(manager_call_id)
        except Exception as exc:
            self._set_failed(manager_call_id, "failed_cleanup", str(exc))
            raise

    async def terminate_customer_leg(self, customer_leg_id: str) -> None:
        await self._telephony.terminate_leg(customer_leg_id)

    async def get_transfer_progress(self, external_id: str) -> Optional[dict]:
        progress = self._progress.get(external_id)
        if progress:
            return progress.as_dict()

        # best-effort runtime reconciliation from telephony state
        try:
            leg_state = await self._telephony.get_leg_state(external_id)
        except Exception:
            return None
        mapped = {
            "initiating": "calling",
            "ringing": "calling",
            "answered": "answered",
            "bridged": "bridged",
            "terminated": "failed",
            "failed": "failed",
        }.get(leg_state.value, "unknown")
        return TransferProgress(
            status=mapped,
            manager_leg_id=external_id,
            updated_at=datetime.now(timezone.utc).isoformat(),
        ).as_dict()

    async def mark_manager_temporarily_unavailable(self, manager_id: uuid.UUID) -> None:
        available_after = datetime.now(timezone.utc) + timedelta(
            seconds=float(settings.transfer_manager_cooldown_seconds)
        )
        async with self._session_factory() as session:
            repo = ManagerRepository(Manager, session)
            await repo.set_temporarily_unavailable(
                manager_id,
                available_after=available_after,
            )
            await session.commit()
        self._schedule_restore(manager_id)

    async def _reserve_manager(self, manager_id: uuid.UUID) -> bool:
        async with self._session_factory() as session:
            repo = ManagerRepository(Manager, session)
            reserved = await repo.try_reserve_available(manager_id)
            await session.commit()
            return reserved

    def _schedule_restore(self, manager_id: uuid.UUID) -> None:
        existing = self._restore_tasks.get(manager_id)
        if existing and not existing.done():
            existing.cancel()

        task = asyncio.create_task(
            self._restore_manager_after_cooldown(manager_id),
            name=f"mgr_restore_{manager_id}",
        )
        self._restore_tasks[manager_id] = task
        task.add_done_callback(lambda _: self._restore_tasks.pop(manager_id, None))

    async def _restore_manager_after_cooldown(self, manager_id: uuid.UUID) -> None:
        try:
            await asyncio.sleep(float(settings.transfer_manager_cooldown_seconds))
            async with self._session_factory() as session:
                repo = ManagerRepository(Manager, session)
                restored = await repo.restore_manager_if_due(manager_id)
                await session.commit()
            if not restored:
                return
            log.info(
                "mango_transfer.manager_restored",
                manager_id=str(manager_id),
                cooldown_seconds=settings.transfer_manager_cooldown_seconds,
            )
        except asyncio.CancelledError:
            return
        except Exception as exc:
            log.warning(
                "mango_transfer.manager_restore_failed",
                manager_id=str(manager_id),
                error=str(exc),
            )

    def _set_progress(
        self,
        *,
        external_id: str,
        status: str,
        manager_id: Optional[str] = None,
        manager_leg_id: Optional[str] = None,
        customer_leg_id: Optional[str] = None,
    ) -> None:
        prev = self._progress.get(external_id)
        self._progress[external_id] = TransferProgress(
            status=status,
            manager_id=manager_id or (prev.manager_id if prev else None),
            manager_leg_id=manager_leg_id or (prev.manager_leg_id if prev else None),
            customer_leg_id=customer_leg_id or (prev.customer_leg_id if prev else None),
            error=None,
            updated_at=datetime.now(timezone.utc).isoformat(),
        )

    def _set_failed(
        self,
        external_id: str,
        status: str,
        error: str,
        *,
        manager_id: Optional[str] = None,
        manager_leg_id: Optional[str] = None,
        customer_leg_id: Optional[str] = None,
    ) -> None:
        self._set_progress(
            external_id=external_id,
            status=status,
            manager_id=manager_id,
            manager_leg_id=manager_leg_id,
            customer_leg_id=customer_leg_id,
        )
        self._progress[external_id].error = error

    async def _maybe_suspend_direct_audio(self, customer_leg_id: str) -> None:
        if self._direct_session_manager is None:
            return
        session_id = self._customer_leg_to_direct_session.get(customer_leg_id)
        if not session_id:
            return
        try:
            await self._direct_session_manager.suspend_audio(
                session_id, reason="warm_transfer_bridged"
            )
        except Exception as exc:
            log.warning(
                "mango_transfer.suspend_audio_failed",
                session_id=session_id,
                customer_leg_id=customer_leg_id,
                error=str(exc),
            )
