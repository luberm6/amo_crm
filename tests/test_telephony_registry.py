"""
Tests for TelephonyProviderRegistry, ProviderCapabilities, and adapter declarations.

Coverage:
  1.  registry.register + registry.get → returns adapter instance
  2.  registry.get unknown provider → ProviderNotFoundError
  3.  registry.list_providers → returns registered names
  4.  registry.get_capabilities → returns ProviderCapabilities
  5.  StubTelephonyAdapter.capabilities → supports_audio_stream True
  6.  MangoTelephonyAdapter.capabilities → supports_audio_stream False
  7.  TwilioTelephonyAdapter.capabilities → provider_name "twilio"
  8.  ProviderCapabilities.check("audio_stream") on Mango → UnsupportedOperationError
  9.  build_default_registry with no Mango creds → "stub" registered, "mango" absent
  10. registry.resolve("auto") with no production provider → returns Stub (dev mode)
  11. registry.resolve("auto") with Mango registered → returns Mango
  12. registry.resolve("stub") always → returns Stub regardless of registered providers
  13. registry.resolve("auto") in production with no real provider → EngineError
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.core.config import settings
from app.core.exceptions import EngineError
from app.integrations.telephony.capabilities import (
    ProviderCapabilities,
    UnsupportedOperationError,
)
from app.integrations.telephony.mango import MangoTelephonyAdapter
from app.integrations.telephony.registry import (
    ProviderNotFoundError,
    TelephonyProviderRegistry,
    build_default_registry,
)
from app.integrations.telephony.stub import StubTelephonyAdapter
from app.integrations.telephony.twilio import TwilioTelephonyAdapter


# ── 1. register + get ─────────────────────────────────────────────────────────

def test_registry_register_and_get() -> None:
    """Registered factory produces an adapter instance."""
    registry = TelephonyProviderRegistry()
    registry.register("stub", StubTelephonyAdapter)
    adapter = registry.get("stub")
    assert isinstance(adapter, StubTelephonyAdapter)


# ── 2. unknown provider raises ────────────────────────────────────────────────

def test_registry_unknown_provider_raises() -> None:
    registry = TelephonyProviderRegistry()
    with pytest.raises(ProviderNotFoundError) as exc_info:
        registry.get("nonexistent")
    assert "nonexistent" in str(exc_info.value)
    assert exc_info.value.error_code == "telephony_provider_not_found"


# ── 3. list_providers ─────────────────────────────────────────────────────────

def test_registry_list_providers() -> None:
    registry = TelephonyProviderRegistry()
    registry.register("stub", StubTelephonyAdapter)
    registry.register("twilio", TwilioTelephonyAdapter)
    providers = registry.list_providers()
    assert "stub" in providers
    assert "twilio" in providers


# ── 4. get_capabilities ───────────────────────────────────────────────────────

def test_registry_get_capabilities() -> None:
    registry = TelephonyProviderRegistry()
    registry.register("stub", StubTelephonyAdapter)
    caps = registry.get_capabilities("stub")
    assert isinstance(caps, ProviderCapabilities)
    assert caps.provider_name == "stub"


# ── 5. Stub capabilities ──────────────────────────────────────────────────────

def test_stub_has_full_capabilities() -> None:
    adapter = StubTelephonyAdapter()
    caps = adapter.capabilities
    assert caps.provider_name == "stub"
    assert caps.supports_outbound_call is True
    assert caps.supports_audio_stream is True
    assert caps.supports_bridge is True
    assert caps.supports_whisper is True


# ── 6. Mango capabilities ─────────────────────────────────────────────────────

def test_mango_lacks_audio_stream() -> None:
    adapter = MangoTelephonyAdapter()
    caps = adapter.capabilities
    assert caps.provider_name == "mango"
    assert caps.supports_outbound_call is True
    assert caps.supports_audio_stream is False     # Phase 2 — SIP UA required
    assert caps.supports_bridge is True
    assert caps.supports_whisper is True


# ── 7. Twilio capabilities ────────────────────────────────────────────────────

def test_twilio_capabilities_declared() -> None:
    adapter = TwilioTelephonyAdapter()
    caps = adapter.capabilities
    assert caps.provider_name == "twilio"
    assert caps.supports_outbound_call is True    # Declared for when implemented
    assert caps.supports_audio_stream is True     # Declared for when implemented
    assert "Skeletal" in caps.notes


# ── 8. UnsupportedOperationError via check() ─────────────────────────────────

def test_unsupported_operation_error_raised() -> None:
    """
    capabilities.check() on an unsupported feature raises UnsupportedOperationError.
    This is the mechanism callers use before invoking optional telephony operations.
    """
    adapter = MangoTelephonyAdapter()
    caps = adapter.capabilities
    assert caps.supports_audio_stream is False

    with pytest.raises(UnsupportedOperationError) as exc_info:
        caps.check("audio_stream")

    assert "mango" in str(exc_info.value)
    assert "audio_stream" in str(exc_info.value)
    assert exc_info.value.error_code == "unsupported_operation"
    assert exc_info.value.status_code == 422


# ── 9. build_default_registry with no Mango creds ────────────────────────────

def test_build_default_registry_stub_always_available() -> None:
    """
    build_default_registry() with no Mango credentials → "stub" registered,
    "mango" absent, resolve("auto") returns Stub.
    """
    with patch.object(settings, "mango_api_key", ""), \
         patch.object(settings, "mango_api_salt", ""):
        registry = build_default_registry()

    providers = registry.list_providers()
    assert "stub" in providers
    assert "mango" not in providers


# ── 10. resolve("auto") with no production provider → Stub ────────────────────

def test_resolve_auto_no_production_provider_returns_stub() -> None:
    """auto selection falls back to Stub when no production provider is registered."""
    registry = TelephonyProviderRegistry()
    registry.register("stub", StubTelephonyAdapter)  # only stub

    adapter = registry.resolve("auto")
    assert isinstance(adapter, StubTelephonyAdapter)


# ── 11. resolve("auto") with Mango registered → Mango ────────────────────────

def test_resolve_auto_with_mango_returns_mango() -> None:
    """auto selection returns first non-stub provider with outbound support."""
    registry = TelephonyProviderRegistry()
    registry.register("stub", StubTelephonyAdapter)
    registry.register("mango", MangoTelephonyAdapter)

    adapter = registry.resolve("auto")
    assert isinstance(adapter, MangoTelephonyAdapter)


# ── 12. resolve("stub") always returns Stub ───────────────────────────────────

def test_resolve_stub_always_returns_stub() -> None:
    """resolve("stub") bypasses auto and always returns StubTelephonyAdapter."""
    registry = TelephonyProviderRegistry()
    registry.register("stub", StubTelephonyAdapter)
    registry.register("mango", MangoTelephonyAdapter)

    adapter = registry.resolve("stub")
    assert isinstance(adapter, StubTelephonyAdapter)


# ── 13. resolve("auto") in production with no real provider → EngineError ─────

def test_auto_select_raises_in_production_with_no_real_provider(monkeypatch) -> None:
    """Production mode without a real provider must raise EngineError — not fall back to stub."""
    monkeypatch.setattr(settings, "environment", "production")

    registry = TelephonyProviderRegistry()
    registry.register("stub", StubTelephonyAdapter)  # only stub, no Mango/Twilio

    with pytest.raises(EngineError, match="Production mode requires"):
        registry.resolve("auto")
