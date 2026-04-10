#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import platform
import shutil
import subprocess
import sys
from urllib.parse import urlparse
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

Status = Literal["READY", "PARTIAL", "BLOCKED"]

PLACEHOLDER_VALUES = {
    "",
    "CHANGE_ME",
    "your-token-here",
    "your-secret-here",
    "your-api-key-here",
    "your-telegram-bot-token-here",
    "PUBLIC_OR_REACHABLE_IP",
    "...",
}

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ENV_FILE = REPO_ROOT / ".env"
BACKEND_ENV_TEMPLATE = REPO_ROOT / ".env.local.example"
FRONTEND_ENV_FILE = REPO_ROOT / "admin-panel" / ".env.local"
FRONTEND_ENV_TEMPLATE = REPO_ROOT / "admin-panel" / ".env.example"
VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
LOCAL_DB_HOST = "127.0.0.1"
LOCAL_DB_PORT = 5433


@dataclass
class DoctorCheck:
    name: str
    status: Status
    message: str
    details: dict[str, Any] | None = None
    affects_overall: bool = True


@dataclass
class RuntimeContext:
    settings: Any
    voice_strategy_module: Any
    sqlalchemy_text: Any
    create_async_engine: Any
    redis_module: Any
    alembic_config_cls: Any
    alembic_script_directory_cls: Any
    create_app: Any


@dataclass
class DoctorReport:
    status: Status
    checks: list[DoctorCheck]
    manual_secrets: list[dict[str, str]]


def is_missing_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return False
    cleaned = str(value).strip()
    if not cleaned:
        return True
    if cleaned in PLACEHOLDER_VALUES:
        return True
    return cleaned.startswith("CHANGE_ME")


def classify_overall_status(checks: list[DoctorCheck]) -> Status:
    relevant = [check for check in checks if check.affects_overall]
    if any(check.status == "BLOCKED" for check in relevant):
        return "BLOCKED"
    if any(check.status == "PARTIAL" for check in relevant):
        return "PARTIAL"
    return "READY"


