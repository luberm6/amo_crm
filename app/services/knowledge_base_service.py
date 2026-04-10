from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.agent_knowledge_binding import AgentKnowledgeBinding
from app.models.agent_profile import AgentProfile
from app.models.company_profile import CompanyProfile
from app.models.knowledge_document import KnowledgeDocument
from app.repositories.agent_knowledge_binding_repo import AgentKnowledgeBindingRepository
from app.repositories.agent_profile_repo import AgentProfileRepository
from app.repositories.company_profile_repo import CompanyProfileRepository
from app.repositories.knowledge_document_repo import KnowledgeDocumentRepository


@dataclass(frozen=True)
class RuntimeKnowledgeSnippet:
    document_id: uuid.UUID
    title: str
    category: str
    content: str


@dataclass(frozen=True)
class RuntimeKnowledgeContext:
    company_profile: Optional[dict[str, Any]]
    documents: list[RuntimeKnowledgeSnippet]
    categories: dict[str, list[RuntimeKnowledgeSnippet]]


def _clean_optional_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def assemble_company_profile_context(profile: Optional[CompanyProfile]) -> Optional[dict[str, Any]]:
    if profile is None:
        return None
    return {
        "name": profile.name,
        "legal_name": profile.legal_name,
        "description": profile.description,
        "value_proposition": profile.value_proposition,
        "target_audience": profile.target_audience,
        "contact_info": profile.contact_info,
        "website_url": profile.website_url,
        "working_hours": profile.working_hours,
        "compliance_notes": profile.compliance_notes,
        "config": dict(profile.config or {}),
    }


def assemble_runtime_knowledge_context(
    company_profile: Optional[CompanyProfile],
    documents: list[KnowledgeDocument],
    *,
    snippet_limit: int = 8,
) -> RuntimeKnowledgeContext:
    selected = documents[:snippet_limit]
    snippets = [
        RuntimeKnowledgeSnippet(
            document_id=document.id,
            title=document.title,
            category=document.category,
            content=document.content,
        )
        for document in selected
    ]
    categories: dict[str, list[RuntimeKnowledgeSnippet]] = {}
    for snippet in snippets:
        categories.setdefault(snippet.category, []).append(snippet)

    return RuntimeKnowledgeContext(
        company_profile=assemble_company_profile_context(company_profile),
        documents=snippets,
        categories=categories,
    )


