from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError, NotFoundError
from app.models.agent_knowledge_binding import AgentKnowledgeBinding
from app.models.agent_profile import AgentProfile
from app.models.knowledge_document import KnowledgeDocument
from app.models.telephony_line import TelephonyLine
from app.repositories.agent_knowledge_binding_repo import AgentKnowledgeBindingRepository
from app.repositories.agent_profile_repo import AgentProfileRepository
from app.repositories.knowledge_document_repo import KnowledgeDocumentRepository
from app.repositories.telephony_line_repo import TelephonyLineRepository
from app.schemas.telephony import AgentProfileSettingsUpdate
from app.services.agent_profile_service import assemble_agent_system_prompt


class TelephonyLineNotFoundError(NotFoundError):
    error_code = "telephony_line_not_found"


class TelephonyLineInactiveError(AppError):
    status_code = 422
    error_code = "telephony_line_inactive"


class InvalidVoiceProviderError(AppError):
    status_code = 422
    error_code = "invalid_voice_provider"


@dataclass(frozen=True)
class AgentSettingsSnapshot:
    agent: AgentProfile
    telephony_line: Optional[TelephonyLine]
    knowledge_document_ids: list[uuid.UUID]


class AgentSettingsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.agent_repo = AgentProfileRepository(AgentProfile, session)
        self.line_repo = TelephonyLineRepository(TelephonyLine, session)
        self.binding_repo = AgentKnowledgeBindingRepository(AgentKnowledgeBinding, session)
        self.document_repo = KnowledgeDocumentRepository(KnowledgeDocument, session)

    async def get_settings(self, agent_id: uuid.UUID) -> AgentSettingsSnapshot:
        agent = await self.agent_repo.get_with_related(agent_id)
        if agent is None:
            raise NotFoundError(f"Agent profile {agent_id} not found")
        bindings = await self.binding_repo.list_for_agent(agent_id)
        return AgentSettingsSnapshot(
            agent=agent,
            telephony_line=agent.telephony_line,
            knowledge_document_ids=[binding.knowledge_document_id for binding in bindings],
        )

    async def update_settings(
        self,
        agent_id: uuid.UUID,
        body: AgentProfileSettingsUpdate,
    ) -> AgentSettingsSnapshot:
        snapshot = await self.get_settings(agent_id)
        agent = snapshot.agent
        fields = body.model_fields_set

        if "name" in fields and body.name is not None:
            agent.name = body.name
        if "is_active" in fields and body.is_active is not None:
            agent.is_active = body.is_active
        if "system_prompt" in fields and body.system_prompt is not None:
            agent.system_prompt = body.system_prompt
        if "tone_rules" in fields:
            agent.tone_rules = body.tone_rules
        if "business_rules" in fields:
            agent.business_rules = body.business_rules
        if "sales_objectives" in fields:
            agent.sales_objectives = body.sales_objectives
        if "greeting_text" in fields:
            agent.greeting_text = body.greeting_text
        if "transfer_rules" in fields:
            agent.transfer_rules = body.transfer_rules
        if "prohibited_promises" in fields:
            agent.prohibited_promises = body.prohibited_promises
        if "user_settings" in fields:
            agent.config = dict(body.user_settings or {})

        if "voice_provider" in fields:
            if body.voice_provider is None:
                raise InvalidVoiceProviderError("voice_provider cannot be empty")
            agent.voice_provider = body.voice_provider
            agent.voice_strategy = _strategy_from_voice_provider(body.voice_provider)

        await self._apply_telephony_update(agent, body, fields)

        if "knowledge_document_ids" in fields:
            await self._replace_knowledge_bindings(agent.id, body.knowledge_document_ids or [])

        agent.version += 1
        await self.agent_repo.save(agent)
        return await self.get_settings(agent_id)

    async def _apply_telephony_update(
        self,
        agent: AgentProfile,
        body: AgentProfileSettingsUpdate,
        fields: set[str],
    ) -> None:
        telephony_related = {
            "telephony_provider",
            "telephony_line_id",
            "telephony_extension",
        }
        if not (fields & telephony_related):
            return

        if "telephony_provider" in fields and body.telephony_provider is None and "telephony_line_id" not in fields:
            agent.telephony_provider = None
            agent.telephony_line_id = None
            agent.telephony_extension = None
            return

        if "telephony_line_id" in fields:
            if body.telephony_line_id is None:
                agent.telephony_provider = None if body.telephony_provider is None else body.telephony_provider
                agent.telephony_line_id = None
                agent.telephony_extension = None if "telephony_extension" not in fields else body.telephony_extension
                return

            line = await self.line_repo.get(body.telephony_line_id)
            if line is None:
                raise TelephonyLineNotFoundError(f"Telephony line {body.telephony_line_id} not found")
            if line.provider != "mango":
                raise TelephonyLineNotFoundError(
                    f"Telephony line {body.telephony_line_id} is not a Mango line"
                )
            if not line.is_active:
                raise TelephonyLineInactiveError(
                    f"Telephony line {line.phone_number} is inactive",
                    detail={"telephony_line_id": str(line.id), "phone_number": line.phone_number},
                )

            agent.telephony_provider = body.telephony_provider or line.provider
            agent.telephony_line_id = line.id
            if "telephony_extension" in fields:
                agent.telephony_extension = body.telephony_extension
            elif not agent.telephony_extension:
                agent.telephony_extension = line.extension
            return

        if "telephony_provider" in fields:
            agent.telephony_provider = body.telephony_provider
        if "telephony_extension" in fields:
            agent.telephony_extension = body.telephony_extension

    async def _replace_knowledge_bindings(
        self,
        agent_id: uuid.UUID,
        knowledge_document_ids: list[uuid.UUID],
    ) -> None:
        unique_ids: list[uuid.UUID] = list(dict.fromkeys(knowledge_document_ids))
        documents = await self.document_repo.get_many_active(unique_ids)
        found_ids = {document.id for document in documents}
        missing_ids = [str(document_id) for document_id in unique_ids if document_id not in found_ids]
        if missing_ids:
            raise NotFoundError(
                "One or more knowledge documents do not exist or are inactive.",
                detail={"knowledge_document_ids": missing_ids},
            )

        existing = await self.binding_repo.list_for_agent(agent_id)
        existing_by_document_id = {binding.knowledge_document_id: binding for binding in existing}
        keep_ids = set(unique_ids)

        for binding in existing:
            if binding.knowledge_document_id in keep_ids:
                continue
            await self.binding_repo.delete(binding)

        for document_id in unique_ids:
            if document_id in existing_by_document_id:
                continue
            await self.binding_repo.save(
                AgentKnowledgeBinding(
                    agent_profile_id=agent_id,
                    knowledge_document_id=document_id,
                    role=None,
                )
            )


def build_agent_settings_preview(agent: AgentProfile) -> str:
    return assemble_agent_system_prompt(agent)


def _strategy_from_voice_provider(voice_provider: str) -> str:
    cleaned = (voice_provider or "").strip().lower()
    if cleaned == "gemini":
        return "gemini_primary"
    if cleaned == "elevenlabs":
        return "tts_primary"
    raise InvalidVoiceProviderError(
        f"Unsupported voice provider {voice_provider}",
        detail={"voice_provider": voice_provider},
    )
