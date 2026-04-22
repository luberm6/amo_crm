"""
TelephonyProviderRegistry — factory and registry for telephony adapters.

Design:
  - One registry per process, built at startup via build_default_registry()
  - Adapters are registered by name with a factory callable (not a singleton instance)
  - get() returns a fresh adapter instance per call (stateful adapters need their own lifetime)
  - resolve() picks the right adapter based on a preference string ("auto", "mango", "stub", etc.)

Adding a new provider:
  1. Create your adapter in app/integrations/telephony/yourprovider.py
  2. Implement all AbstractTelephonyAdapter methods
  3. Implement the capabilities property
  4. Add config fields to app/core/config.py (YOUR_PROVIDER_API_KEY, etc.)
  5. Add a `yourprovider_configured` property to Settings
  6. Add registration in build_default_registry() below
  7. Test with TELEPHONY_PROVIDER=yourprovider in .env

Provider selection (TELEPHONY_PROVIDER env var):
  "auto"    — picks the first registered non-stub provider with supports_outbound_call;
              falls back to stub if none configured
  "stub"    — always stub (dev, tests, Redis-unavailable)
  "mango"   — force Mango; ProviderNotFoundError if not registered
  "twilio"  — force Twilio; ProviderNotFoundError if not registered
  <name>    — any registered provider name; ProviderNotFoundError if not registered
"""
from __future__ import annotations

from typing import Callable, Optional

from app.core.exceptions import EngineError
from app.core.logging import get_logger
from app.integrations.telephony.base import AbstractTelephonyAdapter
from app.integrations.telephony.capabilities import ProviderCapabilities

log = get_logger(__name__)


class ProviderNotFoundError(EngineError):
    """Raised when a requested telephony provider is not registered."""
    error_code = "telephony_provider_not_found"
    status_code = 503


class TelephonyProviderRegistry:
    """
    Registry that maps provider names to adapter factories.

    Thread-safety: all mutations happen at startup (build_default_registry).
    After that the registry is read-only — safe for concurrent coroutine access.
    """

    def __init__(self) -> None:
        self._factories: dict[str, Callable[[], AbstractTelephonyAdapter]] = {}

    def register(
        self,
        name: str,
        factory: Callable[[], AbstractTelephonyAdapter],
    ) -> None:
        """
        Register an adapter factory under a provider name.

        factory — zero-argument callable that returns a new adapter instance.
        Re-registering the same name overwrites the previous factory (last wins).
        """
        self._factories[name] = factory
        log.debug("telephony_registry.registered", provider=name)

    def get(self, name: str) -> AbstractTelephonyAdapter:
        """
        Create and return a new adapter instance for the named provider.

        Raises ProviderNotFoundError if no factory is registered for name.
        Returns a fresh instance — callers own the lifecycle.
        """
        factory = self._factories.get(name)
        if factory is None:
            registered = list(self._factories.keys())
            raise ProviderNotFoundError(
                f"Telephony provider '{name}' is not registered. "
                f"Registered providers: {registered}",
                detail={"requested": name, "available": registered},
            )
        return factory()

    def get_capabilities(self, name: str) -> ProviderCapabilities:
        """
        Return capabilities for a named provider without creating a full instance.

        Creates a temporary instance to read the capabilities property, then
        discards it.  This is lightweight for all current adapters.
        """
        return self.get(name).capabilities

    def list_providers(self) -> list[str]:
        """Return names of all registered providers."""
        return list(self._factories.keys())

    def resolve(self, preferred: str) -> AbstractTelephonyAdapter:
        """
        Return an adapter based on preference string.

        "auto" → first registered non-stub provider with supports_outbound_call;
                  falls back to "stub" if no production provider is registered.
        "stub" → always return StubTelephonyAdapter (bypasses preference).
        <name> → return that specific provider; raise ProviderNotFoundError if absent.

        Used by get_call_engine() in deps.py to select the telephony backend
        based on TELEPHONY_PROVIDER config without knowing provider specifics.
        """
        if preferred == "auto":
            return self._auto_select()
        if preferred == "stub":
            return self.get("stub")
        return self.get(preferred)

    def _auto_select(self) -> AbstractTelephonyAdapter:
        """
        Auto-selection policy:
          1. Try each non-stub registered provider in registration order
          2. Return the first one that supports outbound calls
          3. Fall back to stub if nothing else is available

        Logs the selection decision for observability.
        """
        for name, factory in self._factories.items():
            if name == "stub":
                continue
            adapter = factory()
            if adapter.capabilities.supports_outbound_call:
                log.info(
                    "telephony_registry.auto_selected",
                    provider=name,
                    supports_audio_stream=adapter.capabilities.supports_audio_stream,
                )
                return adapter

        # No production provider available
        from app.core.config import settings
        if settings.is_production:
            raise EngineError(
                "Production mode requires a real telephony provider. "
                "No configured provider found (Mango/Twilio). "
                "Set TELEPHONY_PROVIDER=mango and provide MANGO_API_KEY/MANGO_API_SALT. "
                "StubTelephonyAdapter is NOT allowed in production."
            )
        log.warning(
            "telephony_registry.auto_fallback_to_stub",
            message="No production telephony provider configured — using StubTelephonyAdapter",
        )
        return self.get("stub")


# ── Default registry factory ───────────────────────────────────────────────────

def build_default_registry() -> TelephonyProviderRegistry:
    """
    Build the production registry with all compiled-in adapters.

    Called once at application startup (in deps.py).  Registers providers
    conditionally based on which credentials are configured.

    Always registered:
      "stub"   — StubTelephonyAdapter (dev/test, always available)

    Conditionally registered (based on settings):
      "mango"  — MangoTelephonyAdapter  (if Mango API or SIP trunk is configured)
      "twilio" — TwilioTelephonyAdapter (if settings.twilio_configured — placeholder)
    """
    from app.core.config import settings
    from app.integrations.telephony.stub import StubTelephonyAdapter
    from app.integrations.telephony.twilio import TwilioTelephonyAdapter

    registry = TelephonyProviderRegistry()

    # Stub is always available
    registry.register("stub", StubTelephonyAdapter)

    # Mango — register if API or SIP trunk credentials are set
    if settings.mango_runtime_configured:
        from app.integrations.telephony.mango import MangoTelephonyAdapter
        registry.register("mango", MangoTelephonyAdapter)
        log.info("telephony_registry.mango_registered")

    # Twilio — placeholder registration (skeletal, NotImplementedError on use)
    # Uncomment when TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_FROM_NUMBER are added
    # to config.py and TwilioTelephonyAdapter is implemented.
    #
    # if settings.twilio_configured:
    #     registry.register("twilio", TwilioTelephonyAdapter)
    #     log.info("telephony_registry.twilio_registered")

    registered = registry.list_providers()
    log.info("telephony_registry.built", providers=registered)
    return registry
