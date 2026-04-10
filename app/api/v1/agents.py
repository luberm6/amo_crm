from __future__ import annotations

from typing import Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_auth import require_admin_auth
from app.api.deps import get_db
from app.core.exceptions import AppError
from app.models.agent_profile import AgentProfile
from app.schemas.agent_profile import (
    AgentProfileCreate,
    AgentProfileListItem,
    AgentProfileListRead,
    AgentProfileRead,
    AgentProfileUpdate,
)
from app.schemas.knowledge_base import (
    AgentKnowledgeBindingCreate,
    AgentKnowledgeBindingListRead,
    AgentKnowledgeBindingRead,
    KnowledgeDocumentRead,
)
from app.services.agent_profile_service import (
    AgentProfileService,
    assemble_agent_system_prompt,
)
from app.services.knowledge_base_service import KnowledgeBaseService

router = APIRouter(
    prefix="/agents",
    tags=["agents"],
    dependencies=[Depends(require_admin_auth)],
)


def _handle_app_error(exc: AppError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())


def _to_read(profile: AgentProfile) -> AgentProfileRead:
    return AgentProfileRead(
        id=profile.id,
        name=profile.name,
        is_active=profile.is_active,
        system_prompt=profile.system_prompt,
        tone_rules=profile.tone_rules,
        business_rules=profile.business_rules,
        sales_objectives=profile.sales_objectives,
        greeting_text=profile.greeting_text,
        transfer_rules=profile.transfer_rules,
        prohibited_promises=profile.prohibited_promises,
        voice_strategy=profile.voice_strategy,
        config=dict(profile.config or {}),
        version=profile.version,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
        assembled_prompt_preview=assemble_agent_system_prompt(profile),
    )


def _binding_to_read(binding) -> AgentKnowledgeBindingRead:
    document = binding.knowledge_document
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "binding_document_missing",
                "message": "Knowledge binding is missing its document relation.",
            },
        )
    return AgentKnowledgeBindingRead(
        id=binding.id,
        agent_profile_id=binding.agent_profile_id,
        knowledge_document_id=binding.knowledge_document_id,
        role=binding.role,
        created_at=binding.created_at,
        knowledge_document=KnowledgeDocumentRead(
            id=document.id,
            title=document.title,
            category=document.category,
            content=document.content,
            is_active=document.is_active,
            notes=document.notes,
            metadata=dict(document.metadata_json or {}),
            created_at=document.created_at,
            updated_at=document.updated_at,
        ),
    )


@router.get("", response_model=AgentProfileListRead)
async def list_agents(
    active_only: Optional[bool] = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> AgentProfileListRead:
    service = AgentProfileService(db)
    profiles = await service.list_profiles(active_only=active_only)
    return AgentProfileListRead(
        items=[AgentProfileListItem.model_validate(profile) for profile in profiles],
        total=len(profiles),
    )


@router.get("/{agent_id}", response_model=AgentProfileRead)
async def get_agent(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> AgentProfileRead:
    service = AgentProfileService(db)
    try:
        profile = await service.get_profile(agent_id)
    except AppError as exc:
        _handle_app_error(exc)
    return _to_read(profile)


@router.post("", response_model=AgentProfileRead, status_code=status.HTTP_201_CREATED)
async def create_agent(
    body: AgentProfileCreate,
    db: AsyncSession = Depends(get_db),
) -> AgentProfileRead:
    service = AgentProfileService(db)
    profile = await service.create_profile(
        name=body.name,
        is_active=body.is_active,
        system_prompt=body.system_prompt,
        tone_rules=body.tone_rules,
        business_rules=body.business_rules,
        sales_objectives=body.sales_objectives,
        greeting_text=body.greeting_text,
        transfer_rules=body.transfer_rules,
        prohibited_promises=body.prohibited_promises,
        voice_strategy=body.voice_strategy,
        config=body.config,
    )
    await db.commit()
    return _to_read(profile)


@router.patch("/{agent_id}", response_model=AgentProfileRead)
async def update_agent(
    agent_id: uuid.UUID,
    body: AgentProfileUpdate,
    db: AsyncSession = Depends(get_db),
) -> AgentProfileRead:
    service = AgentProfileService(db)
    try:
        profile = await service.update_profile(
            agent_id,
            name=body.name,
            is_active=body.is_active,
            system_prompt=body.system_prompt,
            tone_rules=body.tone_rules,
            business_rules=body.business_rules,
            sales_objectives=body.sales_objectives,
            greeting_text=body.greeting_text,
            transfer_rules=body.transfer_rules,
            prohibited_promises=body.prohibited_promises,
            voice_strategy=body.voice_strategy,
            config=body.config,
        )
    except AppError as exc:
        _handle_app_error(exc)
    await db.commit()
    return _to_read(profile)


@router.delete("/{agent_id}", response_model=AgentProfileRead)
async def delete_agent(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> AgentProfileRead:
    service = AgentProfileService(db)
    try:
        profile = await service.soft_delete_profile(agent_id)
    except AppError as exc:
        _handle_app_error(exc)
    await db.commit()
    return _to_read(profile)


@router.get("/{agent_id}/knowledge", response_model=AgentKnowledgeBindingListRead)
async def list_agent_knowledge(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> AgentKnowledgeBindingListRead:
    service = KnowledgeBaseService(db)
    try:
        bindings = await service.list_agent_bindings(agent_id)
    except AppError as exc:
        _handle_app_error(exc)
    return AgentKnowledgeBindingListRead(
        items=[_binding_to_read(binding) for binding in bindings],
        total=len(bindings),
    )


@router.post(
    "/{agent_id}/knowledge/bind",
    response_model=AgentKnowledgeBindingRead,
    status_code=status.HTTP_201_CREATED,
)
async def bind_agent_knowledge(
    agent_id: uuid.UUID,
    body: AgentKnowledgeBindingCreate,
    db: AsyncSession = Depends(get_db),
) -> AgentKnowledgeBindingRead:
    service = KnowledgeBaseService(db)
    try:
        binding = await service.bind_document(
            agent_id=agent_id,
            knowledge_document_id=body.knowledge_document_id,
            role=body.role,
        )
        bindings = await service.list_agent_bindings(agent_id)
    except AppError as exc:
        _handle_app_error(exc)
    await db.commit()
    matched = next((item for item in bindings if item.id == binding.id), None)
    if matched is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "binding_refresh_failed",
                "message": "Knowledge binding was saved but could not be reloaded.",
            },
        )
    return _binding_to_read(matched)


@router.delete("/{agent_id}/knowledge/{binding_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unbind_agent_knowledge(
    agent_id: uuid.UUID,
    binding_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    service = KnowledgeBaseService(db)
    try:
        await service.unbind_document(agent_id=agent_id, binding_id=binding_id)
    except AppError as exc:
        _handle_app_error(exc)
    await db.commit()
