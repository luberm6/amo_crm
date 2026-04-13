from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class MangoApiConfig:
    base_url: str
    api_key: str
    api_salt: str
    timeout_seconds: float = 15.0

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.api_salt)


@dataclass(frozen=True)
class MangoLinePayload:
    provider_resource_id: str
    phone_number: str
    display_name: Optional[str]
    extension: Optional[str]
    is_active: bool
    is_inbound_enabled: bool
    is_outbound_enabled: bool
    raw_payload: dict[str, Any]


@dataclass(frozen=True)
class MangoExtensionPayload:
    provider_resource_id: str
    extension: str
    display_name: Optional[str]
    line_provider_resource_id: Optional[str]
    line_phone_number: Optional[str]
    raw_payload: dict[str, Any]


class MangoClientError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        stage: str,
        http_status: Optional[int] = None,
        detail: Any = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.http_status = http_status
        self.detail = detail


class MangoClient:
    def __init__(self, config: MangoApiConfig) -> None:
        self.config = config
        self._http = httpx.AsyncClient(
            base_url=config.base_url.rstrip("/"),
            timeout=config.timeout_seconds,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    @classmethod
    def from_settings(cls) -> "MangoClient":
        return cls(
            MangoApiConfig(
                base_url=(settings.mango_api_base_url or "https://app.mango-office.ru/vpbx").strip(),
                api_key=(settings.mango_api_key or "").strip(),
                api_salt=(settings.mango_api_salt or "").strip(),
            )
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    def runtime_diagnostics(self) -> dict[str, Any]:
        return {
            "base_url": self.config.base_url,
            "api_key_set": bool(self.config.api_key),
            "api_key_masked": _mask_secret(self.config.api_key),
            "api_salt_set": bool(self.config.api_salt),
            "configured": self.config.configured,
        }

    async def list_lines(self) -> list[MangoLinePayload]:
        payload = await self._post_json("/incominglines", {})
        records = _extract_records(payload, preferred_keys=("incominglines", "lines", "numbers"))
        lines: list[MangoLinePayload] = []
        for record in records:
            provider_resource_id = _first_non_empty(
                record,
                "id",
                "line_id",
                "incomingline_id",
                "number_id",
                "line_number_id",
                "lineNumberId",
                "numberId",
            )
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
            lines.append(
                MangoLinePayload(
                    provider_resource_id=provider_resource_id or phone_number,
                    phone_number=phone_number,
                    display_name=_first_non_empty(
                        record,
                        "display_name",
                        "name",
                        "title",
                        "label",
                        "line_name",
                        "schema_name",  # Mango: human-readable routing schema name (e.g. "ДЛЯ ИИ менеджера")
                    ),
                    extension=_first_non_empty(
                        record,
                        "extension",
                        "internal_number",
                        "sip_number",
                    ),
                    is_active=_coerce_bool(record.get("is_active"), default=True),
                    is_inbound_enabled=_coerce_bool(
                        _first_value(record, "is_inbound_enabled", "inbound_enabled", "can_receive"),
                        default=True,
                    ),
                    is_outbound_enabled=_coerce_bool(
                        _first_value(record, "is_outbound_enabled", "outbound_enabled", "can_call_out"),
                        default=False,
                    ),
                    raw_payload=record,
                )
            )

        log.info(
            "mango_client.lines_loaded",
            line_count=len(lines),
            base_url=self.config.base_url,
            api_key_masked=_mask_secret(self.config.api_key),
        )
        return _deduplicate_lines(lines)

    async def list_extensions(self) -> list[MangoExtensionPayload]:
        payload = await self._post_json("/config/users/request", {})
        records = _extract_records(payload, preferred_keys=("users", "employees", "extensions"))
        extensions: list[MangoExtensionPayload] = []
        for record in records:
            extension = _first_non_empty(
                record,
                "extension",
                "internal_number",
                "sip_number",
                "short_number",
                "number",
            )
            if not extension:
                continue
            provider_resource_id = _first_non_empty(record, "id", "user_id", "employee_id") or extension
            extensions.append(
                MangoExtensionPayload(
                    provider_resource_id=provider_resource_id,
                    extension=extension,
                    display_name=_first_non_empty(record, "name", "full_name", "fio", "title"),
                    line_provider_resource_id=_first_non_empty(
                        record,
                        "line_id",
                        "outgoing_line_id",
                        "line_number_id",
                    ),
                    line_phone_number=_first_non_empty(
                        record,
                        "line_number",
                        "outgoing_line",
                        "phone_number",
                    ),
                    raw_payload=record,
                )
            )

        log.info(
            "mango_client.extensions_loaded",
            extension_count=len(extensions),
            base_url=self.config.base_url,
            api_key_masked=_mask_secret(self.config.api_key),
        )
        return _deduplicate_extensions(extensions)

    async def _post_json(self, path: str, payload: dict[str, Any]) -> Any:
        if not self.config.configured:
            raise MangoClientError(
                "Mango API credentials are not configured.",
                stage="configuration",
            )

        signed = _build_signed_payload(self.config.api_key, self.config.api_salt, payload)
        log.info(
            "mango_client.request_started",
            path=path,
            base_url=self.config.base_url,
            payload_keys=sorted(payload.keys()),
            api_key_masked=_mask_secret(self.config.api_key),
        )
        try:
            response = await self._http.post(path, data=signed)
        except httpx.RequestError as exc:
            raise MangoClientError(
                f"Mango request failed: {exc}",
                stage="http_request",
                detail={"path": path},
            ) from exc

        if response.status_code >= 400:
            detail = _read_response_detail(response)
            log.warning(
                "mango_client.request_failed",
                path=path,
                http_status=response.status_code,
                detail_preview=_detail_preview(detail),
            )
            raise MangoClientError(
                f"Mango API returned HTTP {response.status_code} for {path}.",
                stage="http_response",
                http_status=response.status_code,
                detail=detail,
            )

        try:
            data = response.json()
        except Exception as exc:
            raise MangoClientError(
                f"Mango API returned a non-JSON payload for {path}.",
                stage="response_parse",
                http_status=response.status_code,
                detail={"text": response.text[:500]},
            ) from exc

        log.info(
            "mango_client.request_completed",
            path=path,
            http_status=response.status_code,
            top_level_keys=sorted(data.keys()) if isinstance(data, dict) else None,
        )
        return data


def _build_signed_payload(api_key: str, api_salt: str, payload: dict[str, Any]) -> dict[str, str]:
    json_payload = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    sign_source = f"{api_key}{json_payload}{api_salt}"
    sign = hashlib.sha256(sign_source.encode("utf-8")).hexdigest()
    return {"vpbx_api_key": api_key, "sign": sign, "json": json_payload}


def _extract_records(payload: Any, *, preferred_keys: tuple[str, ...]) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []

    for key in preferred_keys:
        candidate = payload.get(key)
        records = _extract_records(candidate, preferred_keys=())
        if records:
            return records

    for value in payload.values():
        records = _extract_records(value, preferred_keys=())
        if records:
            return records
    return []


def _first_non_empty(record: dict[str, Any], *keys: str) -> Optional[str]:
    value = _first_value(record, *keys)
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    return str(value)


def _first_value(record: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in record and record[key] not in (None, ""):
            return record[key]
    return None


def _coerce_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        cleaned = value.strip().lower()
        if cleaned in {"1", "true", "yes", "enabled", "active"}:
            return True
        if cleaned in {"0", "false", "no", "disabled", "inactive"}:
            return False
    return default


def _read_response_detail(response: httpx.Response) -> Any:
    try:
        return response.json()
    except Exception:
        return response.text[:1000]


def _detail_preview(detail: Any) -> Optional[str]:
    if detail is None:
        return None
    if isinstance(detail, str):
        return detail[:200]
    try:
        return json.dumps(detail, ensure_ascii=False)[:200]
    except Exception:
        return str(detail)[:200]


def _mask_secret(value: str) -> Optional[str]:
    cleaned = (value or "").strip()
    if not cleaned:
        return None
    if len(cleaned) <= 4:
        return "*" * len(cleaned)
    if len(cleaned) <= 8:
        return f"{cleaned[:1]}***{cleaned[-1:]}"
    return f"{cleaned[:2]}***{cleaned[-2:]}"


def _deduplicate_lines(lines: list[MangoLinePayload]) -> list[MangoLinePayload]:
    deduped: dict[tuple[str, str], MangoLinePayload] = {}
    for item in lines:
        deduped[(item.provider_resource_id, item.phone_number)] = item
    return list(deduped.values())


def _deduplicate_extensions(items: list[MangoExtensionPayload]) -> list[MangoExtensionPayload]:
    deduped: dict[tuple[str, str], MangoExtensionPayload] = {}
    for item in items:
        deduped[(item.provider_resource_id, item.extension)] = item
    return list(deduped.values())
