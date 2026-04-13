#!/usr/bin/env python3
"""
Mango API connectivity and inventory diagnostic script.

Read-only: makes only GET/POST queries that do not change any account state.
No side effects on PBX, no calls originated.
No raw secrets in stdout.

Usage:
    .venv/bin/python scripts/mango_connectivity_check.py
    .venv/bin/python scripts/mango_connectivity_check.py --raw   # include full raw payloads
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

# Ensure repo root is on the path before any app imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Load .env before pydantic-settings reads environment
from dotenv import load_dotenv  # noqa: E402
load_dotenv()

from app.core.config import settings  # noqa: E402
from app.integrations.telephony.mango_client import (  # noqa: E402
    MangoClient,
    MangoClientError,
)


def _section(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


async def main(*, include_raw: bool = False) -> dict:
    report: dict = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "config": {},
        "checks": [],
        "lines": None,
        "extensions": None,
        "errors": [],
        "verdict": {},
    }

    # ── 1. Config check ──────────────────────────────────────────────────────
    _section("1 / Config Diagnostics")
    client = MangoClient.from_settings()
    diag = client.runtime_diagnostics()
    report["config"] = diag
    print(json.dumps(diag, ensure_ascii=False, indent=2))

    if not diag.get("configured"):
        print("\nFATAL: Mango credentials not configured. Set MANGO_API_KEY and MANGO_API_SALT.")
        report["verdict"] = {"connectivity": "no", "auth": "no", "data": "no", "binding_ready": "no"}
        return report

    # ── 2. List incoming lines ────────────────────────────────────────────────
    _section("2 / POST /incominglines — External phone lines")
    lines_ok = False
    try:
        t0 = time.perf_counter()
        lines = await client.list_lines()
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
        lines_ok = True
        lines_data = {
            "count": len(lines),
            "elapsed_ms": elapsed_ms,
            "sample": [],
        }
        for line in lines[:10]:
            entry: dict = {
                "provider_resource_id": line.provider_resource_id,
                "phone_number": line.phone_number,
                "display_name": line.display_name,
                "extension": line.extension,
                "is_active": line.is_active,
                "is_inbound_enabled": line.is_inbound_enabled,
                "is_outbound_enabled": line.is_outbound_enabled,
            }
            if include_raw:
                entry["raw_payload"] = line.raw_payload
            lines_data["sample"].append(entry)
        report["lines"] = lines_data
        print(f"HTTP 200 OK  ({elapsed_ms} ms)  — {len(lines)} line(s) found")
        print(json.dumps(lines_data, ensure_ascii=False, indent=2))
        report["checks"].append({"name": "/incominglines", "status": "pass", "count": len(lines)})
    except MangoClientError as exc:
        err = {
            "endpoint": "/incominglines",
            "stage": exc.stage,
            "http_status": exc.http_status,
            "detail_preview": str(exc.detail)[:400] if exc.detail else str(exc)[:400],
        }
        print(f"FAILED  stage={exc.stage}  http_status={exc.http_status}")
        print(f"detail: {err['detail_preview']}")
        report["errors"].append(err)
        report["checks"].append({"name": "/incominglines", "status": "fail", **err})
    except Exception as exc:
        err = {"endpoint": "/incominglines", "error": str(exc)[:400]}
        print(f"EXCEPTION: {exc}")
        report["errors"].append(err)
        report["checks"].append({"name": "/incominglines", "status": "fail", **err})

    # ── 3. List extensions / employees ───────────────────────────────────────
    _section("3 / POST /config/users/request — Extensions / employees")
    exts_ok = False
    try:
        t0 = time.perf_counter()
        exts = await client.list_extensions()
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
        exts_ok = True
        exts_data = {
            "count": len(exts),
            "elapsed_ms": elapsed_ms,
            "sample": [],
        }
        for ext in exts[:10]:
            entry = {
                "provider_resource_id": ext.provider_resource_id,
                "extension": ext.extension,
                "display_name": ext.display_name,
                "line_provider_resource_id": ext.line_provider_resource_id,
                "line_phone_number": ext.line_phone_number,
            }
            if include_raw:
                entry["raw_payload"] = ext.raw_payload
            exts_data["sample"].append(entry)
        report["extensions"] = exts_data
        print(f"HTTP 200 OK  ({elapsed_ms} ms)  — {len(exts)} extension(s) found")
        print(json.dumps(exts_data, ensure_ascii=False, indent=2))
        report["checks"].append({"name": "/config/users/request", "status": "pass", "count": len(exts)})
    except MangoClientError as exc:
        err = {
            "endpoint": "/config/users/request",
            "stage": exc.stage,
            "http_status": exc.http_status,
            "detail_preview": str(exc.detail)[:400] if exc.detail else str(exc)[:400],
        }
        print(f"FAILED  stage={exc.stage}  http_status={exc.http_status}")
        print(f"detail: {err['detail_preview']}")
        report["errors"].append(err)
        report["checks"].append({"name": "/config/users/request", "status": "fail", **err})
    except Exception as exc:
        err = {"endpoint": "/config/users/request", "error": str(exc)[:400]}
        print(f"EXCEPTION: {exc}")
        report["errors"].append(err)
        report["checks"].append({"name": "/config/users/request", "status": "fail", **err})

    await client.aclose()

    # ── 4. Verdict ────────────────────────────────────────────────────────────
    _section("4 / Verdict")
    has_connectivity = lines_ok or exts_ok
    has_data = (report["lines"] and report["lines"]["count"] > 0) or \
               (report["extensions"] and report["extensions"]["count"] > 0)
    lines_count = report["lines"]["count"] if report["lines"] else 0
    exts_count = report["extensions"]["count"] if report["extensions"] else 0
    binding_ready = has_data and lines_count > 0

    report["verdict"] = {
        "connectivity": "yes" if has_connectivity else "no",
        "auth_working": "yes" if has_connectivity else "no",
        "inventory_data_available": "yes" if has_data else ("partial" if has_connectivity else "no"),
        "usable_for_agent_number_binding": "yes" if binding_ready else "partial" if has_data else "no",
        "lines_found": lines_count,
        "extensions_found": exts_count,
        "errors_count": len(report["errors"]),
        "webhook_ready": "no — MANGO_WEBHOOK_SECRET is empty",
        "mango_from_ext_set": bool(settings.mango_from_ext),
    }
    print(json.dumps(report["verdict"], ensure_ascii=False, indent=2))

    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mango API connectivity audit")
    parser.add_argument("--raw", action="store_true", help="Include raw API payloads in output")
    args = parser.parse_args()

    result = asyncio.run(main(include_raw=args.raw))

    _section("Full JSON Report")
    print(json.dumps(result, ensure_ascii=False, indent=2))
