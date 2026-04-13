from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_auth import require_admin_auth
from app.api.deps import get_db
from app.core.exceptions import AppError
from app.schemas.telephony import (
    TelephonyExtensionListRead,
    TelephonyExtensionRead,
    TelephonyLineListRead,
    TelephonyLineRead,
    TelephonyLineSyncRead,
)
from app.services.mango_telephony_service import MangoTelephonyService

router = APIRouter(
    prefix="/telephony",
    tags=["telephony"],
    dependencies=[Depends(require_admin_auth)],
)


def _handle_app_error(exc: AppError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())


@router.get("/mango/lines", response_model=TelephonyLineListRead)
async def list_mango_lines(
    db: AsyncSession = Depends(get_db),
) -> TelephonyLineListRead:
    service = MangoTelephonyService(db)
    try:
        items = await service.list_lines(active_only=None)
    finally:
        await service.aclose()
    return TelephonyLineListRead(items=[TelephonyLineRead.model_validate(item) for item in items], total=len(items))


@router.post("/mango/sync-lines", response_model=TelephonyLineSyncRead)
async def sync_mango_lines(
    db: AsyncSession = Depends(get_db),
) -> TelephonyLineSyncRead:
    service = MangoTelephonyService(db)
    try:
        result = await service.sync_lines()
    except AppError as exc:
        _handle_app_error(exc)
    finally:
        await service.aclose()
    await db.commit()
    return TelephonyLineSyncRead(
        items=[TelephonyLineRead.model_validate(item) for item in result.items],
        total=len(result.items),
        synced_count=result.synced_count,
        deactivated_count=result.deactivated_count,
        synced_at=result.synced_at,
    )


@router.get("/mango/extensions", response_model=TelephonyExtensionListRead)
async def list_mango_extensions(
    db: AsyncSession = Depends(get_db),
) -> TelephonyExtensionListRead:
    service = MangoTelephonyService(db)
    try:
        items = await service.list_extensions()
    except AppError as exc:
        _handle_app_error(exc)
    finally:
        await service.aclose()
    return TelephonyExtensionListRead(
        items=[
            TelephonyExtensionRead(
                provider_resource_id=item.provider_resource_id,
                extension=item.extension,
                display_name=item.display_name,
                line_provider_resource_id=item.line_provider_resource_id,
                line_phone_number=item.line_phone_number,
            )
            for item in items
        ],
        total=len(items),
    )
