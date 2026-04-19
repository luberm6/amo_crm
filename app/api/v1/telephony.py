from __future__ import annotations

import uuid
from ipaddress import ip_address, ip_network
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_auth import require_admin_auth
from app.api.deps import get_call_service, get_db
from app.core.exceptions import AppError
from app.core.config import settings
from app.integrations.telephony.mango_runtime import resolve_mango_from_ext
from app.models.agent_profile import AgentProfile
from app.models.call import CallMode
from app.models.telephony_line import TelephonyLine
from app.repositories.agent_profile_repo import AgentProfileRepository
from app.repositories.telephony_line_repo import TelephonyLineRepository
from app.schemas.telephony import (
    MangoActionableNextStep,
    MangoRenderReadinessSummary,
    MangoReadinessRead,
    MangoRouteReadinessScope,
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
    TelephonyOutboundCallRequest,
    TelephonyOutboundCallResponse,
)
from app.services.call_service import CallService
from app.services.mango_telephony_service import MangoTelephonyService
from app.services.telephony_routing_service import TelephonyRoutingService

router = APIRouter(
    prefix="/telephony",
    tags=["telephony"],
    dependencies=[Depends(require_admin_auth)],
)


def _handle_app_error(exc: AppError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())


def _normalize_outbound_mode(value: str) -> CallMode:
    normalized = (value or "").strip().lower()
    if normalized != CallMode.DIRECT.value:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "unsupported_mode",
                "message": "Only DIRECT mode is supported by this endpoint.",
                "detail": {"mode": value},
            },
        )
    return CallMode.DIRECT


def _is_public_backend_url(url: str) -> bool:
    parsed = urlparse((url or "").strip())
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return False
    if host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local"):
        return False
    try:
        ip = ip_address(host)
    except ValueError:
        return True
    private_ranges = (
        ip_network("127.0.0.0/8"),
        ip_network("10.0.0.0/8"),
        ip_network("172.16.0.0/12"),
        ip_network("192.168.0.0/16"),
        ip_network("169.254.0.0/16"),
        ip_network("::1/128"),
        ip_network("fc00::/7"),
        ip_network("fe80::/10"),
    )
    return not any(ip in net for net in private_ranges)


def _is_real_network_host(value: str) -> bool:
    host = (value or "").strip().lower()
    if not host:
        return False
    if host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local"):
        return False
    try:
        ip = ip_address(host)
    except ValueError:
        return True
    private_ranges = (
        ip_network("127.0.0.0/8"),
        ip_network("10.0.0.0/8"),
        ip_network("172.16.0.0/12"),
        ip_network("192.168.0.0/16"),
        ip_network("169.254.0.0/16"),
        ip_network("::1/128"),
        ip_network("fc00::/7"),
        ip_network("fe80::/10"),
    )
    return not any(ip in net for net in private_ranges)


def _has_real_freeswitch_host(value: str) -> bool:
    return _is_real_network_host(value)


def _has_real_freeswitch_password(value: str) -> bool:
    password = (value or "").strip()
    if not password:
        return False
    return password not in {"ClueCon", "changeme", "password", "default"}


def _has_real_freeswitch_rtp_ip(value: str) -> bool:
    return _is_real_network_host(value)


def _resolve_direct_runtime_provider() -> tuple[str, bool]:
    preferred = (settings.telephony_provider or "auto").strip().lower() or "auto"
    if (
        preferred == "stub"
        and settings.mango_configured
        and settings.gemini_configured
        and not settings.is_testing
    ):
        return "mango", True
    if preferred == "mango":
        return "mango", bool(settings.mango_configured)
    if preferred == "stub":
        return preferred, preferred != "stub"
    if preferred == "auto":
        return ("mango", True) if settings.mango_configured else ("stub", False)
    return preferred, preferred not in {"stub", ""}


def _requirements_to_blockers(
    requirement_keys: list[str],
    present: set[str],
) -> list[str]:
    mapping = {
        "mango_api_credentials_missing": "Mango API credentials are missing.",
        "mango_webhook_secret_missing": "Webhook secret is missing.",
        "backend_url_not_public": "BACKEND_URL is not public.",
        "mango_from_ext_missing": "FROM_EXT is not configured and no stable fallback is available.",
        "telephony_runtime_not_real": "Telephony runtime is not using a real Mango route.",
        "media_gateway_disabled": "MEDIA_GATEWAY_ENABLED=false.",
        "media_gateway_provider_not_freeswitch": "MEDIA_GATEWAY_PROVIDER must be freeswitch.",
        "media_gateway_mode_not_supported": "MEDIA_GATEWAY_MODE must be mock or esl_rtp.",
        "freeswitch_esl_host_missing": "FREESWITCH_ESL_HOST is missing or local-only.",
        "freeswitch_esl_password_missing": "FREESWITCH_ESL_PASSWORD is missing or still default.",
        "freeswitch_rtp_ip_missing": "FREESWITCH_RTP_IP is missing or local-only.",
    }
    return [mapping[key] for key in requirement_keys if key in present]


