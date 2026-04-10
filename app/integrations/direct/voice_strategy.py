from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from app.core.config import Settings, settings
from app.core.exceptions import EngineError

VoiceStrategyName = Literal[
    "disabled",
    "gemini_primary",
    "tts_primary",
    "experimental_hybrid",
]
VoicePathName = Literal[
    "disabled",
    "gemini_native",
    "tts_primary",
    "tts_fallback",
]
IssueStatus = Literal["pass", "warn", "fail"]


@dataclass(frozen=True)
class VoiceStrategyCheck:
    name: str
    status: IssueStatus
    message: str
    details: Optional[dict] = None


@dataclass(frozen=True)
class VoiceStrategyDefinition:
    strategy: VoiceStrategyName
    primary_path: VoicePathName
    fallback_path: Optional[VoicePathName]
    initial_greeting_path: VoicePathName
    experimental: bool
    uses_gemini_audio_output: bool
    needs_tts_provider: bool
    allow_tts_fallback: bool


@dataclass
class SessionVoiceState:
    definition: VoiceStrategyDefinition
    active_path: VoicePathName
    fallback_activated: bool = False

    @property
    def strategy(self) -> VoiceStrategyName:
        return self.definition.strategy

    @property
    def primary_path(self) -> VoicePathName:
        return self.definition.primary_path

    @property
    def fallback_path(self) -> Optional[VoicePathName]:
        return self.definition.fallback_path

    @property
    def initial_greeting_path(self) -> VoicePathName:
        return self.definition.initial_greeting_path

    @property
    def experimental(self) -> bool:
        return self.definition.experimental

    def wants_gemini_audio_output(self) -> bool:
        return self.active_path == "gemini_native"

    def wants_tts_output(self) -> bool:
        return self.active_path in {"tts_primary", "tts_fallback"}

    def wants_tts_for_assistant_text(self) -> bool:
        return self.wants_tts_output()

    def can_activate_tts_fallback(self) -> bool:
        return (
            self.definition.allow_tts_fallback
            and self.definition.fallback_path == "tts_fallback"
            and self.active_path != "tts_fallback"
        )

    def activate_tts_fallback(self) -> bool:
        if not self.can_activate_tts_fallback():
            return False
        self.active_path = "tts_fallback"
        self.fallback_activated = True
        return True


def inspect_voice_strategy(
    cfg: Settings = settings,
    *,
    strategy_override: Optional[VoiceStrategyName] = None,
) -> list[VoiceStrategyCheck]:
    strategy = strategy_override or cfg.direct_voice_strategy
    checks: list[VoiceStrategyCheck] = [
        VoiceStrategyCheck(
            name="voice_strategy",
            status="pass",
            message="Voice strategy is configured.",
            details={"strategy": strategy},
        )
    ]

    if strategy == "disabled":
        checks.append(
            VoiceStrategyCheck(
                name="voice_strategy_disabled",
                status="warn",
                message="Direct voice strategy is disabled. First live Direct call cannot start.",
            )
        )
        return checks

    if strategy == "gemini_primary":
        if not cfg.gemini_audio_output_enabled:
            checks.append(
                VoiceStrategyCheck(
                    name="voice_strategy_gemini_output",
                    status="fail",
                    message=(
                        "gemini_primary requires GEMINI_AUDIO_OUTPUT_ENABLED=true."
                    ),
                )
            )
        else:
            checks.append(
                VoiceStrategyCheck(
                    name="voice_strategy_gemini_output",
                    status="pass",
                    message="Gemini native audio is enabled for primary voice.",
                )
            )

        if cfg.elevenlabs_configured and cfg.direct_voice_allow_tts_fallback:
            checks.append(
                VoiceStrategyCheck(
                    name="voice_strategy_fallback",
                    status="pass",
                    message="ElevenLabs fallback is configured for gemini_primary.",
                    details={"fallback_path": "tts_fallback"},
                )
            )
        elif cfg.elevenlabs_configured and not cfg.direct_voice_allow_tts_fallback:
            checks.append(
                VoiceStrategyCheck(
                    name="voice_strategy_fallback",
                    status="warn",
                    message="ElevenLabs is configured but runtime fallback is disabled.",
                )
            )
        else:
            checks.append(
                VoiceStrategyCheck(
                    name="voice_strategy_fallback",
                    status="warn",
                    message="No ElevenLabs fallback configured for gemini_primary.",
                )
            )
        return checks

    if strategy == "tts_primary":
        if not cfg.elevenlabs_configured:
            checks.append(
                VoiceStrategyCheck(
                    name="voice_strategy_tts_primary",
                    status="fail",
                    message="tts_primary requires ElevenLabs to be fully configured.",
                )
            )
        else:
            checks.append(
                VoiceStrategyCheck(
                    name="voice_strategy_tts_primary",
                    status="pass",
                    message="ElevenLabs is configured for tts_primary.",
                )
            )

        if cfg.gemini_audio_output_enabled:
            checks.append(
                VoiceStrategyCheck(
                    name="voice_strategy_hybrid_guard",
                    status="fail",
                    message=(
                        "tts_primary forbids GEMINI_AUDIO_OUTPUT_ENABLED=true. "
                        "Use experimental_hybrid if you want mixed behavior."
                    ),
                )
            )
        else:
            checks.append(
                VoiceStrategyCheck(
                    name="voice_strategy_hybrid_guard",
                    status="pass",
                    message="Hybrid mode is disabled under tts_primary.",
                )
            )
        return checks

    if strategy == "experimental_hybrid":
        if not cfg.gemini_audio_output_enabled or not cfg.elevenlabs_configured:
            checks.append(
                VoiceStrategyCheck(
                    name="voice_strategy_experimental",
                    status="fail",
                    message=(
                        "experimental_hybrid requires both GEMINI_AUDIO_OUTPUT_ENABLED=true "
                        "and ElevenLabs to be configured."
                    ),
                )
            )
        else:
            checks.append(
                VoiceStrategyCheck(
                    name="voice_strategy_experimental",
                    status="warn",
                    message="experimental_hybrid is enabled. Do not use it as the default production mode.",
                )
            )
        return checks

    checks.append(
        VoiceStrategyCheck(
            name="voice_strategy_unknown",
            status="fail",
            message=f"Unsupported voice strategy: {strategy}",
        )
    )
    return checks


