"""
Backend HTTP client for the Telegram bot.
All bot→backend communication goes through these helpers.
Using httpx.AsyncClient as a context manager per call — simple and safe for
a low-traffic Telegram bot (connection pool overhead is negligible here).
For high-throughput scenarios, swap to a module-level shared client with
lifespan management in bot/main.py.
"""
from __future__ import annotations

from typing import Any, Optional

import httpx
from app.core.config import settings
from app.core.logging import get_logger
log = get_logger(__name__)
_BASE = settings.backend_url.rstrip("/")
_TIMEOUT = 15.0
class ApiError(Exception):
    """Raised when the backend returns a non-2xx response."""
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
async def _get(path: str, **params: Any) -> dict:
    async with httpx.AsyncClient(base_url=_BASE, timeout=_TIMEOUT) as client:
        try:
            resp = await client.get(path, params=params or None)
        except httpx.RequestError as exc:
            raise ApiError(0, f"Backend unreachable: {exc}") from exc
    if resp.status_code >= 400:
        _raise_api_error(resp)
    return resp.json()
async def _post(path: str, body: dict) -> dict:
    async with httpx.AsyncClient(base_url=_BASE, timeout=_TIMEOUT) as client:
        try:
            resp = await client.post(path, json=body)
        except httpx.RequestError as exc:
            raise ApiError(0, f"Backend unreachable: {exc}") from exc
    if resp.status_code >= 400:
        _raise_api_error(resp)
    return resp.json()
def _raise_api_error(resp: httpx.Response) -> None:
    try:
        detail = resp.json()
        msg = detail.get("message") or detail.get("detail") or str(detail)
    except Exception:
        msg = resp.text or f"HTTP {resp.status_code}"
    raise ApiError(resp.status_code, msg)
# ── Public API helpers ────────────────────────────────────────────────────────
async def create_call(phone: str, mode: str = "auto") -> dict:
    return await _post("/v1/calls", {"phone": phone, "mode": mode})
async def get_active_calls() -> dict:
    return await _get("/v1/calls/active")
async def get_call_card(call_id: str, tail: int = 5) -> dict:
    """GET /calls/{id}/card — compact view for live card rendering."""
    return await _get(f"/v1/calls/{call_id}/card", tail=tail)
async def steer_call(call_id: str, instruction: str, issued_by: str) -> dict:
    return await _post(
        f"/v1/calls/{call_id}/steer",
        {"instruction": instruction, "issued_by": issued_by},
    )
async def stop_call(call_id: str) -> dict:
    return await _post(f"/v1/calls/{call_id}/stop", {})


async def initiate_transfer(call_id: str, department: Optional[str] = None) -> dict:
    """POST /v1/calls/{id}/transfer — start warm transfer."""
    body: dict = {}
    if department is not None:
        body["department"] = department
    return await _post(f"/v1/calls/{call_id}/transfer", body)


async def get_manager_context(call_id: str) -> dict:
    """GET /v1/calls/{id}/manager-context — summary/whisper for manager UI."""
    return await _get(f"/v1/calls/{call_id}/manager-context")