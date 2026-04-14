from __future__ import annotations

from typing import Any, Optional

import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.direct.voice_strategy import inspect_voice_strategy
from app.integrations.media_gateway.factory import get_media_gateway
from app.integrations.telephony.mango_runtime import resolve_mango_from_ext
from app.integrations.telephony.registry import build_default_registry

log = get_logger(__name__)


class DirectVoicePreflightService:
    """
    Read-only preflight for the first real Direct voice call.

    Goal:
    - validate critical config
    - validate local dependencies (DB / Redis)
    - validate telephony/media contour wiring without placing a real call
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def run(self) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []

        await self._check_database(checks)
        await self._check_redis(checks)
        self._check_environment(checks)
        await self._check_direct_config(checks)

        adapter = self._resolve_telephony_adapter(checks)
        if adapter is not None:
            self._check_telephony_capabilities(adapter, checks)

        await self._check_media_gateway(checks)

        failed = sum(1 for c in checks if c["status"] == "fail")
        warned = sum(1 for c in checks if c["status"] == "warn")
        passed = sum(1 for c in checks if c["status"] == "pass")

        overall = "pass"
        if failed:
            overall = "fail"
        elif warned:
            overall = "warn"

        return {
            "status": overall,
            "target": "direct_voice_first_call",
            "summary": {
                "passed": passed,
                "warnings": warned,
                "failed": failed,
                "total": len(checks),
            },
            "checks": checks,
        }

    async def _check_database(self, checks: list[dict[str, Any]]) -> None:
        try:
            await self._session.execute(text("SELECT 1"))
            self._add_check(checks, "database", "pass", "Database is reachable.")
        except Exception as exc:
            self._add_check(
                checks,
                "database",
                "fail",
                "Database is not reachable.",
                details={"error": str(exc)},
            )

    async def _check_redis(self, checks: list[dict[str, Any]]) -> None:
        client = None
        try:
            client = aioredis.from_url(settings.redis_url, socket_connect_timeout=2)
            await client.ping()
            self._add_check(checks, "redis", "pass", "Redis is reachable.")
        except Exception as exc:
            self._add_check(
                checks,
                "redis",
                "fail",
                "Redis is not reachable.",
                details={"error": str(exc)},
            )
        finally:
            if client is not None:
                try:
                    await client.aclose()
                except Exception:
                    pass

    def _check_environment(self, checks: list[dict[str, Any]]) -> None:
        if settings.is_production:
            self._add_check(checks, "environment", "pass", "Production mode is enabled.")
        else:
            self._add_check(
                checks,
                "environment",
                "warn",
                "Environment is not production. Preflight is less representative.",
                details={"environment": settings.environment},
            )

        if settings.backend_url.startswith("https://"):
            self._add_check(checks, "backend_url", "pass", "Backend URL uses HTTPS.")
        else:
            self._add_check(
                checks,
                "backend_url",
                "warn",
                "Backend URL is not HTTPS. Webhook delivery may fail in production.",
                details={"backend_url": settings.backend_url},
            )

    async def _check_direct_config(self, checks: list[dict[str, Any]]) -> None:
        for check in inspect_voice_strategy():
            self._add_check(
                checks,
                check.name,
                check.status,
                check.message,
                details=check.details,
            )

        if settings.telephony_provider == "stub":
            self._add_check(
                checks,
                "telephony_provider",
                "fail",
                "TELEPHONY_PROVIDER=stub cannot make real calls.",
            )
        else:
            self._add_check(
                checks,
                "telephony_provider",
                "pass",
                "Telephony provider is configured for a real route.",
                details={"provider": settings.telephony_provider},
            )

        if settings.gemini_configured:
            self._add_check(checks, "gemini_api", "pass", "Gemini API key is configured.")
        else:
            self._add_check(
                checks,
                "gemini_api",
                "fail",
                "GEMINI_API_KEY is missing.",
            )

        if settings.mango_configured:
            self._add_check(checks, "mango_credentials", "pass", "Mango credentials are configured.")
        else:
            self._add_check(
                checks,
                "mango_credentials",
                "fail",
                "Mango credentials are missing.",
            )

        if settings.mango_from_ext:
            self._add_check(
                checks,
                "mango_from_ext",
                "pass",
                "MANGO_FROM_EXT is configured.",
            )
        else:
            resolved = None
            if settings.mango_configured:
                try:
                    resolved = await resolve_mango_from_ext()
                except Exception as exc:
                    log.warning(
                        "preflight.mango_from_ext_auto_discovery_failed",
                        error=str(exc),
                    )
            if resolved and resolved.value:
                self._add_check(
                    checks,
                    "mango_from_ext",
                    "warn",
                    "MANGO_FROM_EXT is empty, but runtime can auto-discover a usable Mango source extension.",
                    details={
                        "resolved_from_ext": resolved.value,
                        "source": resolved.source,
                        "candidate_count": resolved.candidate_count,
                        "matched_line_id": resolved.matched_line_id,
                        "matched_line_phone_number": resolved.matched_line_phone_number,
                    },
                )
            else:
                self._add_check(
                    checks,
                    "mango_from_ext",
                    "fail",
                    "MANGO_FROM_EXT is missing and runtime could not auto-discover a usable Mango source extension.",
                )

        if settings.media_gateway_enabled and settings.media_gateway_provider == "freeswitch":
            self._add_check(
                checks,
                "media_gateway",
                "pass",
                "FreeSWITCH media gateway is enabled.",
                details={"mode": settings.media_gateway_mode},
            )
        else:
            self._add_check(
                checks,
                "media_gateway",
                "fail",
                "Real Direct voice requires MEDIA_GATEWAY_ENABLED=true and MEDIA_GATEWAY_PROVIDER=freeswitch.",
            )

        if settings.media_gateway_mode == "esl_rtp":
            self._add_check(
                checks,
                "media_gateway_mode",
                "pass",
                "FreeSWITCH media gateway mode is esl_rtp.",
            )
        else:
            self._add_check(
                checks,
                "media_gateway_mode",
                "fail",
                "First real Direct voice call requires MEDIA_GATEWAY_MODE=esl_rtp.",
                details={"mode": settings.media_gateway_mode},
            )

        if settings.direct_initial_greeting_enabled:
            self._add_check(
                checks,
                "initial_greeting",
                "pass",
                "Initial greeting is enabled.",
            )
        else:
            self._add_check(
                checks,
                "initial_greeting",
                "warn",
                "Initial greeting is disabled. The first live call may start in silence.",
            )

        if settings.direct_model_response_timeout_seconds > 0:
            self._add_check(
                checks,
                "model_timeout",
                "pass",
                "Model response timeout is configured.",
                details={"seconds": settings.direct_model_response_timeout_seconds},
            )
        else:
            self._add_check(
                checks,
                "model_timeout",
                "warn",
                "Model response timeout is disabled.",
            )

    def _resolve_telephony_adapter(self, checks: list[dict[str, Any]]) -> Optional[Any]:
        try:
            registry = build_default_registry()
            adapter = registry.resolve(settings.telephony_provider)
            provider_name = getattr(getattr(adapter, "capabilities", None), "provider_name", type(adapter).__name__)
            if provider_name == "stub":
                self._add_check(
                    checks,
                    "telephony_resolution",
                    "fail",
                    "Telephony provider resolved to stub. A real provider is still not configured.",
                    details={
                        "requested_provider": settings.telephony_provider,
                        "resolved_provider": provider_name,
                    },
                )
                return None
            self._add_check(
                checks,
                "telephony_resolution",
                "pass",
                "Telephony adapter resolved successfully.",
                details={
                    "adapter": type(adapter).__name__,
                    "provider": provider_name,
                },
            )
            return adapter
        except Exception as exc:
            self._add_check(
                checks,
                "telephony_resolution",
                "fail",
                "Telephony adapter could not be resolved.",
                details={"error": str(exc)},
            )
            return None

    def _check_telephony_capabilities(self, adapter: Any, checks: list[dict[str, Any]]) -> None:
        caps = adapter.capabilities

        if caps.supports_outbound_call:
            self._add_check(
                checks,
                "telephony_outbound",
                "pass",
                "Telephony adapter supports outbound calls.",
            )
        else:
            self._add_check(
                checks,
                "telephony_outbound",
                "fail",
                "Telephony adapter does not support outbound calls.",
            )

        if caps.supports_audio_bridge:
            self._add_check(
                checks,
                "telephony_audio_bridge",
                "pass",
                "Telephony adapter supports audio bridge attachment.",
            )
        else:
            self._add_check(
                checks,
                "telephony_audio_bridge",
                "fail",
                "Telephony adapter does not support audio bridge attachment.",
            )

    async def _check_media_gateway(self, checks: list[dict[str, Any]]) -> None:
        if not settings.media_gateway_enabled or settings.media_gateway_provider != "freeswitch":
            return

        try:
            gateway = get_media_gateway()
        except Exception as exc:
            self._add_check(
                checks,
                "freeswitch_gateway_init",
                "fail",
                "FreeSWITCH media gateway could not be created.",
                details={"error": str(exc)},
            )
            return

        self._add_check(
            checks,
            "freeswitch_gateway_init",
            "pass",
            "FreeSWITCH media gateway instance created.",
            details={"gateway": type(gateway).__name__},
        )

        ensure_connected = getattr(gateway, "_ensure_esl_connected", None)
        health_fn = getattr(gateway, "health", None)
        if not callable(ensure_connected) or not callable(health_fn):
            self._add_check(
                checks,
                "freeswitch_esl",
                "warn",
                "Media gateway does not expose ESL health hooks; ESL preflight skipped.",
            )
            return

        try:
            await ensure_connected()
            health = await health_fn()
            if health.get("esl_connected"):
                self._add_check(
                    checks,
                    "freeswitch_esl",
                    "pass",
                    "FreeSWITCH ESL connection succeeded.",
                    details=health,
                )
            else:
                self._add_check(
                    checks,
                    "freeswitch_esl",
                    "fail",
                    "FreeSWITCH ESL did not report a connected state.",
                    details=health,
                )
        except Exception as exc:
            self._add_check(
                checks,
                "freeswitch_esl",
                "fail",
                "FreeSWITCH ESL connection failed.",
                details={"error": str(exc)},
            )

    @staticmethod
    def _add_check(
        checks: list[dict[str, Any]],
        name: str,
        status: str,
        message: str,
        *,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        item = {
            "name": name,
            "status": status,
            "message": message,
        }
        if details:
            item["details"] = details
        checks.append(item)
