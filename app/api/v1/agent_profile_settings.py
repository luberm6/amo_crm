from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_auth import require_admin_auth
from app.api.deps import get_db
from app.core.exceptions import AppError
from app.schemas.telephony import (
    AgentProfileSettingsRead,
    AgentProfileSettingsUpdate,
    TelephonyLineRead,
)
from app.services.agent_settings_service import (
    AgentSettingsService,
    AgentSettingsSnapshot,
    build_agent_settings_preview,
)

router = APIRouter(
    prefix="/agent-profiles",
    tags=["agent-profiles"],
    dependencies=[Depends(require_admin_auth)],
)


def _handle_app_error(exc: AppError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())


def _to_read(snapshot: AgentSettingsSnapshot) -> AgentProfileSettingsRead:
    agent = snapshot.agent
    return AgentProfileSettingsRead(
        agent_profile_id=agent.id,
        name=agent.name,
        is_active=agent.is_active,
        system_prompt=agent.system_prompt,
        tone_rules=agent.tone_rules,
        business_rules=agent.business_rules,
        sales_objectives=agent.sales_objectives,
        greeting_text=agent.greeting_text,
        transfer_rules=agent.transfer_rules,
        prohibited_promises=agent.prohibited_promises,
        voice_strategy=agent.voice_strategy,
        voice_provider=agent.voice_provider,
        telephony_provider=agent.telephony_provider,
        telephony_line_id=agent.telephony_line_id,
        telephony_extension=agent.telephony_extension,
        telephony_line=(
            TelephonyLineRead.model_validate(snapshot.telephony_line)
            if snapshot.telephony_line is not None
            else None
        ),
        user_settings=dict(agent.config or {}),
        knowledge_document_ids=list(snapshot.knowledge_document_ids),
        version=agent.version,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
        assembled_prompt_preview=build_agent_settings_preview(agent),
    )


@router.get("/{agent_id}/settings", response_model=AgentProfileSettingsRead)
async def get_agent_profile_settings(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> AgentProfileSettingsRead:
    service = AgentSettingsService(db)
    try:
        snapshot = await service.get_settings(agent_id)
    except AppError as exc:
        _handle_app_error(exc)
    return _to_read(snapshot)


@router.patch("/{agent_id}/settings", response_model=AgentProfileSettingsRead)
async def update_agent_profile_settings(
    agent_id: uuid.UUID,
    body: AgentProfileSettingsUpdate,
    db: AsyncSession = Depends(get_db),
) -> AgentProfileSettingsRead:
    service = AgentSettingsService(db)
    try:
        snapshot = await service.update_settings(agent_id, body)
    except AppError as exc:
        _handle_app_error(exc)
    await db.commit()
    return _to_read(snapshot)
