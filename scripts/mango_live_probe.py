#!/usr/bin/env python3
"""
Live Mango integration probe.

Purpose:
- Read current env/.env configuration with masked diagnostics.
- Perform real HTTP calls to Mango using the configured tenant credentials.
- Verify backend sync-lines path against the real Mango tenant.
- Verify a temporary agent binding roundtrip using the real synced line data.

Safety:
- No raw secrets are printed.
- No outbound Mango originate/call actions are performed.
- The temporary agent created for the binding roundtrip is deleted at the end.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import socket
import ssl
import sys
import time
from pathlib import Path
from typing import Any, Optional

import httpx
from sqlalchemy import delete, select

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO_ROOT / ".env")

from app.core.config import settings  # noqa: E402
from app.db.session import AsyncSessionLocal  # noqa: E402
from app.integrations.telephony.mango_client import (  # noqa: E402
    _build_signed_payload,
    _coerce_bool,
    _extract_records,
    _first_non_empty,
    _first_value,
    normalize_mango_phone,
)
from app.main import create_app  # noqa: E402
from app.models.agent_profile import AgentProfile  # noqa: E402
from app.models.telephony_line import TelephonyLine  # noqa: E402


def _mask_secret(value: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        return ""
    if len(cleaned) <= 4:
        return "*" * len(cleaned)
    if len(cleaned) <= 8:
        return f"{cleaned[:1]}***{cleaned[-1:]}"
    return f"{cleaned[:2]}***{cleaned[-2:]}"


def _mask_phone(value: Optional[str]) -> Optional[str]:
    cleaned = (value or "").strip()
    if not cleaned:
        return None
    normalized = normalize_mango_phone(cleaned)
    if len(normalized) < 7:
        return normalized
    if normalized.startswith("+") and len(normalized) >= 8:
        return f"{normalized[:4]}***{normalized[-4:]}"
    return f"{normalized[:3]}***{normalized[-4:]}"


def _section(title: str) -> None:
    print(f"\n{'=' * 72}")
    print(title)
    print(f"{'=' * 72}")


def _json_preview(payload: Any) -> str:
    try:
        rendered = json.dumps(payload, ensure_ascii=False)
    except Exception:
        rendered = str(payload)
    return rendered[:800]


def _redact_personal_name(value: Optional[str]) -> Optional[str]:
    cleaned = (value or "").strip()
    if not cleaned:
        return None
    return "<redacted>"


def _sanitize_line_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "remote_line_id": str(
            _first_non_empty(
                record,
                "line_id",
                "id",
                "incomingline_id",
                "number_id",
            )
            or ""
        ),
        "phone_number": _mask_phone(_first_non_empty(record, "number", "phone_number", "line_number")),
        "schema_name": _first_non_empty(record, "schema_name", "schema", "routing_schema_name"),
        "name": _first_non_empty(record, "name", "display_name", "title", "label"),
        "comment": _first_non_empty(record, "comment"),
        "region": _first_non_empty(record, "region"),
        "schema_id": _first_non_empty(record, "schema_id"),
    }


def _sanitize_extension_record(record: dict[str, Any]) -> dict[str, Any]:
    general = record.get("general") if isinstance(record.get("general"), dict) else {}
    telephony = record.get("telephony") if isinstance(record.get("telephony"), dict) else {}
    return {
        "provider_resource_id": (
            _first_non_empty(record, "id", "user_id", "employee_id")
            or _first_non_empty(general, "id", "user_id", "employee_id")
        ),
        "extension": _first_non_empty(
            telephony,
            "extension",
            "internal_number",
            "sip_number",
            "short_number",
            "number",
        ) or _first_non_empty(record, "extension", "internal_number", "sip_number", "short_number"),
        "display_name": (
            _redact_personal_name(
                _first_non_empty(general, "name", "full_name", "fio", "title")
                or _first_non_empty(record, "name", "full_name", "fio", "title")
            )
        ),
        "line_provider_resource_id": _first_non_empty(
            telephony,
            "line_id",
            "outgoing_line_id",
            "line_number_id",
        ) or _first_non_empty(record, "line_id", "outgoing_line_id", "line_number_id"),
        "line_phone_number": _mask_phone(
            _first_non_empty(
                telephony,
                "outgoingline",
                "outgoing_line",
                "line_number",
                "phone_number",
            ) or _first_non_empty(record, "line_number", "outgoing_line", "phone_number")
        ),
    }


def _parse_line_records(payload: Any) -> list[dict[str, Any]]:
    records = _extract_records(payload, preferred_keys=("incominglines", "lines", "numbers"))
    parsed: list[dict[str, Any]] = []
    for record in records:
        phone_number = _first_non_empty(
            record,
            "number",
            "phone_number",
            "line_number",
            "incomingline",
            "line",
            "callerid",
        )
        if not phone_number:
            continue
        schema_name = _first_non_empty(record, "schema_name", "schema", "routing_schema_name")
        display_name = _first_non_empty(
            record,
            "display_name",
            "name",
            "title",
            "label",
            "line_name",
        )
        parsed.append(
            {
                "remote_line_id": str(
                    _first_non_empty(
                        record,
                        "line_id",
                        "id",
                        "incomingline_id",
                        "number_id",
                        "line_number_id",
                        "lineNumberId",
                        "numberId",
                    )
                    or normalize_mango_phone(phone_number)
                ),
                "phone_number": normalize_mango_phone(phone_number),
                "schema_name": schema_name,
                "display_name": display_name or schema_name or normalize_mango_phone(phone_number),
                "extension": _first_non_empty(record, "extension", "internal_number", "sip_number"),
                "is_active": _coerce_bool(record.get("is_active"), default=True),
                "is_inbound_enabled": _coerce_bool(
                    _first_value(record, "is_inbound_enabled", "inbound_enabled", "can_receive"),
                    default=True,
                ),
                "is_outbound_enabled": _coerce_bool(
                    _first_value(record, "is_outbound_enabled", "outbound_enabled", "can_call_out"),
                    default=False,
                ),
                "raw_payload": record,
            }
        )
    return parsed


def _parse_extension_records(payload: Any) -> list[dict[str, Any]]:
    records = _extract_records(payload, preferred_keys=("users", "employees", "extensions"))
    parsed: list[dict[str, Any]] = []
    for record in records:
        general = record.get("general") if isinstance(record.get("general"), dict) else {}
        telephony = record.get("telephony") if isinstance(record.get("telephony"), dict) else {}
        extension = _first_non_empty(
            telephony,
            "extension",
            "internal_number",
            "sip_number",
            "short_number",
            "number",
        ) or _first_non_empty(
            record,
            "extension",
            "internal_number",
            "sip_number",
            "short_number",
            "number",
        )
        if not extension:
            continue
        line_phone_number = _first_non_empty(
            telephony,
            "outgoingline",
            "outgoing_line",
            "line_number",
            "phone_number",
        ) or _first_non_empty(record, "line_number", "outgoing_line", "phone_number")
        parsed.append(
            {
                "provider_resource_id": (
                    _first_non_empty(record, "id", "user_id", "employee_id")
                    or _first_non_empty(general, "id", "user_id", "employee_id")
                    or extension
                ),
                "extension": extension,
                "display_name": (
                    _first_non_empty(general, "name", "full_name", "fio", "title")
                    or _first_non_empty(record, "name", "full_name", "fio", "title")
                ),
                "line_provider_resource_id": _first_non_empty(
                    telephony,
                    "line_id",
                    "outgoing_line_id",
                    "line_number_id",
                ) or _first_non_empty(
                    record,
                    "line_id",
                    "outgoing_line_id",
                    "line_number_id",
                ),
                "line_phone_number": normalize_mango_phone(line_phone_number) if line_phone_number else None,
                "raw_payload": record,
            }
        )
    return parsed


def _effective_base_url() -> str:
    return (settings.mango_api_base_url or "https://app.mango-office.ru/vpbx").strip()


def _env_diagnostics() -> dict[str, Any]:
    return {
        "mango_api_key_present": bool(settings.mango_api_key),
        "mango_api_salt_present": bool(settings.mango_api_salt),
        "mango_api_base_url": _effective_base_url(),
        "mango_from_ext_present": bool(settings.mango_from_ext),
        "mango_webhook_secret_present": bool(settings.mango_webhook_secret),
        "mango_webhook_shared_secret_present": bool(settings.mango_webhook_shared_secret),
        "admin_auth_configured": bool(settings.admin_auth_configured),
        "mango_client_env_used": [
            "MANGO_API_KEY",
            "MANGO_API_SALT",
            "MANGO_API_BASE_URL",
        ],
        "mango_runtime_env_used": [
            "MANGO_FROM_EXT",
            "MANGO_WEBHOOK_SECRET",
            "MANGO_WEBHOOK_SHARED_SECRET",
        ],
        "mango_api_key_masked": _mask_secret(settings.mango_api_key),
        "mango_api_salt_masked": _mask_secret(settings.mango_api_salt),
    }


async def _dns_tls_probe(base_url: str) -> dict[str, Any]:
    host = httpx.URL(base_url).host
    result: dict[str, Any] = {"host": host}
    try:
        addr_info = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        result["dns_ok"] = True
        result["addresses"] = sorted({entry[4][0] for entry in addr_info})
    except Exception as exc:  # pragma: no cover - environment/network dependent
        return {"host": host, "dns_ok": False, "error": str(exc)}

    try:
        context = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=10) as sock:
            with context.wrap_socket(sock, server_hostname=host) as tls_sock:
                result["tls_ok"] = True
                result["tls_version"] = tls_sock.version()
                result["cipher"] = tls_sock.cipher()[0] if tls_sock.cipher() else None
                cert = tls_sock.getpeercert()
                result["cert_subject"] = cert.get("subject")
                result["cert_issuer"] = cert.get("issuer")
    except Exception as exc:  # pragma: no cover - environment/network dependent
        result["tls_ok"] = False
        result["tls_error"] = str(exc)
    return result


async def _call_mango_endpoint(
    client: httpx.AsyncClient,
    *,
    path: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    signed = _build_signed_payload(settings.mango_api_key, settings.mango_api_salt, payload)
    started = time.perf_counter()
    response = await client.post(path, data=signed)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    body_preview: Any
    parsed_json: Any = None
    try:
        parsed_json = response.json()
        body_preview = parsed_json
    except Exception:
        body_preview = response.text[:800]
    return {
        "endpoint": path,
        "status_code": response.status_code,
        "success": response.status_code < 400,
        "elapsed_ms": elapsed_ms,
        "body_preview": body_preview,
        "json": parsed_json,
    }


async def _login_admin(ac: httpx.AsyncClient) -> dict[str, Any]:
    if not settings.admin_auth_configured:
        return {"success": False, "detail": "admin_auth_not_configured"}
    response = await ac.post(
        "/v1/admin/auth/login",
        json={"email": settings.admin_email, "password": settings.admin_password},
    )
    detail: Any
    try:
        detail = response.json()
    except Exception:
        detail = response.text[:400]
    if response.status_code != 200:
        return {
            "success": False,
            "status_code": response.status_code,
            "detail": detail,
        }
    payload = response.json()
    return {
        "success": True,
        "status_code": response.status_code,
        "access_token": payload["access_token"],
        "token_type": payload.get("token_type"),
    }


async def _query_synced_lines() -> list[TelephonyLine]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(TelephonyLine)
            .where(TelephonyLine.provider == "mango")
            .order_by(TelephonyLine.phone_number.asc())
        )
        return list(result.scalars().all())


async def _delete_agent(agent_id: str) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(delete(AgentProfile).where(AgentProfile.id == agent_id))
        await session.commit()


async def _backend_sync_and_binding() -> dict[str, Any]:
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    result: dict[str, Any] = {
        "login": None,
        "sync_lines": None,
        "lines_get": None,
        "extensions_get": None,
        "db_lines": [],
        "binding": None,
    }
    temp_agent_id: Optional[str] = None
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://probe.local") as ac:
            login = await _login_admin(ac)
            result["login"] = {
                "success": login.get("success", False),
                "status_code": login.get("status_code"),
                "detail": login.get("detail"),
            }
            if not login.get("success"):
                return result

            token = login["access_token"]
            headers = {"Authorization": f"Bearer {token}"}

            sync_response = await ac.post("/v1/telephony/mango/sync-lines", headers=headers)
            try:
                sync_body = sync_response.json()
            except Exception:
                sync_body = {"text": sync_response.text[:800]}
            result["sync_lines"] = {
                "status_code": sync_response.status_code,
                "success": sync_response.status_code < 400,
                "body_preview": _json_preview(sync_body),
            }

            lines_response = await ac.get("/v1/telephony/mango/lines", headers=headers)
            lines_body = lines_response.json()
            result["lines_get"] = {
                "status_code": lines_response.status_code,
                "success": lines_response.status_code < 400,
                "total": lines_body.get("total"),
                "items_preview": [
                    {
                        "id": item["id"],
                        "remote_line_id": item.get("remote_line_id") or item.get("provider_resource_id"),
                        "phone_number": _mask_phone(item.get("phone_number")),
                        "display_name": item.get("display_name"),
                        "schema_name": item.get("schema_name"),
                        "label": item.get("label"),
                        "is_active": item.get("is_active"),
                    }
                    for item in lines_body.get("items", [])[:10]
                ],
            }

            extensions_response = await ac.get("/v1/telephony/mango/extensions", headers=headers)
            try:
                extensions_body = extensions_response.json()
            except Exception:
                extensions_body = {"text": extensions_response.text[:800]}
            result["extensions_get"] = {
                "status_code": extensions_response.status_code,
                "success": extensions_response.status_code < 400,
                "body_preview": _json_preview(extensions_body),
            }

            db_lines = await _query_synced_lines()
            result["db_lines"] = [
                {
                    "id": str(line.id),
                    "provider": line.provider,
                    "remote_line_id": line.provider_resource_id,
                    "phone_number": _mask_phone(line.phone_number),
                    "display_name": line.display_name,
                    "schema_name": getattr(line, "schema_name", None),
                    "is_active": line.is_active,
                    "is_inbound_enabled": line.is_inbound_enabled,
                    "is_outbound_enabled": line.is_outbound_enabled,
                }
                for line in db_lines[:10]
            ]

            if not lines_body.get("items"):
                return result

            target_line = lines_body["items"][0]
            create_agent_response = await ac.post(
                "/v1/agents",
                headers=headers,
                json={
                    "name": f"Mango Live Probe {int(time.time())}",
                    "is_active": True,
                    "system_prompt": "Temporary probe agent",
                    "tone_rules": "",
                    "business_rules": "",
                    "sales_objectives": "",
                    "greeting_text": "",
                    "transfer_rules": "",
                    "prohibited_promises": "",
                    "voice_strategy": "tts_primary",
                    "config": {"probe": "mango_live"},
                },
            )
            create_agent_body = create_agent_response.json()
            temp_agent_id = create_agent_body.get("id")

            patch_response = await ac.patch(
                f"/v1/agent-profiles/{temp_agent_id}/settings",
                headers=headers,
                json={
                    "telephony_provider": "mango",
                    "telephony_line_id": target_line["id"],
                    "telephony_extension": None,
                    "voice_provider": "elevenlabs",
                    "system_prompt": "Temporary probe agent",
                    "user_settings": {"probe": "mango_live"},
                    "knowledge_document_ids": [],
                },
            )
            patch_body = patch_response.json()

            get_response = await ac.get(
                f"/v1/agent-profiles/{temp_agent_id}/settings",
                headers=headers,
            )
            get_body = get_response.json()

            result["binding"] = {
                "agent_id": temp_agent_id,
                "create_status_code": create_agent_response.status_code,
                "patch_status_code": patch_response.status_code,
                "get_status_code": get_response.status_code,
                "patch_success": patch_response.status_code < 400,
                "get_success": get_response.status_code < 400,
                "target_remote_line_id": target_line.get("remote_line_id") or target_line.get("provider_resource_id"),
                "patch_body_preview": _json_preview(
                    {
                        "telephony_provider": patch_body.get("telephony_provider"),
                        "telephony_line_id": patch_body.get("telephony_line_id"),
                        "telephony_line": {
                            "remote_line_id": patch_body.get("telephony_line", {}).get("remote_line_id")
                            or patch_body.get("telephony_line", {}).get("provider_resource_id"),
                            "phone_number": _mask_phone(patch_body.get("telephony_line", {}).get("phone_number")),
                            "display_name": patch_body.get("telephony_line", {}).get("display_name"),
                            "schema_name": patch_body.get("telephony_line", {}).get("schema_name"),
                            "label": patch_body.get("telephony_line", {}).get("label"),
                        },
                    }
                ),
                "get_body_preview": _json_preview(
                    {
                        "telephony_provider": get_body.get("telephony_provider"),
                        "telephony_line_id": get_body.get("telephony_line_id"),
                        "telephony_line": {
                            "remote_line_id": get_body.get("telephony_line", {}).get("remote_line_id")
                            or get_body.get("telephony_line", {}).get("provider_resource_id"),
                            "phone_number": _mask_phone(get_body.get("telephony_line", {}).get("phone_number")),
                            "display_name": get_body.get("telephony_line", {}).get("display_name"),
                            "schema_name": get_body.get("telephony_line", {}).get("schema_name"),
                            "label": get_body.get("telephony_line", {}).get("label"),
                        },
                    }
                ),
            }
    finally:
        if temp_agent_id:
            await _delete_agent(temp_agent_id)
    return result


async def main(json_out: Optional[Path]) -> dict[str, Any]:
    report: dict[str, Any] = {
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "env": _env_diagnostics(),
        "dns_tls": None,
        "direct_mango_calls": [],
        "backend_path": None,
        "verdict": {},
    }

    _section("1. Live env diagnostics")
    print(json.dumps(report["env"], ensure_ascii=False, indent=2))

    base_url = _effective_base_url()
    dns_tls = await _dns_tls_probe(base_url)
    report["dns_tls"] = dns_tls
    _section("2. DNS / TLS")
    print(json.dumps(dns_tls, ensure_ascii=False, indent=2))

    _section("3. Direct Mango API calls")
    async with httpx.AsyncClient(
        base_url=base_url.rstrip("/"),
        timeout=15.0,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    ) as client:
        for path, parser in (
            ("/incominglines", _parse_line_records),
            ("/config/users/request", _parse_extension_records),
        ):
            try:
                raw_call = await _call_mango_endpoint(client, path=path, payload={})
                parsed = parser(raw_call["json"]) if raw_call["json"] is not None else []
                body_preview = raw_call["body_preview"]
                if path == "/incominglines" and isinstance(raw_call["json"], dict):
                    body_preview = {
                        "top_level_keys": sorted(raw_call["json"].keys()),
                        "line_count": len(_extract_records(raw_call["json"], preferred_keys=("incominglines", "lines", "numbers"))),
                    }
                if path == "/config/users/request" and isinstance(raw_call["json"], dict):
                    body_preview = {
                        "top_level_keys": sorted(raw_call["json"].keys()),
                        "user_count": len(_extract_records(raw_call["json"], preferred_keys=("users", "employees", "extensions"))),
                    }
                record = {
                    "endpoint": path,
                    "status_code": raw_call["status_code"],
                    "success": raw_call["success"],
                    "body_preview": _json_preview(body_preview),
                    "parsed_count": len(parsed),
                    "parsed_preview": [
                        {
                            **item,
                            "display_name": (
                                _redact_personal_name(item.get("display_name"))
                                if path == "/config/users/request"
                                else item.get("display_name")
                            ),
                            "phone_number": _mask_phone(item.get("phone_number")),
                            "line_phone_number": _mask_phone(item.get("line_phone_number")),
                            "raw_payload": (
                                _sanitize_line_record(item["raw_payload"])
                                if path == "/incominglines"
                                else _sanitize_extension_record(item["raw_payload"])
                            ),
                        }
                        for item in parsed[:10]
                    ],
                }
            except Exception as exc:  # pragma: no cover - network dependent
                record = {
                    "endpoint": path,
                    "success": False,
                    "error": str(exc),
                }
            report["direct_mango_calls"].append(record)
            print(json.dumps(record, ensure_ascii=False, indent=2))

    _section("4. Backend sync / binding path")
    backend_path = await _backend_sync_and_binding()
    report["backend_path"] = backend_path
    print(json.dumps(backend_path, ensure_ascii=False, indent=2))

    lines_call = next((item for item in report["direct_mango_calls"] if item["endpoint"] == "/incominglines"), None)
    sync_live_verified = bool(
        backend_path
        and backend_path.get("sync_lines", {}).get("success")
        and backend_path.get("lines_get", {}).get("success")
        and backend_path.get("db_lines")
    )
    binding_verified = bool(backend_path and backend_path.get("binding", {}).get("patch_success") and backend_path.get("binding", {}).get("get_success"))
    report["verdict"] = {
        "mango_connectivity": "yes" if any(item.get("success") for item in report["direct_mango_calls"]) else "no",
        "auth_signature": "yes" if lines_call and lines_call.get("success") else "no",
        "live_inventory_fetched": "yes" if lines_call and lines_call.get("parsed_count", 0) > 0 else "partial",
        "sync_lines_live_verified": "yes" if sync_live_verified else "partial" if backend_path else "no",
        "agent_binding_verified_on_live_data": "yes" if binding_verified else "partial" if backend_path else "no",
    }
    _section("5. Verdict")
    print(json.dumps(report["verdict"], ensure_ascii=False, indent=2))

    if json_out:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nSaved JSON report to {json_out}")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Live Mango integration probe")
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="Optional path to save the full sanitized JSON report",
    )
    args = parser.parse_args()
    asyncio.run(main(args.json_out))
