"""
FastAPI application entry point.
Responsibilities:
- Create the app instance with metadata
- Register middleware (rate limiting, request ID, security headers)
- Mount the API router
- Configure lifespan (startup / shutdown hooks, Redis initialization)
- Install the global exception handler for AppError
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator
import asyncio

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import structlog

from app.api.router import api_router
from app.core.config import settings
from app.core.exceptions import AppError
from app.core.logging import get_logger, setup_logging
from app.core.redis_client import close_redis, init_redis
from app.integrations.direct.voice_strategy import inspect_voice_strategy
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.request_id import RequestIdMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.services.manager_availability import reconcile_manager_availability_job
from app.db.session import AsyncSessionLocal

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Run startup tasks before yield, shutdown tasks after."""
    setup_logging()
    await init_redis()
    manager_restore_task: asyncio.Task | None = None
    log.info(
        "app_startup",
        environment=settings.environment,
        log_level=settings.log_level,
        vapi_configured=settings.vapi_configured,
        gemini_configured=settings.gemini_configured,
        auth_enabled=bool(settings.api_key),
        quiet_hours_enforced=settings.enforce_quiet_hours,
        rate_limit_enabled=settings.rate_limit_enabled,
    )
    # ── Production readiness checks ────────────────────────────────────────────
    if settings.is_production:
        if not settings.vapi_configured and not settings.gemini_configured:
            log.error(
                "production_no_real_engine",
                message="No real call engine configured — all calls use StubEngine",
            )
        strategy_checks = inspect_voice_strategy()
        strategy_failures = [check for check in strategy_checks if check.status == "fail"]
        for check in strategy_checks:
            payload = {"check": check.name, "message": check.message}
            if check.details:
                payload["details"] = check.details
            if check.status == "warn":
                log.warning("startup.voice_strategy_warning", **payload)
            elif check.status == "fail":
                log.error("startup.voice_strategy_invalid", **payload)
        if settings.direct_voice_strategy != "disabled" and strategy_failures:
            raise RuntimeError(
                "Invalid direct voice strategy configuration in production. "
                "Fix voice strategy settings before startup."
            )
        log.warning(
            "production_stub_transfer",
            message="StubTransferEngine active — warm transfers will NOT work in production",
        )
        if not settings.elevenlabs_configured and not settings.gemini_audio_output_enabled:
            log.warning(
                "production_stub_voice",
                message=(
                    "No outbound Direct voice path configured. "
                    "Enable GEMINI_AUDIO_OUTPUT_ENABLED=true or configure ElevenLabs."
                ),
            )
        if not settings.enforce_quiet_hours:
            log.warning(
                "startup.quiet_hours_disabled_in_production",
                note="Set ENFORCE_QUIET_HOURS=true in production to restrict calling window.",
            )

    # ── Direct session startup reconciliation ──────────────────────────────────
    # Must run after Redis is initialised.  Marks orphaned Direct sessions
    # (those whose owning process died without a graceful shutdown) as FAILED
    # in the database so the system state stays consistent.
    if settings.gemini_configured:
        try:
            from app.api import deps as _deps
            from app.models.call import Call
            from app.repositories.call_repo import CallRepository

            coordinator = _deps.get_session_coordinator()
            async with AsyncSessionLocal() as _session:
                repo = CallRepository(Call, _session)
                reconcile_stats = await coordinator.startup_reconcile(repo)
                await _session.commit()
            log.info("startup_reconciliation_complete", **reconcile_stats)
        except Exception as exc:
            # Reconciliation failure must not prevent startup
            log.error(
                "startup_reconciliation_failed",
                error=str(exc),
                message="Direct sessions may be in inconsistent state",
            )

    # ── Manager availability durable reconciliation ───────────────────────────
    if (
        settings.transfer_manager_restore_enabled
        and settings.environment != "testing"
    ):
        try:
            restored = await reconcile_manager_availability_job(AsyncSessionLocal)
            if restored:
                log.info("startup_manager_restore_complete", restored=restored)
        except Exception as exc:
            log.warning("startup_manager_restore_failed", error=str(exc))

        async def _manager_restore_loop() -> None:
            interval = max(5, int(settings.transfer_manager_restore_interval_seconds))
            while True:
                try:
                    await asyncio.sleep(interval)
                    await reconcile_manager_availability_job(AsyncSessionLocal)
                except asyncio.CancelledError:
                    return
                except Exception as exc:
                    log.warning("manager_restore_loop_failed", error=str(exc))

        manager_restore_task = asyncio.create_task(
            _manager_restore_loop(),
            name="manager_restore_loop",
        )

    yield
    if manager_restore_task is not None:
        manager_restore_task.cancel()
        try:
            await manager_restore_task
        except asyncio.CancelledError:
            pass
    await close_redis()
    log.info("app_shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title="AMO CRM Voice — AI Sales System",
        description="Backend API for AI-driven outbound voice calls",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # ── Middleware (applied in reverse registration order) ─────────────────────
    # RateLimit is outermost → added first (executes first, catches IP floods)
    app.add_middleware(RateLimitMiddleware)
    # SecurityHeaders is next → added second
    app.add_middleware(SecurityHeadersMiddleware)
    # RequestId is innermost → added third (executes last)
    app.add_middleware(RequestIdMiddleware)

    # ── Dev-mode CORS (non-production only) ───────────────────────────────────
    # Vite dev proxy handles CORS in the normal local workflow, but this allows
    # direct API access (e.g. Swagger UI, curl from a different origin) without
    # opaque CORS errors.  Never active in production.
    if not settings.is_production:
        from fastapi.middleware.cors import CORSMiddleware
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    elif settings.admin_cors_origins_list:
        from fastapi.middleware.cors import CORSMiddleware
        origins = settings.admin_cors_origins_list
        is_wildcard = origins == ["*"]
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=not is_wildcard,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    else:
        log.warning(
            "startup.production_cors_not_configured",
            message=(
                "Production admin/browser CORS origins are empty. "
                "Set ADMIN_CORS_ORIGINS when serving the admin panel from a separate Render domain."
            ),
        )

    # ── Global exception handler ───────────────────────────────────────────────
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        log.warning(
            "app_error",
            error_code=exc.error_code,
            message=exc.message,
            path=request.url.path,
            method=request.method,
        )
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        request_id = structlog.contextvars.get_contextvars().get("request_id")
        log.exception(
            "unhandled_exception",
            path=request.url.path,
            method=request.method,
            request_id=request_id,
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_server_error",
                "message": "Internal server error.",
                "request_id": request_id,
            },
        )

    # ── Routers ────────────────────────────────────────────────────────────────
    app.include_router(api_router)

    return app


app = create_app()
