from __future__ import annotations

import os
from typing import Literal
from urllib.parse import urlparse

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _env_truthy(value: str) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _normalize_database_url(database_url: str) -> str:
    value = (database_url or "").strip()
    if not value:
        return value
    if value.startswith("postgres://"):
        return "postgresql+asyncpg://" + value[len("postgres://"):]
    if value.startswith("postgresql://") and "+asyncpg" not in value:
        return "postgresql+asyncpg://" + value[len("postgresql://"):]
    return value


def _is_local_database_url(database_url: str) -> bool:
    value = (database_url or "").strip()
    if not value:
        return True
    parsed = urlparse(value)
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return True
    return host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local")


def _is_public_http_url(url: str) -> bool:
    value = (url or "").strip()
    if not value:
        return False
    parsed = urlparse(value)
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return False
    if host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local"):
        return False
    return True


_DEPRECATED_GEMINI_LIVE_MODELS: dict[str, str] = {
    "gemini-2.0-flash-live-001": "gemini-2.5-flash-native-audio-preview-12-2025",
    "gemini-live-2.5-flash-preview": "gemini-2.5-flash-native-audio-preview-12-2025",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://amo_user:amo_pass@127.0.0.1:5433/amo_crm"

    # ── Redis ─────────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── Telegram Bot ──────────────────────────────────────────────────────────
    telegram_bot_token: str = ""

    # ── Backend URL (used by bot to call the API) ─────────────────────────────
    backend_url: str = "http://127.0.0.1:8000"
    # Render public service URL. When BACKEND_URL is left local/unset in a Render
    # environment, runtime can safely fall back to this value for readiness,
    # callbacks and webhook diagnostics.
    render_external_url: str = ""
    # Render-managed Postgres URL. This stays empty locally, but on Render we can
    # bind it from the managed database and use it as a guard against accidental
    # localhost DATABASE_URL overrides.
    render_database_url: str = ""
    # Optional Render-managed Redis URL for the same protection pattern.
    render_redis_url: str = ""
    # Comma-separated CORS origins for the admin panel or other browser clients.
    # Example:
    #   https://amo-crm-admin.onrender.com,https://staging-admin.example.com
    admin_cors_origins: str = ""

    # ── App ───────────────────────────────────────────────────────────────────
    environment: Literal["development", "production", "testing"] = "development"
    log_level: str = "INFO"
    log_format: Literal["console", "json"] = "console"

    # ── Phone normalization ───────────────────────────────────────────────────
    # ISO 3166-1 alpha-2 country code used as fallback when parsing local numbers
    default_phone_country: str = "RU"

    # ── Vapi ──────────────────────────────────────────────────────────────────
    # API key from https://app.vapi.ai → Account → API Keys
    vapi_api_key: str = ""
    # Assistant ID configured in Vapi dashboard with your AI prompt
    vapi_assistant_id: str = ""
    # Vapi-managed phone number ID (from Vapi dashboard → Phone Numbers)
    vapi_phone_number_id: str = ""
    # Public URL of this backend — Vapi POSTs events here
    # E.g. https://your-app.onrender.com  (no trailing slash)
    vapi_server_url: str = ""
    # HMAC-SHA256 secret for validating Vapi webhook signatures
    # Set this to the value in Vapi assistant → Server → Secret
    vapi_webhook_secret: str = ""
    # Base URL for Vapi REST API (no trailing slash)
    vapi_base_url: str = "https://api.vapi.ai"

    # ── Telephony provider selection ──────────────────────────────────────────
    # Which telephony provider to use for Direct mode and warm transfer.
    # "auto"   — pick best available (first registered non-stub provider)
    # "mango"  — force Mango (ProviderNotFoundError if not configured)
    # "twilio" — force Twilio (ProviderNotFoundError if not configured, skeletal)
    # "stub"   — always use Stub (dev/test)
    telephony_provider: str = "auto"

    # ── Mango Office Telephony ────────────────────────────────────────────────
    # Mango VPBX API credentials (https://app.mango-office.ru/vpbx/)
    mango_api_key: str = ""
    mango_api_salt: str = ""
    mango_api_base_url: str = "https://app.mango-office.ru/vpbx"
    mango_sip_login: str = ""
    mango_sip_password: str = ""
    mango_sip_server: str = ""
    # Extension number for Click-to-Call origination (Direct mode)
    mango_from_ext: str = ""
    # Optional HMAC secret for Mango webhook signature verification.
    # Header expected: X-Mango-Signature: sha256=<hex> (or plain hex)
    mango_webhook_secret: str = ""
    # Fallback guard when Mango native signature is unavailable.
    # Header expected: X-Mango-Webhook-Secret: <value>
    mango_webhook_shared_secret: str = ""
    # Optional source IP allowlist for Mango webhook endpoint.
    # Comma-separated CIDR/IP values, e.g. "1.2.3.0/24,5.6.7.8"
    mango_webhook_ip_allowlist: str = ""
    # Timeout for waiting leg ANSWERED via webhook-first/poll fallback.
    mango_answer_wait_timeout_seconds: int = 30
    # Timeout for bridge confirmation after transfer command accepted.
    mango_bridge_confirm_timeout_seconds: int = 12
    # Timeout for whisper completion confirmation.
    mango_whisper_confirm_timeout_seconds: int = 15

    # ── Media gateway (Mango Direct voice RTP plane) ─────────────────────────
    media_gateway_enabled: bool = False
    # Supported values: "disabled" | "mock" | "scaffold" | "esl_rtp"
    # - disabled: media gateway integration path off
    # - mock: in-memory media bus for architecture tests only
    # - scaffold: explicit non-production path (attach fails intentionally)
    # - esl_rtp: baseline ESL command/event loop + RTP ingest/inject
    media_gateway_mode: str = "disabled"
    # Current provider choice: FreeSWITCH (recommended for RTP/media plane).
    media_gateway_provider: str = "freeswitch"
    freeswitch_esl_host: str = "127.0.0.1"
    freeswitch_esl_port: int = 8021
    freeswitch_esl_password: str = "ClueCon"
    freeswitch_sip_profile: str = "external"
    freeswitch_sip_domain: str = "localhost"
    freeswitch_sip_ip: str = ""
    freeswitch_rtp_ip: str = "127.0.0.1"
    freeswitch_ws_url: str = ""
    freeswitch_wss_url: str = ""
    freeswitch_rtp_port_start: int = 16384
    freeswitch_rtp_port_end: int = 32768
    freeswitch_session_timeout_seconds: int = 120
    freeswitch_rtp_payload_type: int = 96
    # FreeSWITCH ESL command templates.
    # {uuid} -> channel UUID, {rtp_ip}/{rtp_port} -> backend RTP bind endpoint.
    freeswitch_attach_command_template: str = "uuid_media_reneg {uuid} ={rtp_ip}:{rtp_port}"
    freeswitch_hangup_command_template: str = "uuid_kill {uuid}"
    freeswitch_esl_events: str = "CHANNEL_HANGUP_COMPLETE CUSTOM HEARTBEAT"
    freeswitch_esl_connect_timeout_seconds: float = 5.0
    freeswitch_esl_reconnect_enabled: bool = True
    freeswitch_esl_reconnect_initial_delay_seconds: float = 0.5
    freeswitch_esl_reconnect_max_delay_seconds: float = 5.0
    # 0 means unlimited reconnect attempts.
    freeswitch_esl_reconnect_max_attempts: int = 0
    # RTP/codec/runtime controls for first real media bridge.
    # Supported codec names: "pcm16", "pcmu"
    freeswitch_rtp_inbound_codec: str = "pcm16"
    freeswitch_rtp_outbound_codec: str = "pcm16"
    freeswitch_rtp_sample_rate_hz: int = 16000
    freeswitch_rtp_frame_bytes: int = 640
    freeswitch_rtp_inbound_timeout_seconds: int = 15
    # Buffer a short outbound RTP burst until the remote endpoint is known.
    freeswitch_rtp_outbound_buffer_max_frames: int = 50
    freeswitch_event_queue_max: int = 512

    @property
    def mango_configured(self) -> bool:
        """True when Mango API credentials are present."""
        return bool(self.mango_api_key and self.mango_api_salt)

    # ── Security ──────────────────────────────────────────────────────────────
    # Shared API key for bot → backend communication.
    # Set a non-empty value to require X-API-Key header on mutating endpoints.
    api_key: str = ""
    # Minimal browser admin auth.
    # This is intentionally env-backed for the first internal admin panel iteration.
    admin_email: str = ""
    admin_password: str = ""
    admin_auth_secret: str = ""
    provider_settings_secret: str = ""
    admin_token_ttl_seconds: int = 60 * 60 * 8

    # ── Metrics / observability ──────────────────────────────────────────────
    # Enables /metrics endpoint and Prometheus telemetry exporters.
    metrics_enabled: bool = True
    # Optional local-only WAV dumps for debugging audio corruption in browser/direct runtime.
    audio_debug_dump_enabled: bool = False
    audio_debug_dump_dir: str = "/tmp/amo_crm_audio_debug"

    # ── Rate limiting ─────────────────────────────────────────────────────────
    # Global enable/disable for all rate limiting checks (disabled during testing)
    rate_limit_enabled: bool = True
    # Max requests from a single IP per minute
    rate_limit_global_per_ip_per_minute: int = 60
    # Max outbound call requests per unique API key per minute
    rate_limit_calls_per_minute: int = 10
    # Max steering instructions per call per minute
    rate_limit_steer_per_call_per_minute: int = 20
    # Max transfer attempts per call (regardless of time window)
    rate_limit_transfer_per_call: int = 3
    # Seconds before a phone can initiate another transfer
    rate_limit_transfer_cooldown_seconds: int = 60
    # Max calls to a single phone number per calendar day
    rate_limit_calls_per_phone_per_day: int = 5
    # Max concurrent active calls across all API keys
    rate_limit_max_concurrent_calls: int = 50
    # Seconds before a phone can be called again after a failed attempt
    rate_limit_phone_repeat_cooldown_seconds: int = 300

    # ── Quiet hours (calling window) ──────────────────────────────────────────
    # Calls outside [calling_hour_start, calling_hour_end) local time are rejected.
    # Set enforce_quiet_hours=False to disable (e.g. in testing/development).
    calling_hour_start: int = 9    # 09:00 inclusive
    calling_hour_end: int = 21     # 21:00 exclusive
    calling_timezone: str = "Europe/Moscow"
    enforce_quiet_hours: bool = False

    # ── Transfer settings ─────────────────────────────────────────────────────
    # Seconds to wait for engine.initiate_manager_call (dial + ring timeout)
    transfer_manager_answer_timeout: int = 30
    # Seconds to wait for engine.play_whisper (briefing audio)
    transfer_briefing_timeout: int = 15
    # Seconds to wait for engine.bridge_calls (bridge establishment)
    transfer_bridge_timeout: int = 10
    # Max number of managers to try per transfer attempt (0 = unlimited)
    transfer_max_manager_attempts: int = 3
    # Seconds before a marked-unavailable manager becomes available again
    # A Celery beat task will restore availability after this cooldown
    transfer_manager_cooldown_seconds: int = 300  # 5 minutes
    # Durable availability reconciliation (restores managers after process restarts)
    transfer_manager_restore_enabled: bool = True
    transfer_manager_restore_interval_seconds: int = 30

    # ── Gemini Live (Direct mode) ──────────────────────────────────────────────
    # API key from Google AI Studio (aistudio.google.com) or Vertex AI
    gemini_api_key: str = ""
    # Model ID — see https://ai.google.dev/api/multimodal-live for available models
    # Current Live API default replacement for deprecated Gemini 2.0 Live model.
    # Phase 2: "gemini-2.5-flash-preview-native-audio-dialog"
    gemini_model_id: str = "gemini-2.5-flash-native-audio-preview-12-2025"
    # Model used for tts_primary strategy (audio-in → TEXT-out → ElevenLabs TTS).
    # Empty string = use the same model as gemini_model_id (primary model).
    gemini_tts_model_id: str = ""
    # API version segment for the WebSocket endpoint URL
    gemini_api_version: str = "v1beta"
    # API version for the TTS model. Empty string = use same as gemini_api_version.
    gemini_tts_api_version: str = ""
    # System prompt injected into every Direct mode session
    gemini_system_prompt: str = (
        "Ты — AI ассистент по продажам. "
        "Отвечай по-русски. Будь кратким и вежливым."
    )
    # Seconds to wait for setupComplete from Gemini before raising TimeoutError
    gemini_setup_timeout: float = 5.0
    # Hard cap on concurrent in-process Direct sessions (single-process guard)
    direct_max_sessions: int = 10
    # Explicit production voice strategy for Direct mode.
    # - disabled: Direct voice path must not start
    # - gemini_primary: Gemini native audio is the primary voice path
    # - tts_primary: Gemini text + ElevenLabs TTS is the primary voice path
    # - experimental_hybrid: mixed path for controlled experiments only
    direct_voice_strategy: Literal[
        "disabled",
        "gemini_primary",
        "tts_primary",
        "experimental_hybrid",
    ] = "disabled"
    # Allow runtime downgrade from gemini_primary to ElevenLabs TTS when available.
    direct_voice_allow_tts_fallback: bool = True
    # Enable Gemini AUDIO output modality (requires SIP audio bridge for input)
    # When False (default): TEXT modality, Gemini returns text, ElevenLabs does TTS
    # When True: TEXT+AUDIO modality, Gemini returns both text and audio PCM
    gemini_audio_output_enabled: bool = False
    # Enable sending inbound telephony audio to Gemini.
    gemini_audio_input_enabled: bool = True
    # Start the conversation immediately after the call is answered.
    direct_initial_greeting_enabled: bool = True
    direct_initial_greeting_text: str = (
        "Здравствуйте! Это AI-ассистент. Чем могу помочь?"
    )
    # Fail the call if Gemini does not answer after an audio chunk or steering turn.
    direct_model_response_timeout_seconds: float = 8.0

    # ── Summary / Whisper generation ──────────────────────────────────────────
    # "" = rule-based only (fast, synchronous, zero dependencies)
    # "gemini" = use gemini_api_key for LLM-assisted summarization
    # LLM path is always best-effort — rule-based fallback is always available
    summary_llm_provider: str = ""

    @property
    def summary_llm_enabled(self) -> bool:
        """True when LLM summarization is configured and Gemini API key present."""
        return bool(self.summary_llm_provider == "gemini" and self.gemini_api_key)

    # ── ElevenLabs TTS (optional, Direct mode voice) ──────────────────────────
    # API key from elevenlabs.io
    elevenlabs_api_key: str = ""
    # Voice ID from ElevenLabs dashboard — do NOT hardcode a specific voice
    elevenlabs_voice_id: str = ""
    # Set to true to activate ElevenLabs TTS (requires api_key + voice_id)
    elevenlabs_enabled: bool = False

    @property
    def vapi_configured(self) -> bool:
        """True when the minimum Vapi credentials are present."""
        return bool(
            self.vapi_api_key
            and self.vapi_assistant_id
            and self.vapi_phone_number_id
        )

    @property
    def gemini_configured(self) -> bool:
        """True when Gemini API key is present — enables Direct mode."""
        return bool(self.gemini_api_key)

    @property
    def elevenlabs_configured(self) -> bool:
        """True when ElevenLabs is fully configured and enabled."""
        return bool(
            self.elevenlabs_enabled
            and self.elevenlabs_api_key
            and self.elevenlabs_voice_id
        )

    @property
    def admin_auth_configured(self) -> bool:
        """True when minimal admin auth is configured."""
        return bool(
            self.admin_email
            and self.admin_password
            and self.admin_auth_secret
        )

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_testing(self) -> bool:
        return self.environment == "testing"

    @property
    def celery_broker_url(self) -> str:
        return self.redis_url

    @property
    def celery_result_backend(self) -> str:
        return self.redis_url

    @property
    def admin_cors_origins_list(self) -> list[str]:
        return [item.strip() for item in self.admin_cors_origins.split(",") if item.strip()]

    @property
    def effective_backend_url(self) -> str:
        configured = (self.backend_url or "").strip().rstrip("/")
        render_url = (self.render_external_url or "").strip().rstrip("/")
        if _is_public_http_url(configured):
            return configured
        if _is_public_http_url(render_url):
            return render_url
        return configured or render_url

    @model_validator(mode="after")
    def normalize_render_urls(self) -> "Settings":
        raw_database_url = _normalize_database_url(os.environ.get("DATABASE_URL", ""))
        raw_render_database_url = _normalize_database_url(os.environ.get("RENDER_DATABASE_URL", ""))
        raw_render_redis_url = (os.environ.get("RENDER_REDIS_URL", "") or "").strip()
        raw_admin_email = (os.environ.get("ADMIN_EMAIL", "") or "").strip()
        raw_admin_password = (os.environ.get("ADMIN_PASSWORD", "") or "").strip()
        raw_admin_auth_secret = (os.environ.get("ADMIN_AUTH_SECRET", "") or "").strip()
        raw_admin_cors_origins = (os.environ.get("ADMIN_CORS_ORIGINS", "") or "").strip()
        raw_direct_voice_strategy = (os.environ.get("DIRECT_VOICE_STRATEGY", "") or "").strip()
        raw_telephony_provider = (os.environ.get("TELEPHONY_PROVIDER", "") or "").strip()
        raw_media_gateway_enabled = (os.environ.get("MEDIA_GATEWAY_ENABLED", "") or "").strip()
        raw_media_gateway_mode = (os.environ.get("MEDIA_GATEWAY_MODE", "") or "").strip()
        raw_media_gateway_provider = (os.environ.get("MEDIA_GATEWAY_PROVIDER", "") or "").strip()
        raw_mango_from_ext = (os.environ.get("MANGO_FROM_EXT", "") or "").strip()
        raw_gemini_audio_output_enabled = (os.environ.get("GEMINI_AUDIO_OUTPUT_ENABLED", "") or "").strip()
        raw_direct_voice_allow_tts_fallback = (os.environ.get("DIRECT_VOICE_ALLOW_TTS_FALLBACK", "") or "").strip()
        raw_direct_initial_greeting_enabled = (os.environ.get("DIRECT_INITIAL_GREETING_ENABLED", "") or "").strip()
        raw_elevenlabs_enabled = (os.environ.get("ELEVENLABS_ENABLED", "") or "").strip()
        raw_elevenlabs_api_key = (os.environ.get("ELEVENLABS_API_KEY", "") or "").strip()
        raw_elevenlabs_voice_id = (os.environ.get("ELEVENLABS_VOICE_ID", "") or "").strip()
        raw_freeswitch_esl_host = (os.environ.get("FREESWITCH_ESL_HOST", "") or "").strip()
        raw_freeswitch_esl_password = (os.environ.get("FREESWITCH_ESL_PASSWORD", "") or "").strip()
        raw_freeswitch_rtp_ip = (os.environ.get("FREESWITCH_RTP_IP", "") or "").strip()
        self.database_url = _normalize_database_url(self.database_url)
        self.render_database_url = _normalize_database_url(self.render_database_url)
        if raw_database_url and _is_local_database_url(self.database_url):
            self.database_url = raw_database_url
        if raw_render_database_url and not self.render_database_url:
            self.render_database_url = raw_render_database_url
        render_like_runtime = self.is_production or _is_public_http_url(self.render_external_url)
        if render_like_runtime and self.render_database_url and _is_local_database_url(self.database_url):
            self.database_url = self.render_database_url
        if raw_render_redis_url and not self.render_redis_url:
            self.render_redis_url = raw_render_redis_url
        if render_like_runtime and self.render_redis_url:
            redis_value = (self.redis_url or "").strip().lower()
            if not redis_value or redis_value.startswith("redis://localhost") or redis_value.startswith("redis://127.0.0.1"):
                self.redis_url = self.render_redis_url
        if raw_admin_email and not (self.admin_email or "").strip():
            self.admin_email = raw_admin_email
        if raw_admin_password and not (self.admin_password or "").strip():
            self.admin_password = raw_admin_password
        if raw_admin_auth_secret and not (self.admin_auth_secret or "").strip():
            self.admin_auth_secret = raw_admin_auth_secret
        if raw_admin_cors_origins and not (self.admin_cors_origins or "").strip():
            self.admin_cors_origins = raw_admin_cors_origins
        if raw_direct_voice_strategy and (self.direct_voice_strategy or "").strip() == "disabled":
            self.direct_voice_strategy = raw_direct_voice_strategy  # type: ignore[assignment]
        if raw_telephony_provider and (self.telephony_provider or "").strip() == "auto":
            self.telephony_provider = raw_telephony_provider
        if raw_media_gateway_enabled and not self.media_gateway_enabled:
            self.media_gateway_enabled = _env_truthy(raw_media_gateway_enabled)
        if raw_media_gateway_mode and (self.media_gateway_mode or "").strip() == "disabled":
            self.media_gateway_mode = raw_media_gateway_mode
        if raw_media_gateway_provider and not (self.media_gateway_provider or "").strip():
            self.media_gateway_provider = raw_media_gateway_provider
        if raw_mango_from_ext and not (self.mango_from_ext or "").strip():
            self.mango_from_ext = raw_mango_from_ext
        if raw_gemini_audio_output_enabled and not self.gemini_audio_output_enabled:
            self.gemini_audio_output_enabled = _env_truthy(raw_gemini_audio_output_enabled)
        if raw_direct_voice_allow_tts_fallback:
            self.direct_voice_allow_tts_fallback = _env_truthy(raw_direct_voice_allow_tts_fallback)
        if raw_direct_initial_greeting_enabled and not self.direct_initial_greeting_enabled:
            self.direct_initial_greeting_enabled = _env_truthy(raw_direct_initial_greeting_enabled)
        if raw_elevenlabs_enabled and not self.elevenlabs_enabled:
            self.elevenlabs_enabled = _env_truthy(raw_elevenlabs_enabled)
        if raw_elevenlabs_api_key and not (self.elevenlabs_api_key or "").strip():
            self.elevenlabs_api_key = raw_elevenlabs_api_key
        if raw_elevenlabs_voice_id and not (self.elevenlabs_voice_id or "").strip():
            self.elevenlabs_voice_id = raw_elevenlabs_voice_id
        if raw_freeswitch_esl_host and (self.freeswitch_esl_host or "").strip() in {"", "127.0.0.1", "localhost"}:
            self.freeswitch_esl_host = raw_freeswitch_esl_host
        if raw_freeswitch_esl_password and (self.freeswitch_esl_password or "").strip() == "ClueCon":
            self.freeswitch_esl_password = raw_freeswitch_esl_password
        if raw_freeswitch_rtp_ip and (self.freeswitch_rtp_ip or "").strip() in {"", "127.0.0.1", "localhost"}:
            self.freeswitch_rtp_ip = raw_freeswitch_rtp_ip
        replacement_model = _DEPRECATED_GEMINI_LIVE_MODELS.get((self.gemini_model_id or "").strip())
        if replacement_model:
            self.gemini_model_id = replacement_model
        return self


# Single shared instance — import this throughout the app
settings = Settings()
