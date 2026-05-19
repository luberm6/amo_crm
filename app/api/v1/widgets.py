"""
Admin CRUD for widget configurations.
All endpoints require admin authentication.
"""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_auth import require_admin_auth
from app.api.deps import get_db
from app.core.redis_client import get_redis
from app.models.widget import WidgetConfig
from app.repositories.widget_repo import WidgetLeadRepository, WidgetRepository
from app.schemas.widget import (
    WidgetConfigCreate,
    WidgetConfigRead,
    WidgetConfigUpdate,
    WidgetLeadRead,
)
from app.services.widget_service import generate_widget_token

router = APIRouter(
    prefix="/widgets",
    tags=["widgets"],
    dependencies=[Depends(require_admin_auth)],
)


def _get_widget_repo(db: AsyncSession = Depends(get_db)) -> WidgetRepository:
    return WidgetRepository(db)


@router.get("", response_model=list[WidgetConfigRead])
async def list_widgets(
    repo: WidgetRepository = Depends(_get_widget_repo),
) -> list[WidgetConfigRead]:
    widgets = await repo.list_all()
    return [WidgetConfigRead.model_validate(w) for w in widgets]


@router.post("", response_model=WidgetConfigRead, status_code=status.HTTP_201_CREATED)
async def create_widget(
    body: WidgetConfigCreate,
    repo: WidgetRepository = Depends(_get_widget_repo),
) -> WidgetConfigRead:
    widget = WidgetConfig(
        widget_token=generate_widget_token(),
        agent_profile_id=body.agent_profile_id,
        is_active=body.is_active,
        allowed_domains=body.allowed_domains,
        rate_limit_per_hour=body.rate_limit_per_hour,
        rate_limit_per_ip_per_hour=body.rate_limit_per_ip_per_hour,
        lead_capture_fields=body.lead_capture_fields,
        webhook_url=body.webhook_url,
        telegram_chat_id=body.telegram_chat_id,
        custom_greeting=body.custom_greeting,
        custom_styles=body.custom_styles,
    )
    widget = await repo.save(widget)
    return WidgetConfigRead.model_validate(widget)


@router.get("/{widget_id}", response_model=WidgetConfigRead)
async def get_widget(
    widget_id: uuid.UUID,
    repo: WidgetRepository = Depends(_get_widget_repo),
) -> WidgetConfigRead:
    widget = await repo.get(widget_id)
    if widget is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Widget not found")
    return WidgetConfigRead.model_validate(widget)


@router.patch("/{widget_id}", response_model=WidgetConfigRead)
async def update_widget(
    widget_id: uuid.UUID,
    body: WidgetConfigUpdate,
    db: AsyncSession = Depends(get_db),
    repo: WidgetRepository = Depends(_get_widget_repo),
) -> WidgetConfigRead:
    widget = await repo.get(widget_id)
    if widget is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Widget not found")
    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(widget, field, value)
    widget = await repo.save(widget)
    return WidgetConfigRead.model_validate(widget)


@router.delete("/{widget_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_widget(
    widget_id: uuid.UUID,
    repo: WidgetRepository = Depends(_get_widget_repo),
) -> None:
    widget = await repo.get(widget_id)
    if widget is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Widget not found")
    await repo.delete(widget)


@router.post("/{widget_id}/regenerate-token", response_model=WidgetConfigRead)
async def regenerate_widget_token(
    widget_id: uuid.UUID,
    repo: WidgetRepository = Depends(_get_widget_repo),
) -> WidgetConfigRead:
    widget = await repo.get(widget_id)
    if widget is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Widget not found")
    widget.widget_token = generate_widget_token()
    widget = await repo.save(widget)
    return WidgetConfigRead.model_validate(widget)


@router.get("/{widget_id}/leads", response_model=list[WidgetLeadRead])
async def list_widget_leads(
    widget_id: uuid.UUID,
    widget_repo: WidgetRepository = Depends(_get_widget_repo),
    db: AsyncSession = Depends(get_db),
) -> list[WidgetLeadRead]:
    widget = await widget_repo.get(widget_id)
    if widget is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Widget not found")
    lead_repo = WidgetLeadRepository(db)
    leads = await lead_repo.get_by_widget(widget_id)
    return [WidgetLeadRead.model_validate(lead) for lead in leads]
