from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.agent_profile import AgentProfile
from app.repositories.agent_profile_repo import AgentProfileRepository
from app.services.knowledge_base_service import RuntimeKnowledgeContext


@dataclass(frozen=True)
class AgentRuntimeConfiguration:
    agent_id: Optional[uuid.UUID]
    name: str
    system_prompt: str
    greeting_text: Optional[str]
    voice_strategy: Optional[str]
    voice_provider: Optional[str]
    telephony_provider: Optional[str]
    telephony_line_id: Optional[uuid.UUID]
    telephony_extension: Optional[str]
    config: dict[str, Any]
    version: Optional[int]
    company_profile: Optional[dict[str, Any]]
    knowledge_context: Optional[RuntimeKnowledgeContext]


def assemble_agent_system_prompt(agent: AgentProfile) -> str:
    sections: list[tuple[str, Optional[str]]] = [
        ("System Prompt", agent.system_prompt),
        ("Tone Rules", agent.tone_rules),
        ("Business Rules", agent.business_rules),
        ("Sales Objectives", agent.sales_objectives),
        ("Transfer Rules", agent.transfer_rules),
        ("Prohibited Promises", agent.prohibited_promises),
    ]

    blocks: list[str] = []
    for title, value in sections:
        text = (value or "").strip()
        if not text:
            continue
        blocks.append(f"{title}:\n{text}")
    return "\n\n".join(blocks).strip()


def assemble_runtime_knowledge_prompt(
    knowledge_context: Optional[RuntimeKnowledgeContext],
) -> str:
    if knowledge_context is None:
        return ""

    blocks: list[str] = []
    company = knowledge_context.company_profile or {}
    company_lines: list[str] = []
    for label, key in (
        ("Company Name", "name"),
        ("Value Proposition", "value_proposition"),
        ("Target Audience", "target_audience"),
        ("Contact Info", "contact_info"),
        ("Compliance Notes", "compliance_notes"),
    ):
        value = company.get(key)
        if isinstance(value, str) and value.strip():
            company_lines.append(f"- {label}: {value.strip()}")
    if company_lines:
        blocks.append("Company Context:\n" + "\n".join(company_lines))

    if knowledge_context.documents:
        document_lines = []
        for snippet in knowledge_context.documents:
            document_lines.append(
                f"- [{snippet.category}] {snippet.title}:\n{snippet.content.strip()}"
            )
        blocks.append("Knowledge Context:\n" + "\n\n".join(document_lines))

    return "\n\n".join(blocks).strip()


def build_agent_runtime_configuration(
    agent: Optional[AgentProfile],
    *,
    knowledge_context: Optional[RuntimeKnowledgeContext] = None,
) -> AgentRuntimeConfiguration:
    knowledge_prompt = assemble_runtime_knowledge_prompt(knowledge_context)
    if agent is None:
        return AgentRuntimeConfiguration(
            agent_id=None,
            name="default_settings",
            system_prompt=knowledge_prompt,
            greeting_text=None,
            voice_strategy=None,
            voice_provider=None,
            telephony_provider=None,
            telephony_line_id=None,
            telephony_extension=None,
            config={},
            version=None,
            company_profile=knowledge_context.company_profile if knowledge_context else None,
            knowledge_context=knowledge_context,
        )
    base_prompt = assemble_agent_system_prompt(agent)
    full_prompt = "\n\n".join(
        part for part in [base_prompt, knowledge_prompt] if part
    ).strip()
    return AgentRuntimeConfiguration(
        agent_id=agent.id,
        name=agent.name,
        system_prompt=full_prompt,
        greeting_text=(agent.greeting_text or "").strip() or None,
        voice_strategy=(agent.voice_strategy or "").strip() or None,
        voice_provider=(agent.voice_provider or "").strip() or None,
        telephony_provider=(agent.telephony_provider or "").strip() or None,
        telephony_line_id=agent.telephony_line_id,
        telephony_extension=(agent.telephony_extension or "").strip() or None,
        config=dict(agent.config or {}),
        version=agent.version,
        company_profile=knowledge_context.company_profile if knowledge_context else None,
        knowledge_context=knowledge_context,
    )