def parse_env_assignments(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def parse_database_endpoint(database_url: str) -> tuple[str | None, int | None]:
    try:
        normalized = database_url
        if normalized.startswith("postgresql+asyncpg://"):
            normalized = normalized.replace("postgresql+asyncpg://", "postgresql://", 1)
        parsed = urlparse(normalized)
        return parsed.hostname, parsed.port
    except Exception:
        return None, None


def required_browser_voice_secrets(settings: Any) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    if is_missing_value(getattr(settings, "gemini_api_key", "")):
        items.append(
            {
                "variable": "GEMINI_API_KEY",
                "file": ".env",
                "required_for": "Browser voice input + Gemini responses",
            }
        )

    strategy = getattr(settings, "direct_voice_strategy", "disabled")
    if strategy == "tts_primary" or (
        strategy == "gemini_primary"
        and getattr(settings, "direct_voice_allow_tts_fallback", False)
    ):
        if is_missing_value(getattr(settings, "elevenlabs_api_key", "")):
            items.append(
                {
                    "variable": "ELEVENLABS_API_KEY",
                    "file": ".env",
                    "required_for": "Browser voice playback via TTS",
                }
            )
        if is_missing_value(getattr(settings, "elevenlabs_voice_id", "")):
            items.append(
                {
                    "variable": "ELEVENLABS_VOICE_ID",
                    "file": ".env",
                    "required_for": "Browser voice playback via TTS",
                }
            )
    return items


def optional_pstn_secrets(settings: Any) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    mapping = [
        ("mango_api_key", "MANGO_API_KEY", "Real Mango telephony"),
        ("mango_api_salt", "MANGO_API_SALT", "Real Mango telephony"),
        ("mango_from_ext", "MANGO_FROM_EXT", "Real Mango originate/callback source"),
        (
            "mango_webhook_shared_secret",
            "MANGO_WEBHOOK_SHARED_SECRET",
            "Mango webhook verification",
        ),
        (
            "freeswitch_esl_password",
            "FREESWITCH_ESL_PASSWORD",
            "Real FreeSWITCH media gateway",
        ),
    ]
    for attr, variable, purpose in mapping:
        if is_missing_value(getattr(settings, attr, "")):
            items.append({"variable": variable, "file": ".env", "required_for": purpose})
    return items


def discover_compose_command() -> list[str] | None:
    docker = shutil.which("docker")
    if docker:
        try:
            result = subprocess.run(
                [docker, "compose", "version"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if result.returncode == 0:
                return [docker, "compose"]
        except Exception:
            pass
    docker_compose = shutil.which("docker-compose")
    if docker_compose:
        return [docker_compose]
    return None


def docker_daemon_reachable() -> bool:
    docker = shutil.which("docker")
    if not docker:
        return False
    try:
        result = subprocess.run(
            [docker, "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return result.returncode == 0
    except Exception:
        return False


def load_runtime_context() -> RuntimeContext | None:
    try:
        config_module = importlib.import_module("app.core.config")
        voice_strategy_module = importlib.import_module("app.integrations.direct.voice_strategy")
        sqlalchemy = importlib.import_module("sqlalchemy")
        sqlalchemy_async = importlib.import_module("sqlalchemy.ext.asyncio")
        redis_module = importlib.import_module("redis.asyncio")
        alembic_config = importlib.import_module("alembic.config")
        alembic_script = importlib.import_module("alembic.script")
        main_module = importlib.import_module("app.main")
        return RuntimeContext(
            settings=config_module.settings,
            voice_strategy_module=voice_strategy_module,
            sqlalchemy_text=sqlalchemy.text,
            create_async_engine=sqlalchemy_async.create_async_engine,
            redis_module=redis_module,
            alembic_config_cls=alembic_config.Config,
            alembic_script_directory_cls=alembic_script.ScriptDirectory,
            create_app=main_module.create_app,
        )
    except Exception:
        return None


def add_check(
    checks: list[DoctorCheck],
    name: str,
    status: Status,
    message: str,
    *,
    details: dict[str, Any] | None = None,
    affects_overall: bool = True,
) -> None:
    checks.append(
        DoctorCheck(
            name=name,
            status=status,
            message=message,
            details=details,
            affects_overall=affects_overall,
        )
    )


async def check_database(ctx: RuntimeContext, checks: list[DoctorCheck]) -> None:
    host, port = parse_database_endpoint(ctx.settings.database_url)
    if (
        ctx.settings.environment == "development"
        and host in {"localhost", "127.0.0.1"}
        and port == 5432
    ):
        add_check(
            checks,
            "database_contract",
            "BLOCKED",
            "Local development DATABASE_URL still points to 5432. Use 127.0.0.1:5433 to avoid conflicts with a system Postgres on macOS.",
            details={
                "database_url": ctx.settings.database_url,
                "expected_host": LOCAL_DB_HOST,
                "expected_port": LOCAL_DB_PORT,
            },
        )
        return

    engine = None
    try:
        engine = ctx.create_async_engine(ctx.settings.database_url)
        async with engine.connect() as connection:
            await connection.execute(ctx.sqlalchemy_text("SELECT 1"))
        add_check(
            checks,
            "database",
            "READY",
            "Database is reachable.",
            details={
                "database_url": ctx.settings.database_url,
                "host": host,
                "port": port,
            },
        )
    except Exception as exc:
        add_check(
            checks,
            "database",
            "BLOCKED",
            "Database is not reachable.",
            details={
                "error": str(exc),
                "database_url": ctx.settings.database_url,
                "host": host,
                "port": port,
            },
        )
    finally:
        if engine is not None:
            await engine.dispose()


async def check_redis(ctx: RuntimeContext, checks: list[DoctorCheck]) -> None:
    client = None
    try:
        client = ctx.redis_module.from_url(ctx.settings.redis_url, socket_connect_timeout=2)
        await client.ping()
        add_check(
            checks,
            "redis",
            "READY",
            "Redis is reachable.",
            details={"redis_url": ctx.settings.redis_url},
        )
    except Exception as exc:
        add_check(
            checks,
            "redis",
            "BLOCKED",
            "Redis is not reachable.",
            details={"error": str(exc), "redis_url": ctx.settings.redis_url},
        )
    finally:
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                pass


async def check_migrations(ctx: RuntimeContext, checks: list[DoctorCheck]) -> None:
    if not ALEMBIC_INI.exists():
        add_check(checks, "migrations", "BLOCKED", "alembic.ini is missing.")
        return

    config = ctx.alembic_config_cls(str(ALEMBIC_INI))
    script_dir = ctx.alembic_script_directory_cls.from_config(config)
    expected_head = script_dir.get_current_head()

    engine = None
    try:
        engine = ctx.create_async_engine(ctx.settings.database_url)
        async with engine.connect() as connection:
            result = await connection.execute(
                ctx.sqlalchemy_text("SELECT version_num FROM alembic_version")
            )
            row = result.first()
        applied_head = row[0] if row else None
        if applied_head == expected_head:
            add_check(
                checks,
                "migrations",
                "READY",
                "Database migrations are at head.",
                details={"expected_head": expected_head, "applied_head": applied_head},
            )
        else:
            add_check(
                checks,
                "migrations",
                "BLOCKED",
                "Database migrations are not at head.",
                details={"expected_head": expected_head, "applied_head": applied_head},
            )
    except Exception as exc:
        add_check(
            checks,
            "migrations",
            "BLOCKED",
            "Database migrations could not be verified.",
            details={"error": str(exc), "expected_head": expected_head},
        )
    finally:
        if engine is not None:
            await engine.dispose()


def check_backend_files(checks: list[DoctorCheck]) -> None:
    if BACKEND_ENV_FILE.exists():
        add_check(
            checks,
            "backend_env_file",
            "READY",
            ".env exists.",
            details={"path": str(BACKEND_ENV_FILE)},
        )
    else:
        add_check(
            checks,
            "backend_env_file",
            "BLOCKED",
            ".env is missing. Copy .env.local.example to .env first.",
            details={"template": str(BACKEND_ENV_TEMPLATE)},
        )

    if FRONTEND_ENV_FILE.exists():
        add_check(
            checks,
            "frontend_env_file",
            "READY",
            "admin-panel/.env.local exists.",
            details={"path": str(FRONTEND_ENV_FILE)},
        )
    else:
        add_check(
            checks,
            "frontend_env_file",
            "PARTIAL",
            "admin-panel/.env.local is missing. Local Vite dev can still proxy to 127.0.0.1:8000, but creating the file keeps local setup consistent.",
            details={"template": str(FRONTEND_ENV_TEMPLATE)},
            affects_overall=False,
        )


def check_tooling(checks: list[DoctorCheck]) -> None:
    python_ok = sys.version_info >= (3, 9)
    add_check(
        checks,
        "python",
        "READY" if python_ok else "BLOCKED",
        f"Python {platform.python_version()} detected.",
        details={"required": ">=3.9", "executable": sys.executable},
    )

    for command in ("node", "npm"):
        path = shutil.which(command)
        add_check(
            checks,
            command,
            "READY" if path else "BLOCKED",
            f"{command} is {'available' if path else 'missing' }.",
            details={"path": path},
        )

    compose_command = discover_compose_command()
    if compose_command and docker_daemon_reachable():
        add_check(
            checks,
            "docker_compose",
            "READY",
            "Docker Compose and Docker daemon are available for local Postgres/Redis.",
            details={"command": " ".join(compose_command)},
            affects_overall=False,
        )
    elif compose_command:
        add_check(
            checks,
            "docker_compose",
            "PARTIAL",
            "Docker Compose is installed, but Docker daemon is not reachable. Bootstrap cannot auto-start Postgres/Redis until Docker is running.",
            details={"command": " ".join(compose_command)},
            affects_overall=False,
        )
    else:
        add_check(
            checks,
            "docker_compose",
            "PARTIAL",
            "Docker Compose is not available. Local DB/Redis must already be running outside bootstrap.",
            details={"command": None},
            affects_overall=False,
        )

    venv_python_exists = VENV_PYTHON.exists()
    add_check(
        checks,
        "backend_venv",
        "READY" if venv_python_exists else "PARTIAL",
        ".venv exists." if venv_python_exists else ".venv is missing. Run bootstrap first.",
        details={"path": str(VENV_PYTHON)},
    )

    node_modules = REPO_ROOT / "admin-panel" / "node_modules"
    add_check(
        checks,
        "frontend_dependencies",
        "READY" if node_modules.exists() else "PARTIAL",
        "admin-panel dependencies are installed."
        if node_modules.exists()
        else "admin-panel/node_modules is missing. Run bootstrap first.",
        details={"path": str(node_modules)},
    )


def check_backend_runtime(ctx: RuntimeContext | None, checks: list[DoctorCheck]) -> None:
    if ctx is None:
        add_check(
            checks,
            "backend_runtime",
            "BLOCKED",
            "Backend runtime dependencies are not importable. Run the local bootstrap first.",
        )
        return

    try:
        ctx.create_app()
        add_check(checks, "backend_runtime", "READY", "Backend app imports successfully.")
    except Exception as exc:
        add_check(
            checks,
            "backend_runtime",
            "BLOCKED",
            "Backend app failed to import.",
            details={"error": str(exc)},
        )


def check_admin_auth(ctx: RuntimeContext | None, checks: list[DoctorCheck]) -> None:
    if ctx is None:
        return

    settings = ctx.settings
    if settings.admin_auth_configured:
        add_check(
            checks,
            "admin_auth",
            "READY",
            "Admin auth is configured for local login.",
            details={"admin_email": settings.admin_email},
        )
    else:
        add_check(
            checks,
            "admin_auth",
            "BLOCKED",
            "Admin auth is not configured. You will not be able to log into the admin panel.",
        )

    if settings.provider_settings_secret or settings.admin_auth_secret:
        add_check(
            checks,
            "provider_settings_encryption",
            "READY",
            "Provider settings encryption secret is configured.",
        )
    else:
        add_check(
            checks,
            "provider_settings_encryption",
            "BLOCKED",
            "Provider settings encryption secret is missing. Providers UI cannot safely store secrets.",
        )


def check_frontend_env(checks: list[DoctorCheck]) -> None:
    source = FRONTEND_ENV_FILE if FRONTEND_ENV_FILE.exists() else FRONTEND_ENV_TEMPLATE
    env_values = parse_env_assignments(source)
    api_base = env_values.get("VITE_API_BASE_URL", "")
    if not api_base:
        add_check(
            checks,
            "frontend_api_base",
            "READY",
            "VITE_API_BASE_URL is empty, so local Vite dev proxy will be used.",
            details={"mode": "vite_proxy", "source": str(source)},
        )
    elif api_base.startswith("http://") or api_base.startswith("https://"):
        add_check(
            checks,
            "frontend_api_base",
            "READY",
            "VITE_API_BASE_URL is configured.",
            details={"value": api_base, "source": str(source)},
        )
    else:
        add_check(
            checks,
            "frontend_api_base",
            "PARTIAL",
            "VITE_API_BASE_URL is set but does not look like an HTTP URL.",
            details={"value": api_base, "source": str(source)},
        )


def check_browser_voice(ctx: RuntimeContext | None, checks: list[DoctorCheck]) -> list[dict[str, str]]:
    if ctx is None:
        return []

    settings = ctx.settings
    strategy_checks = ctx.voice_strategy_module.inspect_voice_strategy(settings)
    failures = [check for check in strategy_checks if check.status == "fail"]
    missing_secrets = required_browser_voice_secrets(settings)

    if settings.direct_voice_strategy == "disabled":
        add_check(
            checks,
            "browser_voice",
            "PARTIAL",
            "Browser sandbox UI can run, but voice runtime is disabled by DIRECT_VOICE_STRATEGY=disabled.",
            details={"voice_strategy": settings.direct_voice_strategy},
        )
        return missing_secrets

    if failures or missing_secrets:
        add_check(
            checks,
            "browser_voice",
            "PARTIAL",
            "Browser sandbox UI is ready, but browser voice is blocked by missing secrets or invalid voice strategy settings.",
            details={
                "voice_strategy": settings.direct_voice_strategy,
                "issues": [check.message for check in failures],
                "missing_secrets": [item["variable"] for item in missing_secrets],
            },
        )
    else:
        add_check(
            checks,
            "browser_voice",
            "READY",
            "Browser voice prerequisites are configured. Manual browser audio validation is still required.",
            details={"voice_strategy": settings.direct_voice_strategy},
        )
    return missing_secrets


def check_optional_provider_routes(ctx: RuntimeContext | None, checks: list[DoctorCheck]) -> list[dict[str, str]]:
    if ctx is None:
        return []

    missing = optional_pstn_secrets(ctx.settings)
    if missing:
        add_check(
            checks,
            "pstn_optional",
            "PARTIAL",
            "Real Mango/FreeSWITCH telephony is not fully configured. This is expected for browser-only local testing.",
            details={"missing": [item["variable"] for item in missing]},
            affects_overall=False,
        )
    else:
        add_check(
            checks,
            "pstn_optional",
            "READY",
            "Optional PSTN provider secrets are configured.",
            affects_overall=False,
        )
    return missing


async def build_report() -> DoctorReport:
    checks: list[DoctorCheck] = []
    manual_secrets: list[dict[str, str]] = []

    check_tooling(checks)
    check_backend_files(checks)
    ctx = load_runtime_context()
    check_backend_runtime(ctx, checks)
    check_frontend_env(checks)
    check_admin_auth(ctx, checks)

    if ctx is not None:
        await check_database(ctx, checks)
        await check_redis(ctx, checks)
        await check_migrations(ctx, checks)
        manual_secrets.extend(check_browser_voice(ctx, checks))
        manual_secrets.extend(check_optional_provider_routes(ctx, checks))

    deduped_manual_secrets: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in manual_secrets:
        key = (item["variable"], item["file"])
        if key in seen:
            continue
        seen.add(key)
        deduped_manual_secrets.append(item)

    return DoctorReport(
        status=classify_overall_status(checks),
        checks=checks,
        manual_secrets=deduped_manual_secrets,
    )


def render_human_report(report: DoctorReport) -> str:
    lines = [f"LOCAL DOCTOR STATUS: {report.status}", ""]
    for check in report.checks:
        lines.append(f"[{check.status}] {check.name}: {check.message}")
        if check.details:
            for key, value in check.details.items():
                lines.append(f"    - {key}: {value}")
    lines.append("")
    if report.manual_secrets:
        lines.append("Manual secrets still needed:")
        for item in report.manual_secrets:
            lines.append(f"- {item['variable']} -> {item['file']} ({item['required_for']})")
    else:
        lines.append("Manual secrets still needed: none")
    return "\n".join(lines)


async def async_main(as_json: bool) -> int:
    report = await build_report()
    if as_json:
        print(
            json.dumps(
                {
                    "status": report.status,
                    "checks": [asdict(check) for check in report.checks],
                    "manual_secrets": report.manual_secrets,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(render_human_report(report))

    if report.status == "READY":
        return 0
    if report.status == "PARTIAL":
        return 1
    return 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Local environment doctor for localhost setup")
    parser.add_argument("--json", action="store_true", help="Print JSON report")
    args = parser.parse_args()
    return asyncio.run(async_main(args.json))


if __name__ == "__main__":
    raise SystemExit(main())
