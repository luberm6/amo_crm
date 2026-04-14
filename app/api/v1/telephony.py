from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_auth import require_admin_auth
from app.api.deps import get_db
from app.core.exceptions import AppError
from app.core.config import settings
from app.models.agent_profile import AgentProfile
from app.models.telephony_line import TelephonyLine
from app.repositories.agent_profile_repo import AgentProfileRepository
from app.repositories.telephony_line_repo import TelephonyLineRepository
from app.schemas.telephony import (
    MangoReadinessRead,
    MangoResolveInboundRequest,
    MangoResolveInboundResult,
    MangoResolveOutboundResult,
    MangoRoutingMapItem,
    MangoRoutingMapRead,
    TelephonyExtensionListRead,
    TelephonyExtensionRead,
    TelephonyLineListRead,
    TelephonyLineRead,
    TelephonyLineSyncRead,
)
from app.services.mango_telephony_service import MangoTelephonyService
from app.services.telephony_routing_service import TelephonyRoutingService

router = APIRouter(
    prefix="/telephony",
    tags=["telephony"],
    dependencies=[Depends(require_admin_auth)],
)


def _handle_app_error(exc: AppError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())


@router.get("/mango/readiness", response_model=MangoReadinessRead)
async def mango_readiness() -> MangoReadinessRead:
    api_configured = bool(settings.mango_api_key and settings.mango_api_salt)
    webhook_secret_configured = bool(settings.mango_webhook_secret or settings.mango_webhook_shared_secret)
    from_ext_configured = bool(settings.mango_from_ext)

    warnings: list[str] = []
    if not api_configured:
        warnings.append("Mango API credentials (MANGO_API_KEY / MANGO_API_SALT) are not configured.")
    if not webhook_secret_configured:
        warnings.append("Inbound webhook verification is not configured (MANGO_WEBHOOK_SECRET is empty).")
    if not from_ext_configured:
        warnings.append("Outbound calling is not configured (MANGO_FROM_EXT is empty).")

    return MangoReadinessRead(
        api_configured=api_configured,
        webhook_secret_configured=webhook_secret_configured,
        from_ext_configured=from_ext_configured,
        warnings=warnings,
    )


@router.get("/mango/lines", response_model=TelephonyLineListRead)
async def list_mango_lines(
    db: AsyncSession = Depends(get_db),
) -> TelephonyLineListRead:
    service = MangoTelephonyService(db)
    try:
        items = await service.list_lines(active_only=None)
    finally:
        await service.aclose()
    return TelephonyLineListRead(items=[TelephonyLineRead.model_validate(item) for item in items], total=len(items))


@router.post("/mango/sync-lines", response_model=TelephonyLineSyncRead)
async def sync_mango_lines(
    db: AsyncSession = Depends(get_db),
) -> TelephonyLineSyncRead:
    service = MangoTelephonyService(db)
    try:
        result = await service.sync_lines()
    except AppError as exc:
        _handle_app_error(exc)
    finally:
        await service.aclose()
    await db.commit()
    return TelephonyLineSyncRead(
        items=[TelephonyLineRead.model_validate(item) for item in result.items],
        total=len(result.items),
        synced_count=result.synced_count,
        deactivated_count=result.deactivated_count,
        synced_at=result.synced_at,
    )


@router.get("/mango/extensions", response_model=TelephonyExtensionListRead)
async def list_mango_extensions(
    db: AsyncSession = Depends(get_db),
) -> TelephonyExtensionListRead:
    service = MangoTelephonyService(db)
    try:
        items = await service.list_extensions()
    except AppError as exc:
        _handle_app_error(exc)
    finally:
        await service.aclose()
    return TelephonyExtensionListRead(
        items=[
            TelephonyExtensionRead(
                provider_resource_id=item.provider_resource_id,
                extension=item.extension,
                display_name=item.display_name,
                line_provider_resource_id=item.line_provider_resource_id,
                line_phone_number=item.line_phone_number,
            )
            for item in items
        ],
        total=len(items),
    )


