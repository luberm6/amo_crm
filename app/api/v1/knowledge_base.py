from __future__ import annotations

from typing import Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_auth import require_admin_auth
from app.api.deps import get_db
from app.core.exceptions import AppError
from app.models.agent_knowledge_binding import AgentKnowledgeBinding
from app.models.company_profile import CompanyProfile
from app.models.knowledge_document import KnowledgeDocument
from app.schemas.knowledge_base import (
    CompanyProfileRead,
    CompanyProfileUpdate,
    KnowledgeDocumentCreate,
    KnowledgeDocumentListItem,
    KnowledgeDocumentListRead,
    KnowledgeDocumentRead,
    KnowledgeDocumentUpdate,
)
from app.services.knowledge_base_service import KnowledgeBaseService

router = APIRouter(
    tags=["knowledge-base"],
    dependencies=[Depends(require_admin_auth)],
)


def _handle_app_error(exc: AppError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())


def _document_to_read(document: KnowledgeDocument) -> KnowledgeDocumentRead:
    return KnowledgeDocumentRead(
        id=document.id,
        title=document.title,
        category=document.category,
        content=document.content,
        is_active=document.is_active,
        notes=document.notes,
        metadata=dict(document.metadata_json or {}),
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


def _company_to_read(profile: CompanyProfile) -> CompanyProfileRead:
    return CompanyProfileRead(
        id=profile.id,
        name=profile.name,
        legal_name=profile.legal_name,
        description=profile.description,
        value_proposition=profile.value_proposition,
        target_audience=profile.target_audience,
        contact_info=profile.contact_info,
        website_url=profile.website_url,
        working_hours=profile.working_hours,
        compliance_notes=profile.compliance_notes,
        is_active=profile.is_active,
        config=dict(profile.config or {}),
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


@router.get("/knowledge-documents", response_model=KnowledgeDocumentListRead)
async def list_knowledge_documents(
    category: Optional[str] = Query(default=None),
    active_only: Optional[bool] = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> KnowledgeDocumentListRead:
    service = KnowledgeBaseService(db)
    documents = await service.list_documents(category=category, active_only=active_only)
    return KnowledgeDocumentListRead(
        items=[KnowledgeDocumentListItem.model_validate(document) for document in documents],
        total=len(documents),
    )


@router.get("/knowledge-documents/{document_id}", response_model=KnowledgeDocumentRead)
async def get_knowledge_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> KnowledgeDocumentRead:
    service = KnowledgeBaseService(db)
    try:
        document = await service.get_document(document_id)
    except AppError as exc:
        _handle_app_error(exc)
    return _document_to_read(document)


@router.post(
    "/knowledge-documents",
    response_model=KnowledgeDocumentRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_knowledge_document(
    body: KnowledgeDocumentCreate,
    db: AsyncSession = Depends(get_db),
) -> KnowledgeDocumentRead:
    service = KnowledgeBaseService(db)
    document = await service.create_document(
        title=body.title,
        category=body.category,
        content=body.content,
        is_active=body.is_active,
        notes=body.notes,
        metadata=body.metadata,
    )
    await db.commit()
    return _document_to_read(document)


@router.patch("/knowledge-documents/{document_id}", response_model=KnowledgeDocumentRead)
async def update_knowledge_document(
    document_id: uuid.UUID,
    body: KnowledgeDocumentUpdate,
    db: AsyncSession = Depends(get_db),
) -> KnowledgeDocumentRead:
    service = KnowledgeBaseService(db)
    try:
        document = await service.update_document(
            document_id,
            title=body.title,
            category=body.category,
            content=body.content,
            is_active=body.is_active,
            notes=body.notes,
            metadata=body.metadata,
        )
    except AppError as exc:
        _handle_app_error(exc)
    await db.commit()
    return _document_to_read(document)


@router.delete("/knowledge-documents/{document_id}", response_model=KnowledgeDocumentRead)
async def delete_knowledge_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> KnowledgeDocumentRead:
    service = KnowledgeBaseService(db)
    try:
        document = await service.soft_delete_document(document_id)
    except AppError as exc:
        _handle_app_error(exc)
    await db.commit()
    return _document_to_read(document)


@router.get("/company-profile", response_model=Optional[CompanyProfileRead])
async def get_company_profile(
    db: AsyncSession = Depends(get_db),
) -> Optional[CompanyProfileRead]:
    service = KnowledgeBaseService(db)
    profile = await service.get_company_profile()
    if profile is None:
        return None
    return _company_to_read(profile)


@router.put("/company-profile", response_model=CompanyProfileRead)
async def upsert_company_profile(
    body: CompanyProfileUpdate,
    db: AsyncSession = Depends(get_db),
) -> CompanyProfileRead:
    service = KnowledgeBaseService(db)
    profile = await service.upsert_company_profile(
        name=body.name,
        legal_name=body.legal_name,
        description=body.description,
        value_proposition=body.value_proposition,
        target_audience=body.target_audience,
        contact_info=body.contact_info,
        website_url=body.website_url,
        working_hours=body.working_hours,
        compliance_notes=body.compliance_notes,
        is_active=body.is_active,
        config=body.config,
    )
    await db.commit()
    return _company_to_read(profile)
