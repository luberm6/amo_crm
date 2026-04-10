"""
Tests for SummaryService — rule-based transcript extraction.

10 tests covering:
- Budget detection (rubles, k-notation)
- Objection / positive signal detection
- Sentiment derivation
- Whisper generation (length, fallback)
- as_text() formatting
"""
from __future__ import annotations

import pytest

from dataclasses import dataclass

from app.models.transcript import TranscriptRole
from app.services.summary_service import CallSummary, SummaryService


@dataclass
class _FakeEntry:
    """Minimal stand-in for TranscriptEntry — SummaryService only needs role + text."""
    role: TranscriptRole
    text: str


def _user(text: str) -> _FakeEntry:
    return _FakeEntry(role=TranscriptRole.USER, text=text)


def _bot(text: str) -> _FakeEntry:
    return _FakeEntry(role=TranscriptRole.ASSISTANT, text=text)


svc = SummaryService()


# ── Budget extraction ──────────────────────────────────────────────────────────

def test_budget_rubles():
    entries = [_user("У меня бюджет около 50 000 руб")]
    summary = svc.generate_summary(entries)
    assert summary.budget is not None
    assert "50" in summary.budget


def test_budget_k_notation():
    entries = [_user("готов потратить 200к на это")]
    summary = svc.generate_summary(entries)
    assert summary.budget is not None
    assert "200" in summary.budget


def test_budget_from_bot_if_user_has_none():
    """Budget detected in bot text when user doesn't mention it."""
    entries = [_bot("Итак, вы рассчитываете на 1.5 млн?")]
    summary = svc.generate_summary(entries)
    assert summary.budget is not None
    assert "1 500 000" in summary.budget or "500 000" in summary.budget


# ── Objection and positive signal detection ────────────────────────────────────

def test_objection_detected():
    entries = [_user("Нет, это слишком дорого для меня")]
    summary = svc.generate_summary(entries)
    assert "дорого" in summary.objections


def test_positive_signal_detected():
    entries = [_user("Мне интересно ваше предложение, расскажите подробнее")]
    summary = svc.generate_summary(entries)
    assert "интересно" in summary.positive_signals


def test_objection_only_from_user_not_bot():
    """Bot utterance with objection keyword must NOT trigger objection detection."""
    entries = [_bot("Многие клиенты говорят: дорого, но потом видят ценность")]
    summary = svc.generate_summary(entries)
    assert summary.objections == []


# ── Sentiment heuristic ────────────────────────────────────────────────────────

def test_sentiment_positive():
    entries = [_user("Хочу, давайте оформим")]
    summary = svc.generate_summary(entries)
    assert summary.sentiment == "positive"


def test_sentiment_negative():
    entries = [_user("нет денег, не могу, не подходит")]
    summary = svc.generate_summary(entries)
    assert summary.sentiment == "negative"


def test_sentiment_mixed():
    entries = [_user("интересно, но дорого")]
    summary = svc.generate_summary(entries)
    assert summary.sentiment == "mixed"


# ── Whisper generation ─────────────────────────────────────────────────────────

def test_whisper_max_200_chars():
    summary = CallSummary(
        budget="500 000 руб",
        objections=["дорого"],
        positive_signals=["интересно"],
    )
    whisper = svc.generate_whisper(summary)
    assert len(whisper) <= 200


def test_whisper_fallback_on_empty():
    summary = CallSummary()
    whisper = svc.generate_whisper(summary)
    assert "Клиент на линии" in whisper


# ── as_text formatting ─────────────────────────────────────────────────────────

def test_as_text_all_fields():
    summary = CallSummary(
        budget="100к",
        objections=["дорого"],
        positive_signals=["интересно"],
        sentiment="mixed",
    )
    text = summary.as_text()
    assert "Бюджет:" in text
    assert "Возражения:" in text
    assert "Интерес:" in text
    assert "Настрой:" in text


def test_as_text_no_data():
    """Empty summary still shows sentiment (default neutral)."""
    summary = CallSummary()
    text = summary.as_text()
    assert "Настрой:" in text
    assert "neutral" in text
