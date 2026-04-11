"""
Dataclasses для Gemini Live API WebSocket сообщений.

Gemini Live использует JSON поверх WebSocket (не protobuf на клиентской стороне).
Ссылка: https://ai.google.dev/api/multimodal-live

Входящие (client → Gemini):
  GeminiSetupMessage      — первое сообщение после connect (модель + system prompt)
  GeminiClientContent     — текстовый turn (steering injection, user input)
  GeminiRealtimeInput     — аудио chunk от телефонии (Phase 2)

Исходящие (Gemini → client):
  GeminiSetupComplete     — подтверждение что setup принят
  GeminiServerContent     — ответ модели (text или audio)
  GeminiModelTurn         — одна реплика модели
  GeminiPart              — один элемент реплики (text или inlineData)

Заметки по совместимости:
  - Все поля Optional — API может добавлять новые поля (forward compat)
  - extra="allow" в Pydantic не нужен — используем простые dataclasses
  - Python 3.9: нельзя dict[str, str], только Dict[str, str] из typing
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ── Исходящие (client → Gemini) ───────────────────────────────────────────────

@dataclass
class GeminiGenerationConfig:
    response_modalities: List[str] = field(default_factory=lambda: ["TEXT"])
    speech_config: Optional[Dict[str, Any]] = None  # Phase 2: AUDIO modality

    def to_dict(self) -> dict:
        d: Dict[str, Any] = {"responseModalities": self.response_modalities}
        if self.speech_config:
            d["speechConfig"] = self.speech_config
        return d

    @classmethod
    def for_audio_modality(
        cls,
        voice_name: str = "Aoede",
        language_code: str = "ru-RU",
    ) -> "GeminiGenerationConfig":
        """Create config for Gemini AUDIO output modality.
        Audio-to-audio models (e.g. gemini-3.1-flash-live-preview) require AUDIO only.
        language_code instructs the model to synthesise speech in the given locale.
        """
        return cls(
            response_modalities=["AUDIO"],
            speech_config={
                "languageCode": language_code,
                "voiceConfig": {
                    "prebuiltVoiceConfig": {
                        "voiceName": voice_name,
                    }
                },
            },
        )


@dataclass
class GeminiSystemInstruction:
    parts: List[Dict[str, str]] = field(default_factory=list)

    @classmethod
    def from_text(cls, text: str) -> "GeminiSystemInstruction":
        return cls(parts=[{"text": text}])


@dataclass
class GeminiSetupPayload:
    model: str
    generation_config: GeminiGenerationConfig
    system_instruction: GeminiSystemInstruction
    tools: List[Any] = field(default_factory=list)

    def to_dict(self) -> dict:
        d: Dict[str, Any] = {
            "model": self.model,
            "generationConfig": self.generation_config.to_dict(),
            "systemInstruction": {
                "parts": self.system_instruction.parts,
            },
        }
        if self.tools:
            d["tools"] = self.tools
        return d


@dataclass
class GeminiSetupMessage:
    """Первое сообщение к Gemini после WS connect."""
    setup: GeminiSetupPayload

    def to_dict(self) -> dict:
        return {"setup": self.setup.to_dict()}


@dataclass
class GeminiTurnPart:
    text: str

    def to_dict(self) -> dict:
        return {"text": self.text}


@dataclass
class GeminiTurn:
    role: str   # "user" | "model"
    parts: List[GeminiTurnPart] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "parts": [p.to_dict() for p in self.parts],
        }


@dataclass
class GeminiClientContent:
    """
    Текстовый input к Gemini: пользовательский turn или steering instruction.
    Для steering: role="user", turn_complete=True.
    """
    turns: List[GeminiTurn]
    turn_complete: bool = True

    def to_dict(self) -> dict:
        return {
            "clientContent": {
                "turns": [t.to_dict() for t in self.turns],
                "turnComplete": self.turn_complete,
            }
        }

    @classmethod
    def from_text(cls, text: str, role: str = "user") -> "GeminiClientContent":
        return cls(
            turns=[GeminiTurn(role=role, parts=[GeminiTurnPart(text=text)])],
            turn_complete=True,
        )


@dataclass
class GeminiRealtimeInput:
    """Аудио chunk от телефонии → Gemini (Phase 2)."""
    mime_type: str   # "audio/pcm;rate=16000"
    data_b64: str    # base64-encoded PCM bytes

    def to_dict(self) -> dict:
        return {
            "realtimeInput": {
                "audio": {"data": self.data_b64, "mimeType": self.mime_type}
            }
        }


# ── Входящие (Gemini → client) ────────────────────────────────────────────────

@dataclass
class GeminiInlineData:
    mime_type: str
    data_b64: str  # base64-encoded bytes


@dataclass
class GeminiServerPart:
    text: Optional[str] = None
    inline_data: Optional[GeminiInlineData] = None

    @classmethod
    def from_dict(cls, d: dict) -> "GeminiServerPart":
        if "text" in d:
            return cls(text=d["text"])
        if "inlineData" in d:
            return cls(
                inline_data=GeminiInlineData(
                    mime_type=d["inlineData"].get("mimeType", ""),
                    data_b64=d["inlineData"].get("data", ""),
                )
            )
        return cls()


@dataclass
class GeminiModelTurn:
    parts: List[GeminiServerPart] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "GeminiModelTurn":
        return cls(parts=[GeminiServerPart.from_dict(p) for p in d.get("parts", [])])


@dataclass
class GeminiServerContent:
    model_turn: Optional[GeminiModelTurn] = None
    turn_complete: bool = False
    interrupted: bool = False

    @classmethod
    def from_dict(cls, d: dict) -> "GeminiServerContent":
        return cls(
            model_turn=GeminiModelTurn.from_dict(d["modelTurn"]) if "modelTurn" in d else None,
            turn_complete=d.get("turnComplete", False),
            interrupted=d.get("interrupted", False),
        )
