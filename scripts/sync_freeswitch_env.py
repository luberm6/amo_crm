#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Iterable
from urllib import error, parse, request


ROOT = Path(__file__).resolve().parent.parent
LOCAL_ENV_PATH = ROOT / ".env"
ENV_TEMPLATE_PATHS = [
    ROOT / ".env.example",
    ROOT / ".env.local.example",
    ROOT / ".env.production.example",
]
RENDER_CLI_CONFIG = Path.home() / ".render" / "cli.yaml"
DEFAULT_RENDER_SERVICE_NAME = "amo-crm-api"
DEFAULT_RENDER_API_HOST = "https://api.render.com/v1"

FREESWITCH_VALUES = {
    "FREESWITCH_ESL_HOST": "84.247.184.72",
    "FREESWITCH_ESL_PORT": "8021",
    "FREESWITCH_ESL_PASSWORD": "ClueCon",
    "FREESWITCH_SIP_IP": "84.247.184.72",
    "FREESWITCH_RTP_IP": "84.247.184.72",
    "FREESWITCH_WS_URL": "ws://84.247.184.72:5066",
    "FREESWITCH_WSS_URL": "wss://84.247.184.72:7443",
}

MANGO_PLACEHOLDERS = {
    "MANGO_SIP_LOGIN": "",
    "MANGO_SIP_PASSWORD": "",
    "MANGO_SIP_SERVER": "",
}


def _mask_value(key: str, value: str) -> str:
    if not value:
        return "<empty>"
    if "PASSWORD" in key or "SECRET" in key or "KEY" in key:
        if len(value) <= 4:
            return "*" * len(value)
        return f"{value[:2]}***{value[-2:]}"
    return value


def _upsert_env_lines(existing_text: str, updates: dict[str, str]) -> str:
    lines = existing_text.splitlines()
    consumed: set[str] = set()
    updated_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            updated_lines.append(line)
            continue
        key, _value = line.split("=", 1)
        if key in updates:
            updated_lines.append(f"{key}={updates[key]}")
            consumed.add(key)
        else:
            updated_lines.append(line)

    for key, value in updates.items():
        if key not in consumed:
            updated_lines.append(f"{key}={value}")

    return "\n".join(updated_lines).rstrip() + "\n"


def update_env_file(path: Path, updates: dict[str, str], *, dry_run: bool = False) -> None:
    original = path.read_text() if path.exists() else ""
    next_text = _upsert_env_lines(original, updates)
    if dry_run:
        print(f"[dry-run] would update {path}")
        return
    path.write_text(next_text)
    print(f"[ok] updated {path}")


def _load_render_credentials() -> tuple[str, str]:
    token = (os.environ.get("RENDER_API_KEY") or "").strip()
    host = (os.environ.get("RENDER_API_HOST") or "").strip() or DEFAULT_RENDER_API_HOST
    if token:
        return host.rstrip("/"), token

    if not RENDER_CLI_CONFIG.exists():
        raise RuntimeError(
            "RENDER_API_KEY is not set and ~/.render/cli.yaml was not found. "
            "Local env sync still works; Render sync needs one of those auth sources."
        )

    text = RENDER_CLI_CONFIG.read_text()
    token_match = re.search(r"^\s*key:\s*(\S+)\s*$", text, re.MULTILINE)
    host_match = re.search(r"^\s*host:\s*(\S+)\s*$", text, re.MULTILINE)
    if not token_match:
        raise RuntimeError("Could not find Render API key in ~/.render/cli.yaml")
    return (host_match.group(1).rstrip("/") if host_match else DEFAULT_RENDER_API_HOST), token_match.group(1)


def _render_request(api_host: str, token: str, method: str, path: str, payload: object | None = None) -> object:
    data = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = request.Request(f"{api_host}{path}", method=method, headers=headers, data=data)
    with request.urlopen(req) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body) if body else {}


def _iter_render_services(api_host: str, token: str) -> Iterable[dict[str, object]]:
    next_path = "/services?limit=100"
    while next_path:
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        req = request.Request(f"{api_host}{next_path}", headers=headers)
        with request.urlopen(req) as resp:
            services = json.loads(resp.read().decode("utf-8"))
            link_header = resp.headers.get("Link", "")
        for item in services:
            yield item
        next_match = re.search(r"<([^>]+)>;\s*rel=\"next\"", link_header)
        next_path = next_match.group(1).replace(api_host, "", 1) if next_match else ""


def resolve_render_service_id(api_host: str, token: str, service_name: str) -> str:
    explicit = (os.environ.get("RENDER_SERVICE_ID") or "").strip()
    if explicit:
        return explicit
    for service in _iter_render_services(api_host, token):
        payload = service.get("service") if isinstance(service.get("service"), dict) else service
        if payload.get("name") == service_name:
            return str(payload["id"])
    raise RuntimeError(f"Could not find Render service named {service_name!r}")


def sync_render_env(service_name: str, updates: dict[str, str], *, dry_run: bool = False) -> None:
    api_host, token = _load_render_credentials()
    service_id = resolve_render_service_id(api_host, token, service_name)
    payload = [{"key": key, "value": value} for key, value in updates.items()]
    if dry_run:
        print(f"[dry-run] would sync {len(payload)} env vars to Render service {service_name} ({service_id})")
        for key, value in updates.items():
            print(f"  - {key}={_mask_value(key, value)}")
        return
    _render_request(api_host, token, "PUT", f"/services/{service_id}/env-vars", payload)
    print(f"[ok] synced {len(payload)} env vars to Render service {service_name} ({service_id})")
    for key, value in updates.items():
        print(f"  - {key}={_mask_value(key, value)}")


def build_updates() -> dict[str, str]:
    merged = dict(FREESWITCH_VALUES)
    merged.update(MANGO_PLACEHOLDERS)
    return merged


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Idempotently sync FreeSWITCH/Mango telephony env locally and to Render.",
    )
    parser.add_argument("--local-only", action="store_true", help="Only update local env files, skip Render sync.")
    parser.add_argument("--render-only", action="store_true", help="Only sync Render env, skip local files.")
    parser.add_argument("--dry-run", action="store_true", help="Print changes without writing them.")
    parser.add_argument(
        "--render-service",
        default=DEFAULT_RENDER_SERVICE_NAME,
        help=f"Render service name to update (default: {DEFAULT_RENDER_SERVICE_NAME}).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.local_only and args.render_only:
        print("[error] --local-only and --render-only cannot be combined", file=sys.stderr)
        return 2

    updates = build_updates()

    try:
        if not args.render_only:
            update_env_file(LOCAL_ENV_PATH, updates, dry_run=args.dry_run)
            for template_path in ENV_TEMPLATE_PATHS:
                update_env_file(template_path, updates, dry_run=args.dry_run)

        if not args.local_only:
            sync_render_env(args.render_service, updates, dry_run=args.dry_run)
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        print(f"[error] Render API request failed: HTTP {exc.code}", file=sys.stderr)
        if body:
            print(body[:2000], file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"[error] {exc}", file=sys.stderr)
        return 1

    print("[done] FreeSWITCH env sync complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