def _build_route_readiness(
    *,
    inbound_webhook_smoke_ready: bool,
    outbound_originate_smoke_ready: bool,
    inbound_ai_runtime_ready: bool,
    missing_requirements: list[str],
) -> tuple[dict[str, MangoRouteReadinessScope], MangoRenderReadinessSummary]:
    present = set(missing_requirements)
    route_readiness = {
        "inbound_webhook": MangoRouteReadinessScope(
            key="inbound_webhook",
            ready=inbound_webhook_smoke_ready,
            status="ready" if inbound_webhook_smoke_ready else "blocked",
            summary=(
                "Render can receive and verify Mango webhook delivery."
                if inbound_webhook_smoke_ready
                else "Render webhook delivery is not ready yet."
            ),
            blockers=_requirements_to_blockers(
                [
                    "mango_api_credentials_missing",
                    "mango_webhook_secret_missing",
                    "backend_url_not_public",
                ],
                present,
            ),
        ),
        "outbound_originate": MangoRouteReadinessScope(
            key="outbound_originate",
            ready=outbound_originate_smoke_ready,
            status="ready" if outbound_originate_smoke_ready else "blocked",
            summary=(
                "Agent-bound Mango lines can run an outbound originate smoke."
                if outbound_originate_smoke_ready
                else "Outbound originate smoke is still blocked."
            ),
            blockers=_requirements_to_blockers(
                [
                    "mango_api_credentials_missing",
                    "mango_from_ext_missing",
                    "telephony_runtime_not_real",
                ],
                present,
            ),
        ),
        "inbound_ai_runtime": MangoRouteReadinessScope(
            key="inbound_ai_runtime",
            ready=inbound_ai_runtime_ready,
            status="ready" if inbound_ai_runtime_ready else "blocked",
            summary=(
                "Inbound Mango webhook can reach a bound AI runtime."
                if inbound_ai_runtime_ready
                else "Inbound AI runtime is still blocked."
            ),
            blockers=_requirements_to_blockers(
                [
                    "mango_api_credentials_missing",
                    "mango_webhook_secret_missing",
                    "backend_url_not_public",
                    "media_gateway_disabled",
                    "media_gateway_provider_not_freeswitch",
                    "media_gateway_mode_not_supported",
                    "freeswitch_esl_host_missing",
                    "freeswitch_esl_password_missing",
                    "freeswitch_rtp_ip_missing",
                ],
                present,
            ),
        ),
    }
    ready_count = sum(1 for item in route_readiness.values() if item.ready)
    blocked_count = len(route_readiness) - ready_count
    overall_status = "ready" if blocked_count == 0 else "partial" if ready_count > 0 else "blocked"
    operator_summary = {
        "ready": "Render-side Mango routing is ready for webhook and originate smoke checks.",
        "partial": "Render-side Mango routing is partially ready. Check the blocked cards before live smoke.",
        "blocked": "Render-side Mango routing is blocked. Fix the listed blockers before live smoke.",
    }[overall_status]
    return route_readiness, MangoRenderReadinessSummary(
        ready_count=ready_count,
        blocked_count=blocked_count,
        overall_status=overall_status,
        operator_summary=operator_summary,
    )


