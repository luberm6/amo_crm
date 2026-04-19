from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.integrations.telephony.mango_client import (
    MangoClient,
    MangoClientError,
    MangoExtensionPayload,
    MangoLinePayload,
)
from app.models.telephony_line import TelephonyLine
from app.repositories.telephony_line_repo import TelephonyLineRepository

log = get_logger(__name__)


class MangoNotConfiguredError(AppError):
    status_code = 503
    error_code = "mango_not_configured"


class MangoApiUnavailableError(AppError):
    status_code = 502
    error_code = "mango_api_unavailable"


class MangoSyncFailedError(AppError):
    status_code = 502
    error_code = "mango_sync_failed"


@dataclass(frozen=True)
class MangoLineSyncResult:
    items: list[TelephonyLine]
    synced_count: int
    deactivated_count: int
    synced_at: datetime


@dataclass(frozen=True)
class MangoExtensionListResult:
    items: list[MangoExtensionPayload]
    source: str


class MangoTelephonyService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        client: Optional[MangoClient] = None,
    ) -> None:
        self.session = session
        self.repo = TelephonyLineRepository(TelephonyLine, session)
        self.client = client or MangoClient.from_settings()

    async def aclose(self) -> None:
        await self.client.aclose()

    async def list_lines(self, *, active_only: Optional[bool] = None) -> list[TelephonyLine]:
        return await self.repo.list_lines(provider="mango", active_only=active_only)

    async def sync_lines(self) -> MangoLineSyncResult:
        self._ensure_configured()
        synced_at = datetime.now(timezone.utc)
        try:
            remote_lines = await self.client.list_lines()
        except MangoClientError as exc:
            raise MangoSyncFailedError(
                "Failed to load Mango phone lines.",
                detail=self._error_detail(exc),
            ) from exc

        existing = await self.repo.list_lines(provider="mango", active_only=None)
        if not remote_lines and existing:
            raise MangoSyncFailedError(
                "Mango sync returned zero parseable lines; cached telephony inventory was left unchanged.",
                detail={"remote_lines": 0, "cached_lines": len(existing)},
            )

        remote_extensions: list[MangoExtensionPayload] = []
        try:
            remote_extensions = await self.client.list_extensions()
        except MangoClientError as exc:
            log.warning(
                "mango_sync.extensions_unavailable",
                stage=exc.stage,
                http_status=exc.http_status,
                detail=exc.detail,
            )

        extension_by_line_id: dict[str, MangoExtensionPayload] = {}
        extension_by_line_phone: dict[str, MangoExtensionPayload] = {}
        for item in remote_extensions:
            if item.line_provider_resource_id:
                extension_by_line_id[item.line_provider_resource_id] = item
            if item.line_phone_number:
                extension_by_line_phone[item.line_phone_number] = item

        existing_by_resource_id = {line.provider_resource_id: line for line in existing}
        seen_resource_ids: set[str] = set()
        synced_count = 0

        for remote in remote_lines:
            matched_extension = extension_by_line_id.get(remote.provider_resource_id) or extension_by_line_phone.get(
                remote.phone_number
            )
            line = existing_by_resource_id.get(remote.provider_resource_id)
            if line is None:
                line = TelephonyLine(
                    provider="mango",
                    provider_resource_id=remote.provider_resource_id,
                    phone_number=remote.phone_number,
                )

            previous_payload = dict(line.raw_payload or {}) if line.raw_payload else {}
            previous_matched_extension = (
                previous_payload.get("matched_extension")
                if isinstance(previous_payload.get("matched_extension"), dict)
                else None
            )
            line.phone_number = remote.phone_number
            line.schema_name = remote.schema_name
            line.display_name = remote.display_name or remote.schema_name or remote.phone_number
            line.extension = remote.extension or (
                matched_extension.extension if matched_extension is not None else line.extension
            )
            line.is_active = remote.is_active
            line.is_inbound_enabled = remote.is_inbound_enabled
            line.is_outbound_enabled = remote.is_outbound_enabled or matched_extension is not None or bool(line.extension)
            line.raw_payload = dict(remote.raw_payload or {})
            if matched_extension is not None:
                line.raw_payload.setdefault("matched_extension", matched_extension.raw_payload)
            elif previous_matched_extension is not None:
                line.raw_payload.setdefault("matched_extension", previous_matched_extension)
            line.synced_at = synced_at
            await self.repo.save(line)

            seen_resource_ids.add(remote.provider_resource_id)
            synced_count += 1

        deactivated_count = 0
        for stale in existing:
            if stale.provider_resource_id in seen_resource_ids:
                continue
            if stale.is_active:
                stale.is_active = False
                stale.synced_at = synced_at
                await self.repo.save(stale)
                deactivated_count += 1

        log.info(
            "mango_sync.completed",
            synced_count=synced_count,
            deactivated_count=deactivated_count,
            extension_count=len(remote_extensions),
        )
        items = await self.repo.list_lines(provider="mango", active_only=None)
        return MangoLineSyncResult(
            items=items,
            synced_count=synced_count,
            deactivated_count=deactivated_count,
            synced_at=synced_at,
        )

    async def list_extensions(self) -> MangoExtensionListResult:
        self._ensure_configured()
        try:
            live_items = await self.client.list_extensions()
            fallback_items = await self._fallback_extensions_from_inventory()
            return MangoExtensionListResult(
                items=self._merge_extension_items(live_items, fallback_items),
                source="mango_api" if live_items else ("cached_inventory_fallback" if fallback_items else "mango_api"),
            )
        except MangoClientError as exc:
            fallback_items = await self._fallback_extensions_from_inventory()
            if fallback_items:
                log.warning(
                    "mango_extensions.fallback_inventory_used",
                    stage=exc.stage,
                    http_status=exc.http_status,
                    fallback_count=len(fallback_items),
                )
                return MangoExtensionListResult(
                    items=fallback_items,
                    source="cached_inventory_fallback",
                )
            raise MangoApiUnavailableError(
                "Failed to load Mango extensions.",
                detail=self._error_detail(exc),
            ) from exc

    def _ensure_configured(self) -> None:
        if not self.client.config.configured:
            raise MangoNotConfiguredError(
                "Mango telephony is not configured. Set MANGO_API_KEY and MANGO_API_SALT in the backend environment.",
                detail=self.client.runtime_diagnostics(),
            )

    async def _fallback_extensions_from_inventory(self) -> list[MangoExtensionPayload]:
        lines = await self.repo.list_lines(provider="mango", active_only=True)
        items: list[MangoExtensionPayload] = []
        seen_extensions: set[str] = set()

        for line in lines:
            raw_payload = line.raw_payload or {}
            matched_extension = raw_payload.get("matched_extension") if isinstance(raw_payload, dict) else None
            matched_general = (
                matched_extension.get("general")
                if isinstance(matched_extension, dict) and isinstance(matched_extension.get("general"), dict)
                else {}
            )
            matched_telephony = (
                matched_extension.get("telephony")
                if isinstance(matched_extension, dict) and isinstance(matched_extension.get("telephony"), dict)
                else {}
            )
            extension = (line.extension or "").strip()
            if not extension or extension in seen_extensions:
                continue
            items.append(
                MangoExtensionPayload(
                    provider_resource_id=(
                        str(matched_extension.get("id") or matched_extension.get("user_id") or matched_extension.get("employee_id") or extension)
                        if isinstance(matched_extension, dict)
                        else extension
                    ),
                    extension=extension,
                    display_name=(
                        (matched_general.get("name") if isinstance(matched_general, dict) else None)
                        or (matched_extension.get("name") if isinstance(matched_extension, dict) else None)
                        or line.display_name
                        or line.schema_name
                    ),
                    line_provider_resource_id=(
                        str(matched_telephony.get("line_id") or matched_telephony.get("outgoing_line_id"))
                        if isinstance(matched_telephony, dict) and (matched_telephony.get("line_id") or matched_telephony.get("outgoing_line_id"))
                        else line.provider_resource_id
                    ),
                    line_phone_number=line.phone_number,
                    raw_payload={
                        "source": "cached_inventory_fallback",
                        "line_provider_resource_id": line.provider_resource_id,
                        "line_phone_number": line.phone_number,
                    },
                )
            )
            seen_extensions.add(extension)

        from_ext = (settings.mango_from_ext or "").strip()
        if from_ext and from_ext not in seen_extensions:
            items.append(
                MangoExtensionPayload(
                    provider_resource_id=from_ext,
                    extension=from_ext,
                    display_name="Основной исходящий внутренний номер",
                    line_provider_resource_id=None,
                    line_phone_number=None,
                    raw_payload={"source": "env_from_ext_fallback"},
                )
            )

        deduped: dict[tuple[str, str], MangoExtensionPayload] = {}
        for item in items:
            deduped[(item.provider_resource_id, item.extension)] = item
        return list(deduped.values())

    @staticmethod
    def _merge_extension_items(
        live_items: list[MangoExtensionPayload],
        fallback_items: list[MangoExtensionPayload],
    ) -> list[MangoExtensionPayload]:
        merged_by_extension: dict[str, MangoExtensionPayload] = {item.extension: item for item in fallback_items}

        for item in live_items:
            fallback = merged_by_extension.get(item.extension)
            if fallback is None:
                merged_by_extension[item.extension] = item
                continue
            merged_by_extension[item.extension] = MangoExtensionPayload(
                provider_resource_id=item.provider_resource_id or fallback.provider_resource_id,
                extension=item.extension or fallback.extension,
                display_name=item.display_name or fallback.display_name,
                line_provider_resource_id=item.line_provider_resource_id or fallback.line_provider_resource_id,
                line_phone_number=item.line_phone_number or fallback.line_phone_number,
                raw_payload=MangoTelephonyService._merge_extension_raw_payload(item.raw_payload, fallback.raw_payload),
            )

        return sorted(merged_by_extension.values(), key=lambda extension: (extension.extension, extension.provider_resource_id))

    @staticmethod
    def _merge_extension_raw_payload(
        live_payload: dict[str, Any],
        fallback_payload: dict[str, Any],
    ) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        if isinstance(fallback_payload, dict):
            merged.update(fallback_payload)
        if isinstance(live_payload, dict):
            merged.update(live_payload)
        return merged

    @staticmethod
    def _error_detail(exc: MangoClientError) -> dict[str, object]:
        return {
            "stage": exc.stage,
            "http_status": exc.http_status,
            "detail": exc.detail,
        }