class AgentProfileService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = AgentProfileRepository(AgentProfile, session)

    async def list_profiles(self, *, active_only: Optional[bool] = None) -> list[AgentProfile]:
        return await self.repo.list_profiles(active_only=active_only)

    async def get_profile(self, agent_id: uuid.UUID) -> AgentProfile:
        profile = await self.repo.get(agent_id)
        if profile is None:
            raise NotFoundError(f"Agent profile {agent_id} not found")
        return profile

    async def get_active_profile(self, agent_id: uuid.UUID) -> AgentProfile:
        profile = await self.repo.get_active(agent_id)
        if profile is None:
            raise NotFoundError(f"Active agent profile {agent_id} not found")
        return profile

    async def create_profile(
        self,
        *,
        name: str,
        is_active: bool,
        system_prompt: str,
        tone_rules: Optional[str],
        business_rules: Optional[str],
        sales_objectives: Optional[str],
        greeting_text: Optional[str],
        transfer_rules: Optional[str],
        prohibited_promises: Optional[str],
        voice_strategy: str,
        config: Optional[dict[str, Any]],
    ) -> AgentProfile:
        profile = AgentProfile(
            name=name.strip(),
            is_active=is_active,
            system_prompt=system_prompt.strip(),
            tone_rules=_clean_optional_text(tone_rules),
            business_rules=_clean_optional_text(business_rules),
            sales_objectives=_clean_optional_text(sales_objectives),
            greeting_text=_clean_optional_text(greeting_text),
            transfer_rules=_clean_optional_text(transfer_rules),
            prohibited_promises=_clean_optional_text(prohibited_promises),
            voice_strategy=voice_strategy.strip(),
            voice_provider=_voice_provider_from_strategy(voice_strategy),
            config=dict(config or {}),
            version=1,
        )
        return await self.repo.save(profile)

    async def update_profile(
        self,
        agent_id: uuid.UUID,
        *,
        name: Optional[str] = None,
        is_active: Optional[bool] = None,
        system_prompt: Optional[str] = None,
        tone_rules: Optional[str] = None,
        business_rules: Optional[str] = None,
        sales_objectives: Optional[str] = None,
        greeting_text: Optional[str] = None,
        transfer_rules: Optional[str] = None,
        prohibited_promises: Optional[str] = None,
        voice_strategy: Optional[str] = None,
        config: Optional[dict[str, Any]] = None,
    ) -> AgentProfile:
        profile = await self.get_profile(agent_id)

        if name is not None:
            profile.name = name.strip()
        if is_active is not None:
            profile.is_active = is_active
        if system_prompt is not None:
            profile.system_prompt = system_prompt.strip()
        if tone_rules is not None:
            profile.tone_rules = _clean_optional_text(tone_rules)
        if business_rules is not None:
            profile.business_rules = _clean_optional_text(business_rules)
        if sales_objectives is not None:
            profile.sales_objectives = _clean_optional_text(sales_objectives)
        if greeting_text is not None:
            profile.greeting_text = _clean_optional_text(greeting_text)
        if transfer_rules is not None:
            profile.transfer_rules = _clean_optional_text(transfer_rules)
        if prohibited_promises is not None:
            profile.prohibited_promises = _clean_optional_text(prohibited_promises)
        if voice_strategy is not None:
            profile.voice_strategy = voice_strategy.strip()
            profile.voice_provider = _voice_provider_from_strategy(voice_strategy)
        if config is not None:
            profile.config = dict(config)

        profile.version += 1
        return await self.repo.save(profile)

    async def soft_delete_profile(self, agent_id: uuid.UUID) -> AgentProfile:
        profile = await self.get_profile(agent_id)
        if not profile.is_active:
            return profile
        profile.is_active = False
        profile.version += 1
        return await self.repo.save(profile)

    async def resolve_runtime_profile(
        self,
        agent_id: Optional[uuid.UUID],
    ) -> Optional[AgentProfile]:
        if agent_id is None:
            return None
        return await self.get_active_profile(agent_id)


def _clean_optional_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _voice_provider_from_strategy(strategy: str) -> str:
    cleaned = (strategy or "").strip()
    if cleaned == "gemini_primary":
        return "gemini"
    return "elevenlabs"
