"""
Transfer endpoints.
POST /calls/{call_id}/transfer          — initiate warm transfer for a call
GET  /calls/{call_id}/manager-context   — get transfer summary/whisper for manager UI
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.deps import get_abuse_policy, get_transfer_service, get_db
from app.core.exceptions import AppError, NotFoundError
from app.core.rate_limit import AbusePolicy
from app.models.call import Call
from app.models.manager import Manager
from app.repositories.call_repo import CallRepository
from app.repositories.manager_repo import ManagerRepository
from app.repositories.transfer_repo import TransferRepository
from app.models.transfer import TransferRecord
from app.schemas.transfer import ManagerContextView, TransferRead, TransferRequest
from app.services.transfer_service import TransferService
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/calls", tags=["transfers"])


def _handle_app_error(exc: AppError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())


@router.post(
    "/{call_id}/transfer",
    response_model=TransferRead,
    status_code=status.HTTP_201_CREATED,
)
async def initiate_transfer(
    call_id: uuid.UUID,
    body: TransferRequest = TransferRequest(),
    request: Request = None,
    session: AsyncSession = Depends(get_db),
    policy: AbusePolicy = Depends(get_abuse_policy),
    service: TransferService = Depends(get_transfer_service),
) -> TransferRead:
    """
    Initiate a warm transfer for an in-progress call.

    Selects the best available manager (optionally filtered by department),
    generates a summary + whisper, dials the manager, plays the whisper,
    then bridges the customer.

    Returns the TransferRecord. Status will be CONNECTED on success.
    """
    # Load call to get phone for rate limiting
    call_repo = CallRepository(Call, session)
    call = await call_repo.get(call_id)
    if call is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Call {call_id} not found"},
        )

    # Rate limiting check
    try:
        await policy.check_transfer(call_id=str(call_id), phone=call.phone)
    except AppError as exc:
        _handle_app_error(exc)

    # Initiate transfer
    try:
        record = await service.initiate_transfer(
            call_id=call_id,
            department=body.department,
            actor="api",
        )
    except AppError as exc:
        _handle_app_error(exc)
    return TransferRead.model_validate(record)


@router.get(
    "/{call_id}/manager-context",
    response_model=ManagerContextView,
)
async def get_manager_context(
    call_id: uuid.UUID,
    service: TransferService = Depends(get_transfer_service),
) -> ManagerContextView:
    """
    Return context for the manager-facing UI or TTS playback.

    Includes: customer phone, transfer status, summary, whisper text,
    manager identity, and any fallback message.

    404 if no transfer record exists for this call.
    """
    record = await service.transfer_repo.get_latest_for_call(call_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"No transfer record for call {call_id}"},
        )

    # Load the call for phone number
    call = await service.call_repo.get(call_id)
    if call is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Call {call_id} not found"},
        )

    # Load manager if assigned
    manager_name: str | None = None
    manager_phone: str | None = None
    if record.manager_id is not None:
        mgr_row = await service.manager_repo.get(record.manager_id)
        if mgr_row is not None:
            manager_name = mgr_row.name
            manager_phone = mgr_row.phone

    return ManagerContextView(
        call_id=call_id,
        customer_phone=call.phone,
        transfer_status=record.status,
        summary=record.summary,
        whisper_text=record.whisper_text,
        fallback_message=record.fallback_message,
        manager_id=record.manager_id,
        manager_name=manager_name,
        manager_phone=manager_phone,
    )