class KnowledgeBaseService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.document_repo = KnowledgeDocumentRepository(KnowledgeDocument, session)
        self.binding_repo = AgentKnowledgeBindingRepository(AgentKnowledgeBinding, session)
        self.agent_repo = AgentProfileRepository(AgentProfile, session)
        self.company_repo = CompanyProfileRepository(CompanyProfile, session)

    async def list_documents(
        self,
        *,
        category: Optional[str] = None,
        active_only: Optional[bool] = None,
    ) -> list[KnowledgeDocument]:
        return await self.document_repo.list_documents(category=category, active_only=active_only)

    async def get_document(self, document_id: uuid.UUID) -> KnowledgeDocument:
        document = await self.document_repo.get(document_id)
        if document is None:
            raise NotFoundError(f"Knowledge document {document_id} not found")
        return document

    async def create_document(
        self,
        *,
        title: str,
        category: str,
        content: str,
        is_active: bool,
        notes: Optional[str],
        metadata: Optional[dict[str, Any]],
    ) -> KnowledgeDocument:
        document = KnowledgeDocument(
            title=title.strip(),
            category=category.strip(),
            content=content.strip(),
            is_active=is_active,
            notes=_clean_optional_text(notes),
            metadata_json=dict(metadata or {}),
        )
        return await self.document_repo.save(document)

    async def update_document(
        self,
        document_id: uuid.UUID,
        *,
        title: Optional[str] = None,
        category: Optional[str] = None,
        content: Optional[str] = None,
        is_active: Optional[bool] = None,
        notes: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> KnowledgeDocument:
        document = await self.get_document(document_id)
        if title is not None:
            document.title = title.strip()
        if category is not None:
            document.category = category.strip()
        if content is not None:
            document.content = content.strip()
        if is_active is not None:
            document.is_active = is_active
        if notes is not None:
            document.notes = _clean_optional_text(notes)
        if metadata is not None:
            document.metadata_json = dict(metadata)
        return await self.document_repo.save(document)

    async def soft_delete_document(self, document_id: uuid.UUID) -> KnowledgeDocument:
        document = await self.get_document(document_id)
        if not document.is_active:
            return document
        document.is_active = False
        return await self.document_repo.save(document)

    async def list_agent_bindings(self, agent_id: uuid.UUID) -> list[AgentKnowledgeBinding]:
        await self._get_agent(agent_id)
        return await self.binding_repo.list_for_agent(agent_id)

    async def bind_document(
        self,
        *,
        agent_id: uuid.UUID,
        knowledge_document_id: uuid.UUID,
        role: Optional[str] = None,
    ) -> AgentKnowledgeBinding:
        await self._get_agent(agent_id)
        document = await self.document_repo.get_active(knowledge_document_id)
        if document is None:
            raise NotFoundError(
                f"Active knowledge document {knowledge_document_id} not found"
            )

        existing = await self.binding_repo.find_existing(
            agent_id=agent_id,
            knowledge_document_id=knowledge_document_id,
        )
        if existing is not None:
            return existing

        binding = AgentKnowledgeBinding(
            agent_profile_id=agent_id,
            knowledge_document_id=knowledge_document_id,
            role=_clean_optional_text(role),
        )
        return await self.binding_repo.save(binding)

    async def unbind_document(
        self,
        *,
        agent_id: uuid.UUID,
        binding_id: uuid.UUID,
    ) -> None:
        await self._get_agent(agent_id)
        binding = await self.binding_repo.get(binding_id)
        if binding is None or binding.agent_profile_id != agent_id:
            raise NotFoundError(f"Knowledge binding {binding_id} not found for agent {agent_id}")
        await self.binding_repo.delete(binding)

    async def get_company_profile(self) -> Optional[CompanyProfile]:
        return await self.company_repo.get_latest_active()

    async def upsert_company_profile(
        self,
        *,
        name: str,
        legal_name: Optional[str],
        description: Optional[str],
        value_proposition: Optional[str],
        target_audience: Optional[str],
        contact_info: Optional[str],
        website_url: Optional[str],
        working_hours: Optional[str],
        compliance_notes: Optional[str],
        is_active: bool,
        config: Optional[dict[str, Any]],
    ) -> CompanyProfile:
        profile = await self.company_repo.get_latest_active()
        if profile is None:
            profile = CompanyProfile(
                name=name.strip(),
                legal_name=_clean_optional_text(legal_name),
                description=_clean_optional_text(description),
                value_proposition=_clean_optional_text(value_proposition),
                target_audience=_clean_optional_text(target_audience),
                contact_info=_clean_optional_text(contact_info),
                website_url=_clean_optional_text(website_url),
                working_hours=_clean_optional_text(working_hours),
                compliance_notes=_clean_optional_text(compliance_notes),
                is_active=is_active,
                config=dict(config or {}),
            )
            return await self.company_repo.save(profile)

        profile.name = name.strip()
        profile.legal_name = _clean_optional_text(legal_name)
        profile.description = _clean_optional_text(description)
        profile.value_proposition = _clean_optional_text(value_proposition)
        profile.target_audience = _clean_optional_text(target_audience)
        profile.contact_info = _clean_optional_text(contact_info)
        profile.website_url = _clean_optional_text(website_url)
        profile.working_hours = _clean_optional_text(working_hours)
        profile.compliance_notes = _clean_optional_text(compliance_notes)
        profile.is_active = is_active
        profile.config = dict(config or {})
        return await self.company_repo.save(profile)

    async def build_agent_runtime_knowledge_context(
        self,
        agent_id: Optional[uuid.UUID],
    ) -> RuntimeKnowledgeContext:
        company_profile = await self.get_company_profile()
        if agent_id is None:
            return assemble_runtime_knowledge_context(company_profile, [])

        bindings = await self.binding_repo.list_for_agent(agent_id)
        documents = [
            binding.knowledge_document
            for binding in bindings
            if binding.knowledge_document is not None and binding.knowledge_document.is_active
        ]
        return assemble_runtime_knowledge_context(company_profile, documents)

    async def _get_agent(self, agent_id: uuid.UUID) -> AgentProfile:
        agent = await self.agent_repo.get(agent_id)
        if agent is None:
            raise NotFoundError(f"Agent profile {agent_id} not found")
        return agent
