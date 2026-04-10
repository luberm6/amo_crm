"""
SteeringInstruction DTOs.
"""
import uuid
from datetime import datetime
from pydantic import BaseModel, Field
class SteerRequest(BaseModel):
    """Request body for POST /calls/{id}/steer."""
    instruction: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Natural-language directive for the AI assistant.",
        examples=["Ask the client about their budget range"],
    )
    # Actor is resolved from auth context in production; for MVP it's passed in
    issued_by: str = Field(
        default="system",
        max_length=100,
        description="Telegram user ID or 'system' for automated steering.",
    )
class SteeringRead(BaseModel):
    """Response body after a steer action."""
    model_config = {"from_attributes": True}
    id: uuid.UUID
    call_id: uuid.UUID
    instruction: str
    issued_by: str
    created_at: datetime