from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_profile import AgentProfile
from app.models.telephony_line import TelephonyLine
from app.repositories.agent_profile_repo import AgentProfileRepository
from app.repositories.telephony_line_repo import TelephonyLineRepository


@dataclass(frozen=True)
class OutboundTelephonyBinding:
    agent: AgentProfile
    telephony_line: Optional[TelephonyLine]


class TelephonyRoutingService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.agent_repo = AgentProfileRepository(AgentProfile, session)
        self.line_repo = TelephonyLineRepository(TelephonyLine, session)

    async def resolve_agent_for_inbound_number(
        self,
        *,
        provider: str,
        phone_number: str,
    ) -> Optional[AgentProfile]:
        line = await self.line_repo.get_active_by_phone_number(
            provider=provider,
            phone_number=phone_number,
        )
        if line is None:
            return None
        return await self.agent_repo.get_active_by_telephony_line(
            telephony_provider=provider,
            telephony_line_id=line.id,
        )

    async def resolve_outbound_binding(
        self,
        agent_id: uuid.UUID,
    ) -> Optional[OutboundTelephonyBinding]:
        agent = await self.agent_repo.get_with_related(agent_id)
        if agent is None or not agent.is_active:
            return None
        return OutboundTelephonyBinding(agent=agent, telephony_line=agent.telephony_line)