def _build_actionable_next_step(
    *,
    missing_requirements: list[str],
    render_summary: MangoRenderReadinessSummary,
) -> MangoActionableNextStep:
    priorities: list[tuple[str, MangoActionableNextStep]] = [
        (
            "mango_api_credentials_missing",
            MangoActionableNextStep(
                key="configure_mango_credentials",
                title="Save Mango API credentials",
                description="Set MANGO_API_KEY and MANGO_API_SALT before trying to sync lines or run live routing checks.",
                cta_label="Set MANGO_API_KEY and MANGO_API_SALT",
                scope="global",
            ),
        ),
        (
            "backend_url_not_public",
            MangoActionableNextStep(
                key="make_backend_url_public",
                title="Make BACKEND_URL public",
                description="Mango cannot deliver a webhook to a local or private BACKEND_URL. Point it to the public Render backend URL.",
                cta_label="Set a public BACKEND_URL",
                scope="inbound_webhook",
            ),
        ),
        (
            "mango_webhook_secret_missing",
            MangoActionableNextStep(
                key="set_mango_webhook_secret",
                title="Set webhook verification secret",
                description="Configure MANGO_WEBHOOK_SECRET or MANGO_WEBHOOK_SHARED_SECRET before testing inbound webhook delivery.",
                cta_label="Set MANGO_WEBHOOK_SECRET",
                scope="inbound_webhook",
            ),
        ),
        (
            "mango_from_ext_missing",
            MangoActionableNextStep(
                key="set_mango_from_ext",
                title="Set outbound source extension",
                description="Outbound originate still needs a stable source extension when auto-discovery is unavailable.",
                cta_label="Set MANGO_FROM_EXT",
                scope="outbound_originate",
            ),
        ),
        (
            "telephony_runtime_not_real",
            MangoActionableNextStep(
                key="use_real_mango_runtime",
                title="Switch telephony runtime to Mango",
                description="The current telephony runtime is not using a real Mango route, so originate smoke would not hit PSTN.",
                cta_label="Set TELEPHONY_PROVIDER=mango",
                scope="outbound_originate",
            ),
        ),
        (
            "media_gateway_disabled",
            MangoActionableNextStep(
                key="enable_media_gateway",
                title="Enable media gateway",
                description="Inbound AI runtime requires MEDIA_GATEWAY_ENABLED=true before Mango inbound calls can reach the AI voice path.",
                cta_label="Enable MEDIA_GATEWAY_ENABLED",
                scope="inbound_ai_runtime",
            ),
        ),
        (
            "media_gateway_provider_not_freeswitch",
            MangoActionableNextStep(
                key="set_freeswitch_gateway_provider",
                title="Use FreeSWITCH media gateway",
                description="Inbound AI runtime currently expects MEDIA_GATEWAY_PROVIDER=freeswitch.",
                cta_label="Set MEDIA_GATEWAY_PROVIDER=freeswitch",
                scope="inbound_ai_runtime",
            ),
        ),
        (
            "media_gateway_mode_not_supported",
            MangoActionableNextStep(
                key="set_supported_gateway_mode",
                title="Use a supported media gateway mode",
                description="Inbound AI runtime currently supports MEDIA_GATEWAY_MODE=mock or esl_rtp.",
                cta_label="Set MEDIA_GATEWAY_MODE=mock or esl_rtp",
                scope="inbound_ai_runtime",
            ),
        ),
        (
            "freeswitch_esl_host_missing",
            MangoActionableNextStep(
                key="set_freeswitch_esl_host",
                title="Set FreeSWITCH ESL host",
                description="Inbound AI runtime cannot attach to a real media gateway until FREESWITCH_ESL_HOST points to your reachable FreeSWITCH instance.",
                cta_label="Set FREESWITCH_ESL_HOST",
                scope="inbound_ai_runtime",
            ),
        ),
        (
            "freeswitch_esl_password_missing",
            MangoActionableNextStep(
                key="set_freeswitch_esl_password",
                title="Set FreeSWITCH ESL password",
                description="Inbound AI runtime still uses a missing or default ESL password. Replace it with the real FreeSWITCH event socket password.",
                cta_label="Set FREESWITCH_ESL_PASSWORD",
                scope="inbound_ai_runtime",
            ),
        ),
        (
            "freeswitch_rtp_ip_missing",
            MangoActionableNextStep(
                key="set_freeswitch_rtp_ip",
                title="Set FreeSWITCH RTP IP",
                description="Inbound AI runtime still points RTP at a local-only address. Set FREESWITCH_RTP_IP to the reachable media IP.",
                cta_label="Set FREESWITCH_RTP_IP",
                scope="inbound_ai_runtime",
            ),
        ),
    ]
    present = set(missing_requirements)
    for requirement, step in priorities:
        if requirement in present:
            return step

    if render_summary.overall_status == "ready":
        return MangoActionableNextStep(
            key="run_live_smoke",
            title="Run a live Mango smoke check",
            description="Render-side readiness is green. The next honest step is one real webhook delivery or originate smoke.",
            cta_label="Run one live smoke check",
            scope="global",
        )

    return MangoActionableNextStep(
        key="review_blocked_cards",
        title="Review blocked routing cards",
        description="Read the blocked cards below and clear the first blocker before attempting a live smoke.",
        cta_label="Clear the first blocked card",
        scope="global",
    )


