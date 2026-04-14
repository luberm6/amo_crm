from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

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

            line.phone_number = remote.phone_number
            line.display_name = remote.display_name or remote.phone_number
            line.extension = remote.extension or (
                matched_extension.extension if matched_extension is not None else None
            )
            line.is_active = remote.is_active
            line.is_inbound_enabled = remote.is_inbound_enabled
            line.is_outbound_enabled = remote.is_outbound_enabled or matched_extension is not None
            line.raw_payload = dict(remote.raw_payload or {})
            if matched_extension is not None:
                line.raw_payload.setdefault("matched_extension", matched_extension.raw_payload)
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

    async def list_extensions(self) -> list[MangoExtensionPayload]:
        self._ensure_configured()
        try:
            return await self.client.list_extensions()
        except MangoClientError as exc:
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

    @staticmethod
    def _error_detail(exc: MangoClientError) -> dict[str, object]:
        return {
            "stage": exc.stage,
            "http_status": exc.http_status,
            "detail": exc.detail,
        }
