from app.integrations.telephony.audio_bridge import (
    AbstractAudioBridge,
    NullAudioBridge,
    SilenceAudioBridge,
)
from app.integrations.telephony.freeswitch_bridge import FreeSwitchAudioBridge
from app.integrations.telephony.base import (
    AbstractTelephonyAdapter,
    TelephonyChannel,
    TelephonyLegState,
    TelephonyOriginateResult,
)
from app.integrations.telephony.capabilities import (
    ProviderCapabilities,
    UnsupportedOperationError,
)
from app.integrations.telephony.registry import (
    ProviderNotFoundError,
    TelephonyProviderRegistry,
    build_default_registry,
)
from app.integrations.telephony.stub import StubTelephonyAdapter
from app.integrations.telephony.twilio import TwilioTelephonyAdapter

__all__ = [
    "AbstractAudioBridge",
    "SilenceAudioBridge",
    "NullAudioBridge",
    "FreeSwitchAudioBridge",
    "AbstractTelephonyAdapter",
    "TelephonyChannel",
    "TelephonyLegState",
    "TelephonyOriginateResult",
    "ProviderCapabilities",
    "UnsupportedOperationError",
    "ProviderNotFoundError",
    "TelephonyProviderRegistry",
    "build_default_registry",
    "StubTelephonyAdapter",
    "TwilioTelephonyAdapter",
]