@router.post("/outbound-call", response_model=TelephonyOutboundCallResponse)
async def create_outbound_call(
    body: TelephonyOutboundCallRequest,
    session: AsyncSession = Depends(get_db),
    service: CallService = Depends(get_call_service),
) -> TelephonyOutboundCallResponse:
    mode = _normalize_outbound_mode(body.mode)
    result = await session.execute(
        select(AgentProfile)
        .where(AgentProfile.name == body.agent_name)
        .where(AgentProfile.is_active.is_(True))
        .limit(1)
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        return TelephonyOutboundCallResponse(
            accepted=False,
            provider="mango",
            agent=body.agent_name,
            mode=mode.value.upper(),
            status="agent_not_found",
            error={
                "error": "agent_not_found",
                "message": f"Active agent '{body.agent_name}' not found.",
            },
        )

    if (agent.telephony_provider or "").strip().lower() != "mango":
        return TelephonyOutboundCallResponse(
            accepted=False,
            provider="mango",
            agent=agent.name,
            mode=mode.value.upper(),
            status="agent_provider_mismatch",
            error={
                "error": "agent_provider_mismatch",
                "message": f"Agent '{agent.name}' is not configured for Mango telephony.",
            },
        )

    try:
        call = await service.create_call(
            raw_phone=body.phone_number,
            mode=mode,
            actor="telephony_api",
            agent_profile_id=agent.id,
        )
    except AppError as exc:
        return TelephonyOutboundCallResponse(
            accepted=False,
            provider="mango",
            agent=agent.name,
            mode=mode.value.upper(),
            status="failed",
            error=exc.to_dict(),
        )

    return TelephonyOutboundCallResponse(
        accepted=True,
        provider="mango",
        agent=agent.name,
        mode=mode.value.upper(),
        status=(getattr(call.status, "value", str(call.status)) or "").lower(),
        call_id=call.id,
        error=None,
    )


@router.get("/mango/readiness", response_model=MangoReadinessRead)
async def mango_readiness() -> MangoReadinessRead:
    api_configured = bool(settings.mango_api_key and settings.mango_api_salt)
    webhook_secret_configured = bool(settings.mango_webhook_secret or settings.mango_webhook_shared_secret)
    from_ext_configured = bool(settings.mango_from_ext)
    direct_runtime_provider, telephony_runtime_real = _resolve_direct_runtime_provider()
    backend_url = settings.effective_backend_url
    webhook_url = f"{backend_url}/v1/webhooks/mango" if backend_url else "/v1/webhooks/mango"
    webhook_url_public = _is_public_backend_url(backend_url)
    freeswitch_esl_host_configured = _has_real_freeswitch_host(settings.freeswitch_esl_host)
    freeswitch_esl_password_configured = _has_real_freeswitch_password(settings.freeswitch_esl_password)
    freeswitch_rtp_ip_configured = _has_real_freeswitch_rtp_ip(settings.freeswitch_rtp_ip)
    media_gateway_transport_enabled = bool(
        settings.media_gateway_enabled
        and settings.media_gateway_provider == "freeswitch"
        and settings.media_gateway_mode in {"mock", "esl_rtp"}
    )
    from_ext_auto_discoverable = False
    if api_configured and not from_ext_configured:
        resolved = await resolve_mango_from_ext()
        from_ext_auto_discoverable = bool(resolved.value)

    warnings: list[str] = []
    missing_requirements: list[str] = []
    if not api_configured:
        warnings.append("Mango API credentials (MANGO_API_KEY / MANGO_API_SALT) are not configured.")
        missing_requirements.append("mango_api_credentials_missing")
    if not webhook_secret_configured:
        warnings.append("Inbound webhook verification is not configured (MANGO_WEBHOOK_SECRET is empty).")
        missing_requirements.append("mango_webhook_secret_missing")
    if not webhook_url_public:
        warnings.append("BACKEND_URL is not publicly reachable. Mango cannot deliver a live webhook to this backend yet.")
        missing_requirements.append("backend_url_not_public")
    if not from_ext_configured and not from_ext_auto_discoverable:
        warnings.append("Outbound calling is not configured (MANGO_FROM_EXT is empty).")
        missing_requirements.append("mango_from_ext_missing")
    elif not from_ext_configured and from_ext_auto_discoverable:
        warnings.append("Outbound calling will use an auto-discovered Mango extension because MANGO_FROM_EXT is empty.")
    if not telephony_runtime_real:
        warnings.append("Direct runtime is not wired to a real telephony provider. PSTN originate smoke would not use Mango.")
        missing_requirements.append("telephony_runtime_not_real")
    if not settings.media_gateway_enabled:
        warnings.append("Inbound AI runtime is blocked because MEDIA_GATEWAY_ENABLED=false.")
        missing_requirements.append("media_gateway_disabled")
    if settings.media_gateway_provider != "freeswitch":
        warnings.append("Inbound AI runtime currently expects MEDIA_GATEWAY_PROVIDER=freeswitch.")
        missing_requirements.append("media_gateway_provider_not_freeswitch")
    if settings.media_gateway_mode not in {"mock", "esl_rtp"}:
        warnings.append("Inbound AI runtime currently expects MEDIA_GATEWAY_MODE=mock or esl_rtp.")
        missing_requirements.append("media_gateway_mode_not_supported")
    if media_gateway_transport_enabled and not freeswitch_esl_host_configured:
        warnings.append("Inbound AI runtime is blocked because FREESWITCH_ESL_HOST is missing or still local-only.")
        missing_requirements.append("freeswitch_esl_host_missing")
    if media_gateway_transport_enabled and not freeswitch_esl_password_configured:
        warnings.append("Inbound AI runtime is blocked because FREESWITCH_ESL_PASSWORD is missing or still using the default value.")
        missing_requirements.append("freeswitch_esl_password_missing")
    if media_gateway_transport_enabled and not freeswitch_rtp_ip_configured:
        warnings.append("Inbound AI runtime is blocked because FREESWITCH_RTP_IP is missing or still local-only.")
        missing_requirements.append("freeswitch_rtp_ip_missing")

    inbound_webhook_smoke_ready = bool(api_configured and webhook_secret_configured and webhook_url_public)
    outbound_originate_smoke_ready = bool(
        api_configured
        and telephony_runtime_real
        and (from_ext_configured or from_ext_auto_discoverable)
    )
    inbound_ai_runtime_ready = bool(
        inbound_webhook_smoke_ready
        and settings.gemini_configured
        and settings.media_gateway_enabled
        and settings.media_gateway_provider == "freeswitch"
        and settings.media_gateway_mode in {"mock", "esl_rtp"}
        and freeswitch_esl_host_configured
        and freeswitch_esl_password_configured
        and freeswitch_rtp_ip_configured
    )
    route_readiness, render_summary = _build_route_readiness(
        inbound_webhook_smoke_ready=inbound_webhook_smoke_ready,
        outbound_originate_smoke_ready=outbound_originate_smoke_ready,
        inbound_ai_runtime_ready=inbound_ai_runtime_ready,
        missing_requirements=missing_requirements,
    )
    actionable_next_step = _build_actionable_next_step(
        missing_requirements=missing_requirements,
        render_summary=render_summary,
    )

    return MangoReadinessRead(
        api_configured=api_configured,
        webhook_secret_configured=webhook_secret_configured,
        from_ext_configured=from_ext_configured,
        from_ext_auto_discoverable=from_ext_auto_discoverable,
        telephony_runtime_provider=direct_runtime_provider,
        telephony_runtime_real=telephony_runtime_real,
        backend_url=backend_url,
        webhook_url=webhook_url,
        webhook_url_public=webhook_url_public,
        inbound_webhook_smoke_ready=inbound_webhook_smoke_ready,
        outbound_originate_smoke_ready=outbound_originate_smoke_ready,
        inbound_ai_runtime_ready=inbound_ai_runtime_ready,
        missing_requirements=missing_requirements,
        warnings=warnings,
        route_readiness=route_readiness,
        render_summary=render_summary,
        actionable_next_step=actionable_next_step,
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
        result = await service.list_extensions()
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
            for item in result.items
        ],
        total=len(result.items),
        source=result.source,
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
                is_recommended_for_ai=line.is_recommended_for_ai,
                is_protected=line.is_protected,
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
            resolved_from_ext=None,
            from_ext_source=None,
            originate_ready=False,
            missing_requirements=["agent_not_found_or_inactive"],
        )

    missing: list[str] = []
    line = binding.telephony_line
    if line is None:
        missing.append("agent_has_no_mango_line")
    elif not line.is_active:
        missing.append("selected_mango_line_inactive")
    resolved_from_ext = None
    from_ext_source = None
    if line is not None:
        resolution = await resolve_mango_from_ext(
            explicit_from_ext=(binding.agent.telephony_extension or "").strip() or None,
            metadata={
                "telephony_remote_line_id": line.remote_line_id,
                "telephony_line_phone_number": line.phone_number,
                "telephony_extension": binding.agent.telephony_extension or line.extension,
            },
        )
        resolved_from_ext = resolution.value
        from_ext_source = resolution.source
    if not resolved_from_ext:
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
        resolved_from_ext=resolved_from_ext,
        from_ext_source=from_ext_source,
        originate_ready=not missing,
        missing_requirements=missing,
    )
