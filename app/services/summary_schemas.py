"""
Summary pipeline output schemas.

All dataclasses are explicit contracts — no free-form dicts.
Each type serves a specific use case:

  SummaryQuality  — confidence and data-quality metadata attached to any output
  CallAnalysis    — internal extracted facts (input to all builders)
  WhisperBrief    — ≤150 char audio brief for manager (validated at construction)
  ManagerBrief    — structured context for Telegram card and future CRM widget
  CallOutcome     — structured call result for DB/CRM persistence

Backward compatibility:
  CallSummary (in summary_service.py) is kept unchanged for TransferService.
  summary_service.SummaryService wraps CallAnalysis → CallSummary transparently.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SummaryQuality:
    """
    Confidence and data-quality metadata for any summary output.

    confidence: 0.0–1.0 — how reliable the extracted facts are
    basis:      "rules" | "llm" | "hybrid" — which extraction path was used
    data_quality: "good" | "sparse" | "empty" — how much user speech was available
    word_count: total user-utterance word count (basis for quality assessment)
    """
    confidence: float
    basis: str          # "rules" | "llm" | "hybrid"
    data_quality: str   # "good" | "sparse" | "empty"
    word_count: int

    @classmethod
    def from_word_count(
        cls,
        word_count: int,
        basis: str = "rules",
        corroborating: int = 0,
        conflicting: int = 0,
    ) -> "SummaryQuality":
        """
        Compute quality from word count and signal counts.

        Confidence base:
          0 words → 0.0 (empty)
          < 50   → 0.3 (sparse)
          50–200 → 0.6 (good)
          200+   → 0.8 (good)

        Each corroborating signal: +0.05
        Each conflicting signal: -0.05
        Result clamped to [0.0, 1.0]
        """
        if word_count == 0:
            return cls(confidence=0.0, basis=basis, data_quality="empty", word_count=0)

        if word_count < 50:
            base = 0.3
            dq = "sparse"
        elif word_count < 200:
            base = 0.6
            dq = "good"
        else:
            base = 0.8
            dq = "good"

        confidence = base + corroborating * 0.05 - conflicting * 0.05
        return cls(
            confidence=max(0.0, min(1.0, confidence)),
            basis=basis,
            data_quality=dq,
            word_count=word_count,
        )


@dataclass
class CallAnalysis:
    """
    Internal structured extraction of call facts.

    This is the central data structure of the pipeline: normalize → extract → build outputs.
    Created by _extract_rules() and optionally enriched by _enhance_with_llm().

    budget_raw:          raw regex match (e.g. "50 000 руб")
    budget_normalized:   canonical form (e.g. "50 000 руб.")
    objections:          negation-aware list of objection signals
    positive_signals:    negation-aware list of interest signals
    strong_intent_signals: subset of positive_signals indicating purchase readiness
    key_topic:           main topic — populated by rule heuristic or LLM
    sentiment:           "positive" | "negative" | "neutral" | "mixed"
    customer_name:       extracted if AI addressed customer by name
    quality:             confidence + basis metadata
    raw_word_count:      total user-utterance words
    """
    budget_raw: Optional[str] = None
    budget_normalized: Optional[str] = None
    objections: list[str] = field(default_factory=list)
    positive_signals: list[str] = field(default_factory=list)
    strong_intent_signals: list[str] = field(default_factory=list)
    key_topic: Optional[str] = None
    sentiment: str = "neutral"
    customer_name: Optional[str] = None
    quality: SummaryQuality = field(
        default_factory=lambda: SummaryQuality(0.0, "rules", "empty", 0)
    )
    raw_word_count: int = 0


@dataclass
class WhisperBrief:
    """
    Short audio brief for manager before bridging to customer.

    HARD CONSTRAINT: text must be ≤ 150 characters.
    The constructor validates this — any violation is a programming error.

    Content priority:
      1. Customer intent (what they want)
      2. Key barrier (primary objection, if any)
      3. Recommended action (what to do first)

    Designed to be heard in ≤5 seconds of TTS playback.
    """
    text: str
    quality: SummaryQuality

    def __post_init__(self) -> None:
        if len(self.text) > 150:
            raise ValueError(
                f"WhisperBrief.text exceeds 150 chars ({len(self.text)}): {self.text!r}"
            )
        if not self.text.strip():
            raise ValueError("WhisperBrief.text must not be empty")


@dataclass
class ManagerBrief:
    """
    Structured context for the manager receiving a warm transfer.

    Designed for two surfaces:
    - Telegram bot card: compact, Markdown-friendly
    - Future CRM widget: structured fields for display

    Fields:
      customer_intent:      what the customer wants (1 sentence max)
      temperature:          "hot" | "warm" | "cold"
      objection:            primary objection if any
      next_best_action:     what to say/do immediately
      ai_covered:           topics already surfaced by the AI assistant
      recommended_first_line: exact opening phrase for manager
      quality:              confidence metadata
    """
    customer_intent: str
    temperature: str            # "hot" | "warm" | "cold"
    objection: Optional[str]
    next_best_action: str
    ai_covered: list[str]
    recommended_first_line: str
    quality: SummaryQuality

    def as_text(self) -> str:
        """
        Format for Telegram bot display (Markdown-safe plain text).

        Example:
          Намерение: готов к покупке
          Температура: 🔥 горячий
          Возражение: нет
          Что делать: перейдите к оформлению
          AI уже обсудил: бюджет, интерес
          Первая фраза: «Отлично, давайте оформим!»
          Уверенность: 80%
        """
        _TEMP_ICONS = {"hot": "🔥 горячий", "warm": "🌡 тёплый", "cold": "❄ холодный"}
        temp_str = _TEMP_ICONS.get(self.temperature, self.temperature)
        objection_str = self.objection or "нет"
        covered_str = ", ".join(self.ai_covered) if self.ai_covered else "нет данных"
        pct = int(self.quality.confidence * 100)
        lines = [
            f"Намерение: {self.customer_intent}",
            f"Температура: {temp_str}",
            f"Возражение: {objection_str}",
            f"Что делать: {self.next_best_action}",
            f"AI уже обсудил: {covered_str}",
            f'Первая фраза: «{self.recommended_first_line}»',
            f"Уверенность: {pct}%",
        ]
        return "\n".join(lines)

    def as_dict(self) -> dict:
        """Serialize to dict for API responses and CRM integration."""
        return {
            "customer_intent": self.customer_intent,
            "temperature": self.temperature,
            "objection": self.objection,
            "next_best_action": self.next_best_action,
            "ai_covered": self.ai_covered,
            "recommended_first_line": self.recommended_first_line,
            "quality": {
                "confidence": self.quality.confidence,
                "basis": self.quality.basis,
                "data_quality": self.quality.data_quality,
                "word_count": self.quality.word_count,
            },
        }


@dataclass
class CallOutcome:
    """
    Structured call result for DB persistence and CRM export.

    Serializable to JSON (all fields are primitives or lists of primitives).
    Stored in calls.summary as JSON string (no migration needed).

    intent:     normalized intent string (1 phrase)
    sentiment:  "positive" | "negative" | "neutral" | "mixed"
    confidence: 0.0–1.0
    budget:     normalized budget string or None
    key_facts:  top 3–5 facts as plain Russian phrases
    basis:      "rules" | "llm" | "hybrid"
    """
    intent: Optional[str]
    sentiment: str
    confidence: float
    budget: Optional[str]
    key_facts: list[str]
    basis: str

    def as_dict(self) -> dict:
        return {
            "intent": self.intent,
            "sentiment": self.sentiment,
            "confidence": self.confidence,
            "budget": self.budget,
            "key_facts": self.key_facts,
            "basis": self.basis,
        }
