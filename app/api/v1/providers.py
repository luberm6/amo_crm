from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin_auth import require_admin_auth
from app.api.deps import get_db
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.schemas.provider_settings import (
    ProviderSettingRead,
    ProviderSettingsListRead,
    ProviderSettingUpdate,
    ProviderValidationRead,
)
from app.services.provider_settings_service import ProviderSettingsService

log = get_logger(__name__)

router = APIRouter(
    prefix="/providers",
    tags=["providers"],
    dependencies=[Depends(require_admin_auth)],
)


def _handle_app_error(exc: AppError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.to_dict())


@router.get("/settings", response_model=ProviderSettingsListRead)
async def list_provider_settings(
    db: AsyncSession = Depends(get_db),
) -> ProviderSettingsListRead:
    service = ProviderSettingsService(db)
    items = await service.list_settings()
    return ProviderSettingsListRead(items=items)


@router.patch("/settings/{provider}", response_model=ProviderSettingRead)
async def update_provider_settings(
    provider: str,
    body: ProviderSettingUpdate,
    db: AsyncSession = Depends(get_db),
) -> ProviderSettingRead:
    service = ProviderSettingsService(db)
    try:
        setting = await service.update_provider(
            provider,
            is_enabled=body.is_enabled,
            config=body.config,
            secrets=body.secrets,
        )
    except AppError as exc:
        _handle_app_error(exc)
    except Exception as exc:
        log.exception("provider_settings.save_failed", provider=provider)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "provider_settings_save_failed",
                "message": str(exc),
                "provider": provider,
            },
        ) from exc
    await db.commit()
    return setting


@router.post("/settings/{provider}/validate", response_model=ProviderValidationRead)
async def validate_provider_settings(
    provider: str,
    db: AsyncSession = Depends(get_db),
) -> ProviderValidationRead:
    service = ProviderSettingsService(db)
    try:
        result = await service.validate_provider(provider)
    except AppError as exc:
        _handle_app_error(exc)
    except Exception as exc:
        log.exception("provider_settings.validate_failed", provider=provider)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "provider_settings_validate_failed",
                "message": str(exc),
                "provider": provider,
            },
        ) from exc
    await db.commit()
    return result
