"""
Transfer DTOs — request/response schemas for warm transfer endpoints.

TransferRequest    — POST /calls/{id}/transfer body
TransferRead       — full TransferRecord view (POST response + detail)
ManagerContextView — GET /calls/{id}/manager-context response
"""
from __future__ import annotations

from typing import Optional
import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.transfer import TransferStatus


class TransferRequest(BaseModel):
    """Optional body for POST /calls/{id}/transfer."""
    department: Optional[str] = Field(
        default=None,
        max_length=100,
        description="Route to managers in this department. Omit to accept any department.",
        examples=["sales", "support"],
    )


class TransferRead(BaseModel):
    """Full TransferRecord representation returned after initiating a transfer."""
    model_config = {"from_attributes": True}

    id: uuid.UUID
    call_id: uuid.UUID
    manager_id: Optional[uuid.UUID] = None
    status: TransferStatus
    summary: Optional[str] = None
    whisper_text: Optional[str] = None
    manager_call_id: Optional[str] = None
    attempt_count: int
    department: Optional[str] = None
    fallback_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ManagerContextView(BaseModel):
    """
    Context shown to manager before they pick up — returned by
    GET /calls/{id}/manager-context.

    Combines call info, transfer status, and generated summary/whisper
    into a single response for the manager-facing UI or TTS endpoint.
    """
    # Call basics
    call_id: uuid.UUID
    customer_phone: str

    # Transfer state
    transfer_status: TransferStatus
    summary: Optional[str] = None
    whisper_text: Optional[str] = None
    fallback_message: Optional[str] = None

    # Manager identity (null if not yet assigned)
    manager_id: Optional[uuid.UUID] = None
    manager_name: Optional[str] = None
    manager_phone: Optional[str] = None
