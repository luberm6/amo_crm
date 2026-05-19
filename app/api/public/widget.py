"""
Public widget API — no admin auth required.

Authentication is implicit:
  - widget_token identifies the widget config
  - Origin header is validated against allowed_domains

These endpoints are called by widget.js running on third-party websites.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_browser_registry,
    get_call_service,
    get_db,
    get_direct_session_manager,
)
from app.core.redis_client import get_redis
from app.repositories.agent_profile_repo import AgentProfileRepository
from app.schemas.widget import (
    WidgetLeadSubmit,
    WidgetPublicConfig,
    WidgetSessionRequest,
    WidgetSessionResponse,
)
from app.services.widget_service import WidgetService

router = APIRouter(prefix="/public/widget", tags=["widget-public"])


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _get_widget_service(
    db: AsyncSession = Depends(get_db),
) -> WidgetService:
    redis = get_redis()
    return WidgetService(session=db, redis=redis)


@router.get("/{widget_token}/config", response_model=WidgetPublicConfig)
async def get_widget_config(
    widget_token: str,
    request: Request,
    svc: WidgetService = Depends(_get_widget_service),
    db: AsyncSession = Depends(get_db),
) -> WidgetPublicConfig:
    widget = await svc.get_widget_by_token(widget_token)
    await svc.validate_origin(widget, request.headers.get("origin", ""))

    agent_repo = AgentProfileRepository(db)
    agent = await agent_repo.get(widget.agent_profile_id)
    if agent is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Agent not found")

    return WidgetPublicConfig(
        agent_name=agent.name,
        greeting=widget.custom_greeting or agent.greeting_text,
        custom_styles=widget.custom_styles,
        lead_capture_fields=widget.lead_capture_fields,
    )


@router.post(
    "/{widget_token}/session",
    response_model=WidgetSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_widget_session(
    widget_token: str,
    body: WidgetSessionRequest,
    request: Request,
    svc: WidgetService = Depends(_get_widget_service),
    call_service=Depends(get_call_service),
    registry=Depends(get_browser_registry),
    session_manager=Depends(get_direct_session_manager),
) -> WidgetSessionResponse:
    widget = await svc.get_widget_by_token(widget_token)
    await svc.validate_origin(widget, request.headers.get("origin", ""))
    await svc.check_rate_limits(widget, _get_client_ip(request))
    return await svc.create_session(widget, call_service, registry, session_manager, request)


@router.post("/{widget_token}/lead", status_code=status.HTTP_202_ACCEPTED)
async def submit_widget_lead(
    widget_token: str,
    body: WidgetLeadSubmit,
    request: Request,
    svc: WidgetService = Depends(_get_widget_service),
) -> dict:
    widget = await svc.get_widget_by_token(widget_token)
    await svc.validate_origin(widget, request.headers.get("origin", ""))
    lead = await svc.submit_lead(widget, body)
    asyncio.create_task(svc.deliver_lead_webhook(lead, widget, transcript=None))
    asyncio.create_task(svc.deliver_lead_telegram(lead, widget))
    return {"ok": True}