def resolve_voice_strategy_definition(
    cfg: Settings = settings,
    *,
    strategy_override: Optional[VoiceStrategyName] = None,
) -> VoiceStrategyDefinition:
    strategy = strategy_override or cfg.direct_voice_strategy
    if strategy == "disabled":
        return VoiceStrategyDefinition(
            strategy="disabled",
            primary_path="disabled",
            fallback_path=None,
            initial_greeting_path="disabled",
            experimental=False,
            uses_gemini_audio_output=False,
            needs_tts_provider=False,
            allow_tts_fallback=False,
        )
    if strategy == "gemini_primary":
        return VoiceStrategyDefinition(
            strategy="gemini_primary",
            primary_path="gemini_native",
            fallback_path="tts_fallback"
            if cfg.elevenlabs_configured and cfg.direct_voice_allow_tts_fallback
            else None,
            initial_greeting_path="gemini_native",
            experimental=False,
            uses_gemini_audio_output=True,
            needs_tts_provider=bool(
                cfg.elevenlabs_configured and cfg.direct_voice_allow_tts_fallback
            ),
            allow_tts_fallback=bool(
                cfg.elevenlabs_configured and cfg.direct_voice_allow_tts_fallback
            ),
        )
    if strategy == "tts_primary":
        return VoiceStrategyDefinition(
            strategy="tts_primary",
            primary_path="tts_primary",
            fallback_path=None,
            initial_greeting_path="tts_primary",
            experimental=False,
            uses_gemini_audio_output=False,
            needs_tts_provider=True,
            allow_tts_fallback=False,
        )
    if strategy == "experimental_hybrid":
        return VoiceStrategyDefinition(
            strategy="experimental_hybrid",
            primary_path="gemini_native",
            fallback_path="tts_fallback" if cfg.elevenlabs_configured else None,
            initial_greeting_path="tts_primary",
            experimental=True,
            uses_gemini_audio_output=True,
            needs_tts_provider=True,
            allow_tts_fallback=bool(cfg.elevenlabs_configured),
        )
    raise EngineError(
        f"Unsupported voice strategy: {strategy}",
        detail={"strategy": strategy},
    )


def make_session_voice_state(cfg: Settings = settings) -> SessionVoiceState:
    definition = resolve_voice_strategy_definition(cfg)
    return SessionVoiceState(
        definition=definition,
        active_path=definition.primary_path,
    )


def make_session_voice_state_for_strategy(
    strategy: VoiceStrategyName,
    cfg: Settings = settings,
) -> SessionVoiceState:
    definition = resolve_voice_strategy_definition(cfg, strategy_override=strategy)
    return SessionVoiceState(definition=definition, active_path=definition.primary_path)

def ensure_voice_strategy_valid(
    cfg: Settings = settings,
    *,
    strategy_override: Optional[VoiceStrategyName] = None,
) -> VoiceStrategyDefinition:
    failures = [
        check
        for check in inspect_voice_strategy(cfg, strategy_override=strategy_override)
        if check.status == "fail"
    ]
    if failures:
        raise EngineError(
            "Direct voice strategy configuration is invalid.",
            detail={
                "strategy": strategy_override or cfg.direct_voice_strategy,
                "failures": [
                    {
                        "name": check.name,
                        "message": check.message,
                        "details": check.details,
                    }
                    for check in failures
                ],
            },
        )
    return resolve_voice_strategy_definition(cfg, strategy_override=strategy_override)
