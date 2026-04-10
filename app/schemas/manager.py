"""
Manager DTOs.
"""
import uuid
from datetime import datetime
from pydantic import BaseModel
class ManagerRead(BaseModel):
    """Read-only representation of a manager."""
    model_config = {"from_attributes": True}
    id: uuid.UUID
    name: str
    phone: str
    telegram_id: int
    is_active: bool
    created_at: datetime