"""Smoke tests for environment variable configuration parsing.

These tests verify that:
- Settings properties compute correctly from their component fields
- Type coercions work (int, bool, float parsed from explicit values)
- Canonical env var names map to the expected Settings attributes

Tests use an IsolatedSettings subclass that does NOT read the .env file,
so they are independent of any local credentials.
"""
from __future__ import annotations
import pytest
from pydantic_settings import SettingsConfigDict

from app.core.config import Settings


class IsolatedSettings(Settings):
    """Settings subclass that ignores .env files for fully isolated unit tests."""

    model_config = SettingsConfigDict(
        env_file=None,
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BASE = dict(
    database_url="postgresql+asyncpg://u:p@localhost/test",
    redis_url="redis://localhost:6379/0",
)


def make(**kwargs) -> IsolatedSettings:
    return IsolatedSettings(**{**BASE, **kwargs})


# ---------------------------------------------------------------------------
# mango_configured
# ---------------------------------------------------------------------------

class TestMangoConfigured:
    def test_true_when_both_key_and_salt_present(self):
        s = make(mango_api_key="key123", mango_api_salt="salt456")
        assert s.mango_configured is True

    def test_false_when_key_missing(self):
        s = make(mango_api_key="", mango_api_salt="salt456")
        assert s.mango_configured is False

    def test_false_when_salt_missing(self):
        s = make(mango_api_key="key123", mango_api_salt="")
        assert s.mango_configured is False

    def test_false_when_both_missing(self):
        s = make(mango_api_key="", mango_api_salt="")
        assert s.mango_configured is False


# ---------------------------------------------------------------------------
# vapi_configured
# ---------------------------------------------------------------------------

class TestVapiConfigured:
    def test_true_when_all_three_present(self):
        s = make(
            vapi_api_key="key",
            vapi_assistant_id="asst",
            vapi_phone_number_id="phone",
        )
        assert s.vapi_configured is True

    def test_false_when_api_key_missing(self):
        s = make(vapi_api_key="", vapi_assistant_id="asst", vapi_phone_number_id="phone")
        assert s.vapi_configured is False

    def test_false_when_assistant_id_missing(self):
        s = make(vapi_api_key="key", vapi_assistant_id="", vapi_phone_number_id="phone")
        assert s.vapi_configured is False

    def test_false_when_phone_number_id_missing(self):
        s = make(vapi_api_key="key", vapi_assistant_id="asst", vapi_phone_number_id="")
        assert s.vapi_configured is False

    def test_false_when_all_missing(self):
        s = make(vapi_api_key="", vapi_assistant_id="", vapi_phone_number_id="")
        assert s.vapi_configured is False


# ---------------------------------------------------------------------------
# gemini_configured
# ---------------------------------------------------------------------------

class TestGeminiConfigured:
    def test_true_when_api_key_present(self):
        s = make(gemini_api_key="AIzaSyXXX")
        assert s.gemini_configured is True

    def test_false_when_api_key_empty(self):
        s = make(gemini_api_key="")
        assert s.gemini_configured is False


# ---------------------------------------------------------------------------
# elevenlabs_configured
# ---------------------------------------------------------------------------

class TestElevenLabsConfigured:
    def test_true_when_enabled_and_all_fields_present(self):
        s = make(
            elevenlabs_enabled=True,
            elevenlabs_api_key="el_key",
            elevenlabs_voice_id="voice_123",
        )
        assert s.elevenlabs_configured is True

    def test_false_when_enabled_false(self):
        s = make(
            elevenlabs_enabled=False,
            elevenlabs_api_key="el_key",
            elevenlabs_voice_id="voice_123",
        )
        assert s.elevenlabs_configured is False

    def test_false_when_api_key_missing(self):
        s = make(elevenlabs_enabled=True, elevenlabs_api_key="", elevenlabs_voice_id="voice_123")
        assert s.elevenlabs_configured is False

    def test_false_when_voice_id_missing(self):
        s = make(elevenlabs_enabled=True, elevenlabs_api_key="el_key", elevenlabs_voice_id="")
        assert s.elevenlabs_configured is False


# ---------------------------------------------------------------------------
# admin_auth_configured
# ---------------------------------------------------------------------------

class TestAdminAuthConfigured:
    def test_true_when_all_three_set(self):
        s = make(
            admin_email="admin@example.com",
            admin_password="secret",
            admin_auth_secret="jwt_secret",
        )
        assert s.admin_auth_configured is True

    def test_false_when_email_missing(self):
        s = make(admin_email="", admin_password="secret", admin_auth_secret="jwt_secret")
        assert s.admin_auth_configured is False

    def test_false_when_password_missing(self):
        s = make(admin_email="admin@example.com", admin_password="", admin_auth_secret="jwt_secret")
        assert s.admin_auth_configured is False

    def test_false_when_secret_missing(self):
        s = make(admin_email="admin@example.com", admin_password="secret", admin_auth_secret="")
        assert s.admin_auth_configured is False


# ---------------------------------------------------------------------------
# is_production / is_testing
# ---------------------------------------------------------------------------

class TestEnvironmentFlags:
    def test_is_production_true(self):
        s = make(environment="production")
        assert s.is_production is True
        assert s.is_testing is False

    def test_is_testing_true(self):
        s = make(environment="testing")
        assert s.is_testing is True
        assert s.is_production is False

    def test_development_neither(self):
        s = make(environment="development")
        assert s.is_production is False
        assert s.is_testing is False


# ---------------------------------------------------------------------------
# Celery URL delegation
# ---------------------------------------------------------------------------

class TestCeleryUrls:
    def test_broker_url_equals_redis_url(self):
        s = make(redis_url="redis://localhost:6380/1")
        assert s.celery_broker_url == "redis://localhost:6380/1"

    def test_result_backend_equals_redis_url(self):
        s = make(redis_url="redis://localhost:6380/2")
        assert s.celery_result_backend == "redis://localhost:6380/2"


class TestRenderDatabaseUrlNormalization:
    def test_normalizes_render_postgres_scheme_to_asyncpg(self):
        s = make(database_url="postgres://user:pass@render-host:5432/app_db")
        assert s.database_url == "postgresql+asyncpg://user:pass@render-host:5432/app_db"

    def test_normalizes_plain_postgresql_scheme_to_asyncpg(self):
        s = make(database_url="postgresql://user:pass@render-host:5432/app_db")
        assert s.database_url == "postgresql+asyncpg://user:pass@render-host:5432/app_db"

    def test_keeps_asyncpg_scheme_unchanged(self):
        s = make(database_url="postgresql+asyncpg://user:pass@render-host:5432/app_db")
        assert s.database_url == "postgresql+asyncpg://user:pass@render-host:5432/app_db"

    def test_uses_render_database_url_when_production_database_url_is_local(self):
        s = make(
            environment="production",
            database_url="postgresql+asyncpg://amo_user:amo_pass@127.0.0.1:5433/amo_crm",
            render_database_url="postgresql://render_user:render_pass@render-db:5432/app_db",
        )
        assert s.database_url == "postgresql+asyncpg://render_user:render_pass@render-db:5432/app_db"

    def test_prefers_raw_process_database_url_when_field_falls_back_to_localhost(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://render_user:render_pass@render-db:5432/app_db")
        s = make(
            environment="production",
            database_url="postgresql+asyncpg://amo_user:amo_pass@127.0.0.1:5433/amo_crm",
            render_database_url="",
        )
        assert s.database_url == "postgresql+asyncpg://render_user:render_pass@render-db:5432/app_db"

    def test_uses_raw_render_database_url_when_settings_field_is_empty(self, monkeypatch):
        monkeypatch.setenv("RENDER_DATABASE_URL", "postgresql://render_user:render_pass@render-db:5432/app_db")
        s = make(
            environment="production",
            database_url="postgresql+asyncpg://amo_user:amo_pass@127.0.0.1:5433/amo_crm",
            render_database_url="",
        )
        assert s.database_url == "postgresql+asyncpg://render_user:render_pass@render-db:5432/app_db"


class TestGeminiLiveModelNormalization:
    def test_rewrites_deprecated_gemini_2_live_model_to_supported_replacement(self):
        s = make(gemini_model_id="gemini-2.0-flash-live-001")
        assert s.gemini_model_id == "gemini-2.5-flash-native-audio-preview-12-2025"

    def test_rewrites_legacy_gemini_25_preview_alias_to_supported_replacement(self):
        s = make(gemini_model_id="gemini-live-2.5-flash-preview")
        assert s.gemini_model_id == "gemini-2.5-flash-native-audio-preview-12-2025"

    def test_keeps_explicit_supported_live_model_unchanged(self):
        s = make(gemini_model_id="gemini-2.5-flash-native-audio-preview-12-2025")
        assert s.gemini_model_id == "gemini-2.5-flash-native-audio-preview-12-2025"

    def test_keeps_explicit_remote_database_url_even_when_render_database_url_exists(self):
        s = make(
            environment="production",
            database_url="postgresql+asyncpg://user:pass@remote-db:5432/app_db",
            render_database_url="postgresql://render_user:render_pass@render-db:5432/app_db",
        )
        assert s.database_url == "postgresql+asyncpg://user:pass@remote-db:5432/app_db"

    def test_uses_render_redis_url_when_production_redis_url_is_local(self):
        s = make(
            environment="production",
            redis_url="redis://127.0.0.1:6379/0",
            render_redis_url="redis://render-redis:6379/0",
        )
        assert s.redis_url == "redis://render-redis:6379/0"


class TestAdminCorsOrigins:
    def test_parses_comma_separated_admin_cors_origins(self):
        s = make(
            admin_cors_origins="https://admin-one.onrender.com, https://admin-two.onrender.com"
        )
        assert s.admin_cors_origins_list == [
            "https://admin-one.onrender.com",
            "https://admin-two.onrender.com",
        ]


# ---------------------------------------------------------------------------
# Type coercions from field values
# ---------------------------------------------------------------------------

class TestTypeCoercions:
    def test_int_field_parsed(self):
        s = make(rate_limit_global_per_ip_per_minute=120)
        assert s.rate_limit_global_per_ip_per_minute == 120
        assert isinstance(s.rate_limit_global_per_ip_per_minute, int)

    def test_bool_field_parsed(self):
        s = make(rate_limit_enabled=False)
        assert s.rate_limit_enabled is False

    def test_float_field_parsed(self):
        s = make(gemini_setup_timeout=10.0)
        assert s.gemini_setup_timeout == 10.0
        assert isinstance(s.gemini_setup_timeout, float)

    def test_default_rate_limit_enabled_is_true(self):
        s = make()
        assert s.rate_limit_enabled is True

    def test_development_environment_explicit(self):
        # conftest sets ENVIRONMENT=testing as a process env var, so we must
        # pass environment explicitly to test the "development" value.
        s = make(environment="development")
        assert s.environment == "development"


# ---------------------------------------------------------------------------
# summary_llm_enabled
# ---------------------------------------------------------------------------

class TestSummaryLlmEnabled:
    def test_true_when_gemini_provider_and_key_present(self):
        s = make(summary_llm_provider="gemini", gemini_api_key="AIza")
        assert s.summary_llm_enabled is True

    def test_false_when_provider_empty(self):
        s = make(summary_llm_provider="", gemini_api_key="AIza")
        assert s.summary_llm_enabled is False

    def test_false_when_gemini_key_empty(self):
        s = make(summary_llm_provider="gemini", gemini_api_key="")
        assert s.summary_llm_enabled is False
