from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.telephony.mango_client import normalize_mango_phone
from app.models.agent_profile import AgentProfile
from app.models.telephony_line import TelephonyLine
from app.repositories.telephony_line_repo import TelephonyLineRepository


async def resolve_inbound_number_to_agent(
    db: AsyncSession,
    phone_number: str,
) -> Optional[AgentProfile]:
    """
    Given an inbound caller's phone number, return the AgentProfile bound to
    the matching active Mango line, or None if not found.

    The phone number is normalized to E.164 (+7...) before lookup so that
    both "79300350609" and "+79300350609" resolve to the same line.
    """
    normalized = normalize_mango_phone(phone_number)
    repo = TelephonyLineRepository(TelephonyLine, db)
    line = await repo.get_active_by_phone_number(provider="mango", phone_number=normalized)
    if line is None:
        return None

    result = await db.execute(
        select(AgentProfile)
        .where(AgentProfile.telephony_line_id == line.id)
        .where(AgentProfile.is_active.is_(True))
        .limit(1)
    )
    return result.scalar_one_or_none()


async def resolve_agent_to_mango_line(
    db: AsyncSession,
    agent_id: uuid.UUID,
) -> Optional[TelephonyLine]:
    """
    Return the TelephonyLine bound to this agent, or None if no binding exists.
    """
    result = await db.execute(
        select(AgentProfile)
        .where(AgentProfile.id == agent_id)
        .limit(1)
    )
    agent = result.scalar_one_or_none()
    if agent is None or agent.telephony_line_id is None:
        return None

    line_result = await db.execute(
        select(TelephonyLine).where(TelephonyLine.id == agent.telephony_line_id)
    )
    return line_result.scalar_one_or_none()
