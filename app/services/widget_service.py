"""
Widget service — business logic for embeddable voice widget.

Handles:
- Widget config lookup and origin validation
- Rate limiting per widget token and IP
- Session creation (delegates to existing browser call infrastructure)
- Lead capture and async delivery (webhook + Telegram)
"""
from __future__ import annotations

import fnmatch
import secrets
import uuid
from typing import TYPE_CHECKING, Optional
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import RateLimitError
from app.core.logging import get_logger
from app.core.rate_limit import RateLimiter
from app.models.call import CallMode
from app.models.widget import WidgetConfig, WidgetLead
from app.repositories.widget_repo import WidgetLeadRepository, WidgetRepository
from app.schemas.widget import WidgetLeadSubmit, WidgetSessionResponse

if TYPE_CHECKING:
    from redis.asyncio import Redis

log = get_logger(__name__)


def generate_widget_token() -> str:
    return "wgt_" + secrets.token_urlsafe(24)


class WidgetService:
    def __init__(self, session: AsyncSession, redis: Optional["Redis"]) -> None:
        self._session = session
        self._redis = redis
        self._widget_repo = WidgetRepository(session)
        self._lead_repo = WidgetLeadRepository(session)
        self._limiter = RateLimiter(redis)

    async def get_widget_by_token(self, token: str) -> WidgetConfig:
        widget = await self._widget_repo.get_by_token(token)
        if widget is None or not widget.is_active:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Widget not found")
        return widget

    async def validate_origin(self, widget: WidgetConfig, origin: str) -> None:
        """Allow all when no domains configured; enforce fnmatch otherwise."""
        if not widget.allowed_domains:
            return
        if not origin:
            return  # non-browser clients (curl, server-side) are allowed
        try:
            hostname = urlparse(origin).hostname or ""
        except Exception:
            hostname = ""
        for pattern in widget.allowed_domains:
            if fnmatch.fnmatch(hostname, pattern):
                return
        log.warning(
            "widget.origin_rejected",
            widget_id=str(widget.id),
            origin=origin,
            allowed_domains=widget.allowed_domains,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Origin not allowed for this widget",
        )

    async def check_rate_limits(self, widget: WidgetConfig, ip: str) -> None:
        token = widget.widget_token
        try:
            await self._limiter.check_fixed_window(
                key=f"rl:widget:{token}",
                limit=widget.rate_limit_per_hour,
                window_seconds=3600,
                label=f"widget:{token}",
            )
            await self._limiter.check_fixed_window(
                key=f"rl:widget_ip:{token}:{ip}",
                limit=widget.rate_limit_per_ip_per_hour,
                window_seconds=3600,
                label=f"widget_ip:{token}:{ip}",
            )
        except RateLimitError as exc:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=str(exc),
            ) from exc

    async def create_session(
        self,
        widget: WidgetConfig,
        call_service,
        registry,
        session_manager,
        request: Request,
    ) -> WidgetSessionResponse:
        from app.core.exceptions import AppError

        try:
            call = await call_service.create_call(
                raw_phone="widget",
                mode=CallMode.BROWSER,
                actor=f"widget:{widget.id}",
                agent_profile_id=widget.agent_profile_id,
            )
        except AppError as exc:
            log.error("widget.session_creation_failed", widget_id=str(widget.id), error=str(exc))
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Failed to create voice session",
            ) from exc

        bridge = registry.get_bridge(call.id)
        if bridge is None or not call.mango_call_id:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Voice bridge not initialized",
            )

        original_proto = (
            request.headers.get("x-original-proto")
            or request.headers.get("x-forwarded-proto")
            or ""
        ).strip()
        original_host = (
            request.headers.get("x-original-host")
            or request.headers.get("x-forwarded-host")
            or ""
        ).strip()
        if original_proto and original_host:
            scheme, netloc = original_proto, original_host
        else:
            scheme, netloc = request.url.scheme, request.url.netloc

        ws_scheme = "wss" if scheme == "https" else "ws"
        ws_url = f"{ws_scheme}://{netloc}/v1/browser-calls/{call.id}/ws?token={bridge.token}"

        log.info(
            "widget.session_created",
            widget_id=str(widget.id),
            call_id=str(call.id),
            agent_profile_id=str(widget.agent_profile_id),
        )

        return WidgetSessionResponse(
            call_id=call.id,
            browser_token=bridge.token,
            websocket_url=ws_url,
        )

    async def submit_lead(self, widget: WidgetConfig, body: WidgetLeadSubmit) -> WidgetLead:
        lead = WidgetLead(
            widget_id=widget.id,
            call_id=body.call_id,
            name=body.name,
            email=body.email,
            phone=body.phone,
            extra_fields=body.extra_fields or None,
        )
        await self._lead_repo.save(lead)
        log.info(
            "widget.lead_submitted",
            widget_id=str(widget.id),
            lead_id=str(lead.id),
            call_id=str(body.call_id),
        )
        return lead

    async def deliver_lead_webhook(
        self, lead: WidgetLead, widget: WidgetConfig, transcript: Optional[str]
    ) -> None:
        if not widget.webhook_url:
            return
        payload = {
            "widget_id": str(widget.id),
            "lead_id": str(lead.id),
            "call_id": str(lead.call_id) if lead.call_id else None,
            "lead": {
                "name": lead.name,
                "email": lead.email,
                "phone": lead.phone,
                "extra_fields": lead.extra_fields,
            },
            "transcript_summary": transcript,
        }
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    widget.webhook_url,
                    json=payload,
                    timeout=settings.widget_webhook_timeout_seconds,
                )
                resp.raise_for_status()
            lead.webhook_delivered = True
            await self._session.flush()
            log.info("widget.webhook_delivered", lead_id=str(lead.id), url=widget.webhook_url)
        except Exception as exc:
            log.warning("widget.webhook_failed", lead_id=str(lead.id), error=str(exc))

    async def deliver_lead_telegram(self, lead: WidgetLead, widget: WidgetConfig) -> None:
        if not widget.telegram_chat_id:
            return
        bot_token = settings.telegram_bot_token
        if not bot_token:
            log.warning("widget.telegram_no_bot_token", lead_id=str(lead.id))
            return

        parts = ["📞 Новый лид с виджета"]
        if lead.name:
            parts.append(f"Имя: {lead.name}")
        if lead.email:
            parts.append(f"Email: {lead.email}")
        if lead.phone:
            parts.append(f"Телефон: {lead.phone}")
        if lead.extra_fields:
            for k, v in lead.extra_fields.items():
                parts.append(f"{k}: {v}")
        text = "\n".join(parts)

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    url,
                    json={"chat_id": widget.telegram_chat_id, "text": text},
                    timeout=10.0,
                )
                resp.raise_for_status()
            lead.telegram_delivered = True
            await self._session.flush()
            log.info("widget.telegram_delivered", lead_id=str(lead.id))
        except Exception as exc:
            log.warning("widget.telegram_failed", lead_id=str(lead.id), error=str(exc))
