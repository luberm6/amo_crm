#!/usr/bin/env python3
"""
Smoke-test the Mango webhook path against the currently configured backend URL.

Purpose:
- verify that BACKEND_URL points to a reachable webhook endpoint
- verify that configured Mango webhook guards can sign/authorize a request
- verify that inbound number -> agent routing returns a structured result

This is NOT a live Mango delivery test. It exercises the exact backend endpoint
with a Mango-like payload so we can isolate config/runtime issues before asking
the tenant to send a real webhook.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO_ROOT / ".env")

from app.core.config import settings  # noqa: E402


def _is_public_backend_url(url: str) -> bool:
    parsed = urlparse((url or "").strip())
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return False
    if host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local"):
        return False
    return True


def _build_payload(to_number: str, caller_number: str) -> dict[str, Any]:
    stamp = int(time.time())
    return {
        "event": "call_start",
        "entry": {
            "id": f"smoke-leg-{stamp}",
            "call_id": f"smoke-call-{stamp}",
            "from": {"number": caller_number},
            "to": {"number": to_number},
        },
    }


def _headers(raw_body: bytes) -> dict[str, str]:
    if settings.mango_webhook_secret:
        signature = hmac.new(
            settings.mango_webhook_secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        return {
            "Content-Type": "application/json",
            "X-Mango-Signature": signature,
        }
    if settings.mango_webhook_shared_secret:
        return {
            "Content-Type": "application/json",
            "X-Mango-Webhook-Secret": settings.mango_webhook_shared_secret,
        }
    raise RuntimeError(
        "No webhook guard configured. Set MANGO_WEBHOOK_SECRET or "
        "MANGO_WEBHOOK_SHARED_SECRET before running webhook smoke."
    )


async def main(to_number: str, caller_number: str) -> int:
    backend_url = (settings.backend_url or "").rstrip("/")
    if not backend_url:
        print("BLOCKED: BACKEND_URL is empty.")
        return 2
    if not _is_public_backend_url(backend_url):
        print(f"BLOCKED: BACKEND_URL is not public enough for tenant webhook delivery: {backend_url}")
        print("This smoke can still work only if the URL is reachable from your machine/network.")

    webhook_url = f"{backend_url}/v1/webhooks/mango"
    payload = _build_payload(to_number=to_number, caller_number=caller_number)
    raw = json.dumps(payload).encode("utf-8")
    try:
        headers = _headers(raw)
    except RuntimeError as exc:
        print(f"BLOCKED: {exc}")
        return 2

    print("Webhook smoke target:", webhook_url)
    print("Inbound number:", to_number)
    print("Caller number:", caller_number)
    print("Guard mode:", "hmac" if "X-Mango-Signature" in headers else "shared-secret")

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(webhook_url, content=raw, headers=headers)
    try:
        body = response.json()
    except Exception:
        body = {"text": response.text[:1000]}

    print("HTTP status:", response.status_code)
    print(json.dumps(body, ensure_ascii=False, indent=2))

    return 0 if response.status_code < 400 else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Smoke-test Mango webhook path against BACKEND_URL")
    parser.add_argument("--to-number", required=True, help="Inbound Mango number to route, e.g. +79300350609")
    parser.add_argument(
        "--caller-number",
        default="+79990001122",
        help="Synthetic caller number to include in the webhook payload",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.to_number, args.caller_number)))