@router.get("/mango/routing-map", response_model=MangoRoutingMapRead)
async def mango_routing_map(
    db: AsyncSession = Depends(get_db),
) -> MangoRoutingMapRead:
    """Return all Mango telephony lines with their bound agents (if any)."""
    line_repo = TelephonyLineRepository(TelephonyLine, db)
    agent_repo = AgentProfileRepository(AgentProfile, db)

    lines = await line_repo.list_lines(provider="mango")

    # Build a map of line_id → first active agent for that line
    items: list[MangoRoutingMapItem] = []
    for line in lines:
        candidates = await agent_repo.get_all_active_by_telephony_line(
            telephony_provider="mango",
            telephony_line_id=line.id,
        )
        agent = candidates[0] if candidates else None
        items.append(
            MangoRoutingMapItem(
                line_id=line.id,
                provider_resource_id=line.provider_resource_id,
                remote_line_id=line.remote_line_id,
                phone_number=line.phone_number,
                schema_name=line.schema_name,
                display_name=line.display_name,
                label=line.label,
                is_active=line.is_active,
                is_inbound_enabled=line.is_inbound_enabled,
                agent_id=agent.id if agent else None,
                agent_name=agent.name if agent else None,
                agent_is_active=agent.is_active if agent else None,
            )
        )

    return MangoRoutingMapRead(items=items, total=len(items))


@router.post("/mango/debug/resolve-inbound", response_model=MangoResolveInboundResult)
async def mango_debug_resolve_inbound(
    body: MangoResolveInboundRequest,
    db: AsyncSession = Depends(get_db),
) -> MangoResolveInboundResult:
    """Dry-run: resolve an inbound phone number to the agent that would handle it."""
    svc = TelephonyRoutingService(db)
    result = await svc.resolve_inbound(provider="mango", phone_number=body.phone_number)
    return MangoResolveInboundResult(
        phone_number_input=body.phone_number,
        phone_number_normalized=result.phone_number_normalized,
        line_found=result.telephony_line is not None,
        line_id=result.telephony_line.id if result.telephony_line else None,
        remote_line_id=result.telephony_line.remote_line_id if result.telephony_line else None,
        line_phone_number=result.telephony_line.phone_number if result.telephony_line else None,
        line_schema_name=result.telephony_line.schema_name if result.telephony_line else None,
        line_display_name=result.telephony_line.display_name if result.telephony_line else None,
        line_label=result.telephony_line.label if result.telephony_line else None,
        agent_found=result.agent is not None,
        agent_id=result.agent.id if result.agent else None,
        agent_name=result.agent.name if result.agent else None,
        ambiguous=result.ambiguous,
        candidate_count=result.candidate_count,
    )


@router.get("/mango/debug/resolve-outbound/{agent_id}", response_model=MangoResolveOutboundResult)
async def mango_debug_resolve_outbound(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
) -> MangoResolveOutboundResult:
    """Dry-run: resolve which Mango line an agent would use for outbound originate."""
    svc = TelephonyRoutingService(db)
    binding = await svc.resolve_outbound_binding(uuid.UUID(agent_id))
    from_ext_configured = bool(settings.mango_from_ext)

    if binding is None:
        return MangoResolveOutboundResult(
            agent_id=uuid.UUID(agent_id),
            agent_found=False,
            line_found=False,
            from_ext_configured=from_ext_configured,
            originate_ready=False,
            missing_requirements=["agent_not_found_or_inactive"],
        )

    missing: list[str] = []
    line = binding.telephony_line
    if line is None:
        missing.append("agent_has_no_mango_line")
    elif not line.is_active:
        missing.append("selected_mango_line_inactive")
    if not from_ext_configured:
        missing.append("mango_from_ext_missing")

    return MangoResolveOutboundResult(
        agent_id=binding.agent.id,
        agent_found=True,
        agent_name=binding.agent.name,
        agent_is_active=binding.agent.is_active,
        telephony_provider=binding.agent.telephony_provider,
        line_found=line is not None,
        line_id=line.id if line else None,
        remote_line_id=line.remote_line_id if line else None,
        line_phone_number=line.phone_number if line else None,
        line_schema_name=line.schema_name if line else None,
        line_display_name=line.display_name if line else None,
        line_label=line.label if line else None,
        line_is_active=line.is_active if line else None,
        from_ext_configured=from_ext_configured,
        originate_ready=not missing,
        missing_requirements=missing,
    )
