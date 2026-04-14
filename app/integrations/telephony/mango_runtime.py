from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.telephony.mango_client import MangoClient, MangoClientError, MangoExtensionPayload

log = get_logger(__name__)


@dataclass(frozen=True)
class ResolvedMangoFromExt:
    value: Optional[str]
    source: str
    matched_line_id: Optional[str] = None
    matched_line_phone_number: Optional[str] = None
    candidate_count: int = 0


async def resolve_mango_from_ext(
    *,
    explicit_from_ext: Optional[str] = None,
    metadata: Optional[dict] = None,
    client: Optional[MangoClient] = None,
) -> ResolvedMangoFromExt:
    metadata = metadata or {}
    direct = (explicit_from_ext or "").strip()
    if direct:
        return ResolvedMangoFromExt(value=direct, source="explicit")

    metadata_from_ext = str(metadata.get("from_ext") or metadata.get("telephony_extension") or "").strip()
    if metadata_from_ext:
        return ResolvedMangoFromExt(value=metadata_from_ext, source="metadata")

    env_from_ext = (settings.mango_from_ext or "").strip()
    if env_from_ext:
        return ResolvedMangoFromExt(value=env_from_ext, source="env")

    own_client = client is None
    mango_client = client or MangoClient.from_settings()
    try:
        extensions = await mango_client.list_extensions()
    except MangoClientError as exc:
        log.warning(
            "mango_from_ext.auto_discovery_failed",
            stage=exc.stage,
            http_status=exc.http_status,
            detail=exc.detail,
        )
        return ResolvedMangoFromExt(value=None, source="auto_discovery_failed")
    finally:
        if own_client:
            await mango_client.aclose()

    if not extensions:
        return ResolvedMangoFromExt(value=None, source="no_extensions", candidate_count=0)

    line_id = str(
        metadata.get("telephony_remote_line_id")
        or metadata.get("mango_remote_line_id")
        or metadata.get("line_number")
        or ""
    ).strip()
    line_phone = str(metadata.get("telephony_line_phone_number") or "").strip()

    matched = _match_extension(extensions, line_id=line_id or None, line_phone=line_phone or None)
    if matched is not None:
        return ResolvedMangoFromExt(
            value=matched.extension,
            source="auto_discovered_by_line",
            matched_line_id=matched.line_provider_resource_id,
            matched_line_phone_number=matched.line_phone_number,
            candidate_count=len(extensions),
        )

    chosen = sorted(extensions, key=lambda item: (item.extension, item.provider_resource_id))[0]
    return ResolvedMangoFromExt(
        value=chosen.extension,
        source="auto_discovered_first_extension",
        matched_line_id=chosen.line_provider_resource_id,
        matched_line_phone_number=chosen.line_phone_number,
        candidate_count=len(extensions),
    )


def _match_extension(
    extensions: list[MangoExtensionPayload],
    *,
    line_id: Optional[str],
    line_phone: Optional[str],
) -> Optional[MangoExtensionPayload]:
    if line_id:
        for item in extensions:
            if item.line_provider_resource_id and item.line_provider_resource_id == line_id:
                return item
    if line_phone:
        for item in extensions:
            if item.line_phone_number and item.line_phone_number == line_phone:
                return item
    return None
