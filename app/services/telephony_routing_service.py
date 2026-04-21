from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.telephony.mango_client import is_allowed_mango_phone_number, normalize_mango_phone
from app.models.agent_profile import AgentProfile
from app.models.telephony_line import TelephonyLine
from app.repositories.agent_profile_repo import AgentProfileRepository
from app.repositories.telephony_line_repo import TelephonyLineRepository

log = get_logger(__name__)


@dataclass(frozen=True)
class InboundRoutingResult:
    """Result of resolving an inbound number to an agent."""
    agent: Optional[AgentProfile]
    telephony_line: Optional[TelephonyLine]
    phone_number_normalized: str
    ambiguous: bool = False
    candidate_count: int = 0


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
        """Legacy single-result lookup. Use resolve_inbound() for full result."""
        result = await self.resolve_inbound(provider=provider, phone_number=phone_number)
        return result.agent

    async def resolve_inbound(
        self,
        *,
        provider: str,
        phone_number: str,
    ) -> InboundRoutingResult:
        """
        Resolve an inbound phone number to the best matching agent.

        Normalizes the phone number to E.164 before lookup.
        Logs ambiguity if multiple active agents share the same line.
        """
        normalized = normalize_mango_phone(phone_number)

        line = await self.line_repo.get_active_by_phone_number(
            provider=provider,
            phone_number=normalized,
        )

        if line is None:
            log.info(
                "mango_inbound.line_not_found",
                provider=provider,
                phone_number_normalized=normalized,
            )
            return InboundRoutingResult(
                agent=None,
                telephony_line=None,
                phone_number_normalized=normalized,
                candidate_count=0,
            )

        if provider == "mango" and not is_allowed_mango_phone_number(line.phone_number):
            log.warning(
                "mango_inbound.line_rejected_by_primary_number_policy",
                provider=provider,
                phone_number_normalized=normalized,
                line_id=str(line.id),
                provider_resource_id=line.provider_resource_id,
                line_phone_number=line.phone_number,
                primary_phone_number=settings.mango_primary_phone_e164,
            )
            return InboundRoutingResult(
                agent=None,
                telephony_line=None,
                phone_number_normalized=normalized,
                candidate_count=0,
            )

        candidates = await self.agent_repo.get_all_active_by_telephony_line(
            telephony_provider=provider,
            telephony_line_id=line.id,
        )

        if not candidates:
            log.info(
                "mango_inbound.agent_not_found",
                provider=provider,
                phone_number_normalized=normalized,
                line_id=str(line.id),
                provider_resource_id=line.provider_resource_id,
            )
            return InboundRoutingResult(
                agent=None,
                telephony_line=line,
                phone_number_normalized=normalized,
                candidate_count=0,
            )

        if len(candidates) > 1:
            log.warning(
                "mango_inbound.routing_ambiguous",
                provider=provider,
                phone_number_normalized=normalized,
                line_id=str(line.id),
                candidate_count=len(candidates),
                candidate_ids=[str(a.id) for a in candidates],
                selected_agent_id=str(candidates[0].id),
            )
            return InboundRoutingResult(
                agent=candidates[0],
                telephony_line=line,
                phone_number_normalized=normalized,
                ambiguous=True,
                candidate_count=len(candidates),
            )

        log.info(
            "mango_inbound.agent_resolved",
            provider=provider,
            phone_number_normalized=normalized,
            line_id=str(line.id),
            provider_resource_id=line.provider_resource_id,
            agent_id=str(candidates[0].id),
            agent_name=candidates[0].name,
        )
        return InboundRoutingResult(
            agent=candidates[0],
            telephony_line=line,
            phone_number_normalized=normalized,
            candidate_count=1,
        )

    async def resolve_outbound_binding(
        self,
        agent_id: uuid.UUID,
    ) -> Optional[OutboundTelephonyBinding]:
        agent = await self.agent_repo.get_with_related(agent_id)
        if agent is None or not agent.is_active:
            return None
        line = agent.telephony_line
        if line is not None and agent.telephony_provider == "mango" and not is_allowed_mango_phone_number(line.phone_number):
            log.warning(
                "mango_outbound.line_rejected_by_primary_number_policy",
                agent_id=str(agent.id),
                agent_name=agent.name,
                line_id=str(line.id),
                provider_resource_id=line.provider_resource_id,
                line_phone_number=line.phone_number,
                primary_phone_number=settings.mango_primary_phone_e164,
            )
            line = None
        return OutboundTelephonyBinding(agent=agent, telephony_line=line)
