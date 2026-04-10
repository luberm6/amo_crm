"""
AbstractTransferEngine — interface for warm transfer telephony operations.

Implementations:
  StubTransferEngine  — dev/test, logs only, simulates immediate manager answer
  VapiTransferEngine  — production, uses Vapi squad/transfer API  [next phase]
  MangoTransferEngine — production, uses Mango SIP BRIDGE command [after Direct mode]

Design constraints:
  - Must NOT mutate Call or Manager objects (that is the service's responsibility)
  - initiate_manager_call must return synchronously with a ManagerCallResult
  - play_whisper must complete before bridge_calls is invoked
  - All methods are async; implementations should not block the event loop
"""
from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.call import Call
    from app.models.manager import Manager
    from app.models.transfer import TransferStatus


@dataclass
class ManagerCallResult:
    """
    Returned by initiate_manager_call to describe the dial-out outcome.

    external_id — engine-assigned call leg ID for the manager side.
                  Used to identify the call in subsequent play_whisper / bridge_calls.
    status      — initial TransferStatus after dialling (typically CALLING_MANAGER).
    metadata    — optional engine-specific debugging payload.
    """
    external_id: Optional[str] = None
    status: Optional[str] = None     # TransferStatus value — avoid circular import
    metadata: Optional[dict] = field(default=None)


class AbstractTransferEngine(ABC):
    """
    Telephony abstraction for warm transfer operations.

    Each method corresponds to one phase of the warm transfer state machine.
    The service layer drives the state machine; the engine executes the
    telephony actions and reports results — it never updates DB state directly.
    """

    @abstractmethod
    async def initiate_manager_call(
        self,
        manager: "Manager",
        call: "Call",
        whisper_text: str,
    ) -> ManagerCallResult:
        """
        Dial the manager's phone number.

        The engine should start ringing the manager's phone and return a
        ManagerCallResult with an external_id that identifies this call leg.
        The customer is NOT yet connected at this point.

        Must NOT mutate manager or call — the service owns state transitions.
        """
        ...

    @abstractmethod
    async def play_whisper(
        self,
        manager_call_id: str,
        whisper_text: str,
    ) -> None:
        """
        Play the whisper audio to the manager after they answer.

        The whisper is a short briefing (≤200 chars) about the customer.
        This must complete BEFORE bridge_calls is invoked.
        If whisper fails, the service logs a warning and proceeds to bridge.
        """
        ...

    @abstractmethod
    async def bridge_calls(
        self,
        manager_call_id: str,
        customer_call_id: str,
    ) -> None:
        """
        Connect the manager's call leg with the customer's call leg.

        After this call, both parties can speak to each other directly.
        customer_call_id is the external engine ID on the originating call
        (vapi_call_id on the Call model).
        """
        ...

    @abstractmethod
    async def mark_manager_temporarily_unavailable(
        self,
        manager_id: uuid.UUID,
    ) -> None:
        """
        Mark a manager as temporarily unavailable after a no-answer.

        The implementation is responsible for:
          1. Persisting is_available=False via ManagerRepository
          2. Scheduling or triggering availability restore after cooldown

        In StubTransferEngine: no-op (availability restore is not simulated).
        In production engines: calls ManagerRepository + schedules Celery task.
        """
        ...

    async def terminate_manager_call(self, manager_call_id: str) -> None:
        """
        Terminate the manager-side call leg.

        Called as cleanup when:
          - Client hangs up after manager answered but before bridge
          - Bridge fails — terminate manager leg to prevent orphaned calls
          - Bridge times out

        Default implementation: no-op (safe for engines that auto-cleanup).
        Override in production engines (e.g. Vapi DELETE /call/{id}).
        """
        ...

    async def wait_manager_answer(self, manager_call_id: str) -> None:
        """
        Optional explicit waiter for provider engines that separate originate
        from answer-awaiting. Default no-op for backward compatibility.
        """
        return None

    async def terminate_manager_leg(self, manager_leg_id: str) -> None:
        """
        Alias for terminate_manager_call to keep terminology explicit in
        telephony control-plane code.
        """
        await self.terminate_manager_call(manager_leg_id)

    async def get_transfer_progress(self, external_id: str) -> Optional[dict]:
        """
        Optional provider progress payload for observability/debugging.
        Returns None by default in engines that do not track per-leg progress.
        """
        return None

    async def terminate_customer_leg(self, customer_leg_id: str) -> None:
        """
        Optional cleanup hook for customer-side leg when transfer flow
        transitions to terminal failure.
        """
        return None
