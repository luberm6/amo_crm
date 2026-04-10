"""
TranscriptEntry DTOs.
"""
import uuid
from datetime import datetime
from pydantic import BaseModel
from app.models.transcript import TranscriptRole
class TranscriptEntryRead(BaseModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    call_id: uuid.UUID
    role: TranscriptRole
    text: str
    sequence_num: int
    created_at: datetime