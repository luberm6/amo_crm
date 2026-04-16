from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AppError, EngineError, NotFoundError
from app.core.logging import get_logger
from app.integrations.voice.elevenlabs import ElevenLabsClient
from app.models.provider_setting import ProviderSetting
from app.repositories.provider_setting_repo import ProviderSettingRepository
from app.schemas.provider_settings import ProviderSecretRead, ProviderSettingRead, ProviderValidationRead

log = get_logger(__name__)


class ProviderSettingsValidationError(AppError):
    status_code = 422
    error_code = "provider_settings_validation_error"


class ProviderSettingsEncryptionError(AppError):
    status_code = 503
    error_code = "provider_settings_encryption_unavailable"


@dataclass(frozen=True)
class ProviderSpec:
    provider: str
    display_name: str
    config_defaults: dict[str, Any]
    config_fields: tuple[str, ...]
    secret_fields: tuple[str, ...]
    required_for_validation: tuple[str, ...]
    safe_mode_note: str
    supports_remote_validation: bool


PROVIDER_SPECS: dict[str, ProviderSpec] = {
    "mango": ProviderSpec(
        provider="mango",
        display_name="Mango",
        config_defaults={
            "from_ext": "",
            "webhook_ip_allowlist": "",
        },
        config_fields=("from_ext", "webhook_ip_allowlist"),
        secret_fields=("api_key", "api_salt", "webhook_secret", "webhook_shared_secret"),
        required_for_validation=("api_key", "api_salt"),
        safe_mode_note=(
            "Saving Mango credentials here does not activate AI routing, does not sync numbers, "
            "and does not take over amoCRM-linked numbers."
        ),
        supports_remote_validation=False,
    ),
    "gemini": ProviderSpec(
        provider="gemini",
        display_name="Gemini",
        config_defaults={
            "model_id": "gemini-2.5-flash-native-audio-preview-12-2025",
            "api_version": "v1beta",
        },
        config_fields=("model_id", "api_version"),
        secret_fields=("api_key",),
        required_for_validation=("api_key", "model_id", "api_version"),
        safe_mode_note="These settings are stored independently and do not change the active runtime until wired explicitly.",
        supports_remote_validation=True,
    ),
    "elevenlabs": ProviderSpec(
        provider="elevenlabs",
        display_name="ElevenLabs",
        config_defaults={"voice_id": "", "enabled": True},
        config_fields=("voice_id", "enabled"),
        secret_fields=("api_key",),
        required_for_validation=("api_key", "voice_id"),
        safe_mode_note="Saving ElevenLabs settings does not switch the live voice path automatically.",
        supports_remote_validation=True,
    ),
    "vapi": ProviderSpec(
        provider="vapi",
        display_name="Vapi",
        config_defaults={
            "assistant_id": "",
            "phone_number_id": "",
            "base_url": "https://api.vapi.ai",
            "server_url": "",
        },
        config_fields=("assistant_id", "phone_number_id", "base_url", "server_url"),
        secret_fields=("api_key", "webhook_secret"),
        required_for_validation=("api_key", "assistant_id", "base_url"),
        safe_mode_note="Vapi settings are stored only as provider config. They do not change call routing by themselves.",
        supports_remote_validation=True,
    ),
}


class ProviderSettingsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = ProviderSettingRepository(ProviderSetting, session)

    async def list_settings(self) -> list[ProviderSettingRead]:
        records = {record.provider: record for record in await self.repo.list_all()}
        return [await self._to_read(spec.provider, records.get(spec.provider)) for spec in PROVIDER_SPECS.values()]

    async def update_provider(
        self,
        provider: str,
        *,
        is_enabled: Optional[bool],
        config: dict[str, Any],
        secrets: dict[str, Optional[str]],
    ) -> ProviderSettingRead:
        spec = self._get_spec(provider)
        record = await self.repo.get_by_provider(provider)
        if record is None:
            record = ProviderSetting(
                provider=provider,
                is_enabled=False,
                config=dict(spec.config_defaults),
                secrets_encrypted=self._encrypt_secrets({}),
                validation_status="not_tested",
                last_validation_message=None,
                last_validation_remote_checked=False,
                last_validated_at=None,
            )

        if is_enabled is not None:
            record.is_enabled = bool(is_enabled)

        current_config = dict(spec.config_defaults)
        current_config.update(record.config or {})
        if config:
            unknown_fields = sorted(set(config.keys()) - set(spec.config_fields))
            if unknown_fields:
                raise ProviderSettingsValidationError(
                    f"Unsupported config fields for provider {provider}.",
                    detail={"unknown_fields": unknown_fields},
                )
            current_config.update({key: value for key, value in config.items()})
        record.config = self._normalize_config(spec, current_config)

        current_secrets: dict[str, str] = {}
        secrets_locked = False
        try:
            current_secrets = self._decrypt_secrets(record.secrets_encrypted)
        except ProviderSettingsEncryptionError:
            secrets_locked = bool(record.secrets_encrypted)
            current_secrets = {}
        if secrets:
            unknown_secret_fields = sorted(set(secrets.keys()) - set(spec.secret_fields))
            if unknown_secret_fields:
                raise ProviderSettingsValidationError(
                    f"Unsupported secret fields for provider {provider}.",
                    detail={"unknown_fields": unknown_secret_fields},
                )
            for key, value in secrets.items():
                if value is None:
                    current_secrets.pop(key, None)
                    continue
                cleaned = value.strip()
                if cleaned:
                    current_secrets[key] = cleaned
        if secrets_locked and not any(
            isinstance(value, str) and value.strip()
            for value in (secrets or {}).values()
        ):
            raise ProviderSettingsEncryptionError(
                self._provider_secret_reentry_message(spec)
            )
        record.secrets_encrypted = self._encrypt_secrets(current_secrets)
        record.validation_status = "not_tested"
        record.last_validation_message = "Settings changed and need explicit validation."
        record.last_validation_remote_checked = False
        record.last_validated_at = None

        saved = await self.repo.save(record)
        return await self._to_read(provider, saved)

    async def validate_provider(self, provider: str) -> ProviderValidationRead:
        spec = self._get_spec(provider)
        record = await self.repo.get_by_provider(provider)
        if record is None:
            raise NotFoundError(f"Provider settings for {provider} are not saved yet")

        config = dict(spec.config_defaults)
        config.update(record.config or {})
        checked_at = datetime.now(timezone.utc)
        try:
            secrets = self._decrypt_secrets(record.secrets_encrypted)
        except ProviderSettingsEncryptionError:
            message = self._provider_secret_reentry_message(spec)
            record.validation_status = "invalid"
            record.last_validation_message = message
            record.last_validation_remote_checked = False
            record.last_validated_at = checked_at
            await self.repo.save(record)
            return ProviderValidationRead(
                provider=provider,
                status="invalid",
                message=message,
                remote_checked=False,
                checked_at=checked_at,
            )
        missing = self._missing_required_fields(spec, config, secrets)

        if missing:
            record.validation_status = "invalid"
            record.last_validation_message = (
                "Missing required settings: " + ", ".join(missing)
            )
            record.last_validation_remote_checked = False
            record.last_validated_at = checked_at
            await self.repo.save(record)
            return ProviderValidationRead(
                provider=provider,
                status="invalid",
                message=record.last_validation_message,
                remote_checked=False,
                checked_at=checked_at,
            )

        try:
            message, remote_checked = await self._run_validation(spec, config, secrets)
            record.validation_status = "configured"
            record.last_validation_message = message
            record.last_validation_remote_checked = remote_checked
        except ProviderSettingsValidationError as exc:
            record.validation_status = "invalid"
            record.last_validation_message = exc.message
            record.last_validation_remote_checked = spec.supports_remote_validation
            await self.repo.save(record)
            record.last_validated_at = checked_at
            await self.repo.save(record)
            return ProviderValidationRead(
                provider=provider,
                status="invalid",
                message=exc.message,
                remote_checked=spec.supports_remote_validation,
                checked_at=checked_at,
            )

        record.last_validated_at = checked_at
        await self.repo.save(record)
        return ProviderValidationRead(
            provider=provider,
            status="configured",
            message=record.last_validation_message or "Provider settings validated.",
            remote_checked=record.last_validation_remote_checked,
            checked_at=checked_at,
        )

    async def _to_read(self, provider: str, record: Optional[ProviderSetting]) -> ProviderSettingRead:
        spec = self._get_spec(provider)
        config = dict(spec.config_defaults)
        secrets: dict[str, str] = {}
        status = record.validation_status if record is not None else "not_tested"
        storage_warning: str | None = None
        secrets_accessible = True
        if record is not None:
            config.update(record.config or {})
            try:
                secrets = self._decrypt_secrets(record.secrets_encrypted)
            except ProviderSettingsEncryptionError:
                secrets = {}
                secrets_accessible = False
                status = "invalid"
                storage_warning = self._provider_secret_reentry_message(spec)
                log.warning(
                    "provider_settings.decryption_unavailable",
                    provider=provider,
                    hint="reenter_secrets_and_save_again",
                )

        return ProviderSettingRead(
            provider=provider,
            display_name=spec.display_name,
            is_enabled=bool(record.is_enabled) if record is not None else False,
            activation_status="active" if record is not None and record.is_enabled else "inactive",
            status=status,
            safe_mode_note=spec.safe_mode_note,
            config=config,
            secrets={
                key: ProviderSecretRead(is_set=key in secrets and bool(secrets.get(key)), masked_value=_mask_secret(secrets.get(key)))
                for key in spec.secret_fields
            },
            secrets_accessible=secrets_accessible,
            storage_warning=storage_warning,
            last_validated_at=record.last_validated_at if record is not None else None,
            last_validation_message=record.last_validation_message if record is not None else None,
            last_validation_remote_checked=bool(record.last_validation_remote_checked) if record is not None else False,
        )

    def _provider_secret_reentry_message(self, spec: ProviderSpec) -> str:
        return (
            f"Stored {spec.display_name} secrets were encrypted with a different secret. "
            "Re-enter the secret fields and save again under the current provider settings secret."
        )

    async def _run_validation(
        self,
        spec: ProviderSpec,
        config: dict[str, Any],
        secrets: dict[str, str],
    ) -> tuple[str, bool]:
        if spec.provider == "mango":
            return (
                "Mango settings were validated in safe mode only. No number sync, routing, or telephony-side call was triggered.",
                False,
            )
        if spec.provider == "gemini":
            await self._validate_gemini(config, secrets)
            return ("Gemini model settings responded successfully.", True)
        if spec.provider == "elevenlabs":
            await self._validate_elevenlabs(config, secrets)
            return ("ElevenLabs voice settings responded successfully.", True)
        if spec.provider == "vapi":
            await self._validate_vapi(config, secrets)
            return ("Vapi assistant settings responded successfully.", True)
        raise ProviderSettingsValidationError(f"Unsupported provider {spec.provider}")

    async def _validate_gemini(self, config: dict[str, Any], secrets: dict[str, str]) -> None:
        url = (
            f"https://generativelanguage.googleapis.com/{config['api_version']}/models/{config['model_id']}"
        )
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, params={"key": secrets["api_key"]})
        except httpx.RequestError as exc:
            raise ProviderSettingsValidationError(f"Gemini validation network error: {exc}") from exc
        if response.status_code >= 400:
            raise ProviderSettingsValidationError(f"Gemini validation failed with HTTP {response.status_code}.")

    async def _validate_elevenlabs(self, config: dict[str, Any], secrets: dict[str, str]) -> None:
        client = ElevenLabsClient(
            api_key=secrets["api_key"],
            default_voice_id=config["voice_id"],
            enabled=True,
            config_source="provider_settings",
            timeout=10.0,
        )
        try:
            await client.validate_tts_contract()
        except EngineError as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            stage = detail.get("stage", "unknown")
            http_status = detail.get("http_status")
            message = f"ElevenLabs validation failed at stage={stage}"
            if http_status is not None:
                message += f" with HTTP {http_status}"
            body_preview = detail.get("body_preview")
            if body_preview:
                message += f": {body_preview}"
            raise ProviderSettingsValidationError(message) from exc

    async def _validate_vapi(self, config: dict[str, Any], secrets: dict[str, str]) -> None:
        base_url = str(config.get("base_url") or "https://api.vapi.ai")
        try:
            async with httpx.AsyncClient(
                base_url=base_url,
                timeout=10.0,
                headers={"Authorization": f"Bearer {secrets['api_key']}"},
            ) as client:
                response = await client.get(f"/assistant/{config['assistant_id']}")
        except httpx.RequestError as exc:
            raise ProviderSettingsValidationError(f"Vapi validation network error: {exc}") from exc
        if response.status_code >= 400:
            raise ProviderSettingsValidationError(f"Vapi validation failed with HTTP {response.status_code}.")

    def _normalize_config(self, spec: ProviderSpec, config: dict[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = dict(spec.config_defaults)
        for key in spec.config_fields:
            value = config.get(key, spec.config_defaults.get(key))
            if isinstance(spec.config_defaults.get(key), bool):
                normalized[key] = bool(value)
            elif value is None:
                normalized[key] = ""
            elif isinstance(value, str):
                normalized[key] = value.strip()
            else:
                normalized[key] = value
        return normalized

    def _missing_required_fields(
        self,
        spec: ProviderSpec,
        config: dict[str, Any],
        secrets: dict[str, str],
    ) -> list[str]:
        missing: list[str] = []
        for field in spec.required_for_validation:
            if field in spec.secret_fields:
                if not secrets.get(field):
                    missing.append(field)
                continue
            value = config.get(field)
            if isinstance(value, bool):
                if not value:
                    missing.append(field)
            elif value is None or (isinstance(value, str) and not value.strip()):
                missing.append(field)
        return missing

    def _get_spec(self, provider: str) -> ProviderSpec:
        cleaned = provider.strip().lower()
        spec = PROVIDER_SPECS.get(cleaned)
        if spec is None:
            raise NotFoundError(f"Provider {provider} is not supported")
        return spec

    def _encrypt_secrets(self, payload: dict[str, str]) -> str:
        key = _build_encryption_key()
        token = Fernet(key).encrypt(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        return token.decode("utf-8")

    def _decrypt_secrets(self, encrypted: str) -> dict[str, str]:
        if not encrypted:
            return {}
        key = _build_encryption_key()
        try:
            raw = Fernet(key).decrypt(encrypted.encode("utf-8"))
        except InvalidToken as exc:
            raise ProviderSettingsEncryptionError(
                "Provider settings could not be decrypted with the current secret."
            ) from exc
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ProviderSettingsEncryptionError("Encrypted provider settings payload is malformed.")
        return {str(key): str(value) for key, value in data.items() if value is not None}


def _build_encryption_key() -> bytes:
    secret = (settings.provider_settings_secret or settings.admin_auth_secret or "").strip()
    if not secret:
        raise ProviderSettingsEncryptionError(
            "Provider settings encryption secret is not configured. Set PROVIDER_SETTINGS_SECRET or ADMIN_AUTH_SECRET."
        )
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _mask_secret(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    if len(value) <= 4:
        return "*" * len(value)
    if len(value) <= 8:
        return f"{value[:1]}***{value[-1:]}"
    return f"{value[:2]}***{value[-2:]}"
