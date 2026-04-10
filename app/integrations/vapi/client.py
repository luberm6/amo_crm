"""
Vapi HTTP client.
Thin wrapper around httpx with:
- Bearer auth header
- JSON request/response handling
- Consistent error translation → EngineError
All Vapi REST calls go through here. This is the only place that knows
the Vapi API shape. If Vapi changes their API, only this file changes.
Vapi API reference: https://docs.vapi.ai/api-reference
"""
from __future__ import annotations
from typing import Optional
import httpx
from app.core.config import Settings
from app.core.exceptions import EngineError
from app.core.logging import get_logger
log = get_logger(__name__)
class VapiClient:
    """
    Async HTTP client for the Vapi REST API.
    Lifecycle: create once per engine instance, share across calls.
    The underlying httpx.AsyncClient is reused for connection pooling.
    """
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._http = httpx.AsyncClient(
            base_url=settings.vapi_base_url,
            headers={
                "Authorization": f"Bearer {settings.vapi_api_key}",
                "Content-Type": "application/json",
            },
            timeout=15.0,
        )
    async def aclose(self) -> None:
        await self._http.aclose()
    # ── Call CRUD ─────────────────────────────────────────────────────────────
    async def create_phone_call(
        self, customer_phone: str, metadata: Optional[dict] = None
    ) -> dict[str, Any]:
        """
        Create an outbound phone call via Vapi.
        POST /call/phone
        Returns the Vapi call object on success.
        """
        payload: dict[str, Any] = {
            "assistantId": self._settings.vapi_assistant_id,
            "phoneNumberId": self._settings.vapi_phone_number_id,
            "customer": {"number": customer_phone},
        }
        if metadata:
            # Pass our internal IDs through Vapi metadata for webhook correlation
            payload["assistantOverrides"] = {"metadata": metadata}
        log.info("vapi.create_phone_call", customer_phone=customer_phone)
        return await self._post("/call/phone", payload)
    async def get_call(self, vapi_call_id: str) -> dict[str, Any]:
        """GET /call/{id} — fetch current call state from Vapi."""
        return await self._get(f"/call/{vapi_call_id}")
    async def delete_call(self, vapi_call_id: str) -> None:
        """DELETE /call/{id} — terminate an active call."""
        log.info("vapi.delete_call", vapi_call_id=vapi_call_id)
        await self._delete(f"/call/{vapi_call_id}")
    async def inject_message(self, vapi_call_id: str, message: str) -> None:
        """
        Inject a system message into a live call.
        POST /call/{id}/say — real-time instruction delivery to the AI.
        No-op if the call has already ended (Vapi returns 4xx).
        """
        log.info(
            "vapi.inject_message",
            vapi_call_id=vapi_call_id,
            message_preview=message[:80],
        )
        try:
            await self._post(
                f"/call/{vapi_call_id}/say",
                {"message": message, "endCallAfterSpoken": False},
            )
        except EngineError as exc:
            # Log but don't crash — instruction injection is best-effort
            log.warning(
                "vapi.inject_message.failed",
                vapi_call_id=vapi_call_id,
                error=str(exc),
            )
    # ── HTTP helpers ──────────────────────────────────────────────────────────
    async def _post(self, path: str, body: dict) -> dict[str, Any]:
        try:
            resp = await self._http.post(path, json=body)
            self._raise_for_status(path, resp)
            return resp.json()
        except httpx.RequestError as exc:
            raise EngineError(f"Vapi network error on POST {path}: {exc}") from exc
    async def _get(self, path: str) -> dict[str, Any]:
        try:
            resp = await self._http.get(path)
            self._raise_for_status(path, resp)
            return resp.json()
        except httpx.RequestError as exc:
            raise EngineError(f"Vapi network error on GET {path}: {exc}") from exc
    async def _delete(self, path: str) -> None:
        try:
            resp = await self._http.delete(path)
            # 404 is OK — call already ended
            if resp.status_code not in (200, 201, 204, 404):
                self._raise_for_status(path, resp)
        except httpx.RequestError as exc:
            raise EngineError(f"Vapi network error on DELETE {path}: {exc}") from exc
    @staticmethod
    def _raise_for_status(path: str, resp: httpx.Response) -> None:
        if resp.status_code >= 400:
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text
            raise EngineError(
                f"Vapi API error {resp.status_code} on {path}",
                detail=detail,
            )