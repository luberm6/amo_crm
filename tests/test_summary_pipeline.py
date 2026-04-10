"""
Tests for the hybrid summary pipeline.

Coverage:
  Negation handling (4 tests)
  New keyword sets (2 tests)
  Quality/confidence scoring (2 tests)
  WhisperBrief constraints (3 tests)
  ManagerBrief structure (3 tests)
  Golden integration test (1 test)
  Backward-compat regression (3 tests)
"""
from __future__ import annotations

import pytest
from dataclasses import dataclass

from app.models.transcript import TranscriptRole
from app.services.summary_schemas import WhisperBrief
from app.services.summary_service import (
    CallSummary,
    SummaryService,
    _extract_rules,
    _normalize,
    _build_manager_brief,
    _build_whisper_brief,
)


@dataclass
class _FakeEntry:
    """Minimal stand-in for TranscriptEntry — pipeline only needs role + text."""
    role: TranscriptRole
    text: str


def _user(text: str) -> _FakeEntry:
    return _FakeEntry(role=TranscriptRole.USER, text=text)


def _bot(text: str) -> _FakeEntry:
    return _FakeEntry(role=TranscriptRole.ASSISTANT, text=text)


svc = SummaryService()


# ── Negation tests ─────────────────────────────────────────────────────────────

def test_ne_interesno_is_objection_not_positive():
    """'не интересно' must land in objections, NOT positive_signals."""
    entries = [_user("Нет, мне совсем не интересно ваше предложение")]
    normalized = _normalize(entries)
    analysis = _extract_rules(normalized)
    assert "интересно" in analysis.objections or any("интересно" in o for o in analysis.objections)
    assert "интересно" not in analysis.positive_signals


def test_ne_dorogo_is_not_objection():
    """'не дорого' must NOT be treated as an objection."""
    entries = [_user("Это совсем не дорого, нормальная цена")]
    normalized = _normalize(entries)
    analysis = _extract_rules(normalized)
    assert "дорого" not in analysis.objections


def test_sovsem_ne_podkhodit_is_objection():
    """'не подходит' with negation → should be objection."""
    entries = [_user("Нет, не подходит для нас")]
    normalized = _normalize(entries)
    analysis = _extract_rules(normalized)
    assert analysis.objections  # at least one objection detected


def test_ne_gotov_seychas_is_objection():
    """'не готов сейчас' is a direct objection phrase."""
    entries = [_user("Я не готов сейчас принять решение")]
    normalized = _normalize(entries)
    analysis = _extract_rules(normalized)
    assert any("готов" in o or "не готов" in o for o in analysis.objections) or analysis.objections


# ── New keyword tests ──────────────────────────────────────────────────────────

def test_dorognovato_triggers_objection():
    """'дороговато' must be recognized as a price objection."""
    entries = [_user("Ну, немного дороговато для нас, конечно")]
    normalized = _normalize(entries)
    analysis = _extract_rules(normalized)
    assert any("дорог" in o for o in analysis.objections), f"got objections: {analysis.objections}"


def test_prishite_triggers_positive():
    """'пришлите' is a positive engagement signal."""
    entries = [_user("Хорошо, пришлите информацию на почту")]
    normalized = _normalize(entries)
    analysis = _extract_rules(normalized)
    assert "пришлите" in analysis.positive_signals


# ── Quality / confidence tests ─────────────────────────────────────────────────

def test_short_transcript_sparse_confidence():
    """Single 5-word user utterance → sparse quality, low confidence."""
    entries = [_user("да хорошо буду думать")]
    normalized = _normalize(entries)
    analysis = _extract_rules(normalized)
    assert analysis.quality.data_quality == "sparse"
    assert analysis.quality.confidence <= 0.5


def test_empty_transcript_empty_quality():
    """No entries at all → empty quality, zero confidence."""
    entries = []
    normalized = _normalize(entries)
    analysis = _extract_rules(normalized)
    assert analysis.quality.data_quality == "empty"
    assert analysis.quality.confidence == 0.0


# ── WhisperBrief constraints ───────────────────────────────────────────────────

def test_whisper_brief_max_150_chars():
    """WhisperBrief text must never exceed 150 characters."""
    # Rich transcript to generate many signals
    entries = [
        _user("Интересно, хочу оформить, готов, беру, давайте договоримся"),
        _user("Бюджет 500 000 рублей"),
        _user("Есть возражение — дорого, нет денег, подумаю, посмотрим"),
    ]
    normalized = _normalize(entries)
    analysis = _extract_rules(normalized)
    brief = _build_whisper_brief(analysis)
    assert len(brief.text) <= 150, f"WhisperBrief too long: {len(brief.text)} chars: {brief.text!r}"


def test_whisper_brief_word_boundary():
    """WhisperBrief truncation must not cut at mid-word."""
    # Craft a summary that would exceed 150 chars without truncation
    from app.services.summary_service import _truncate_at_word_boundary
    long_text = "Клиент хочет приобрести продукт по очень выгодной цене но пока не уверен в бюджете и хочет посоветоваться с коллегами перед финальным решением"
    result = _truncate_at_word_boundary(long_text, 80)
    # Result must not cut mid-word
    assert len(result) <= 80
    # The last character should not be mid-word (next char in original would be non-space)
    if len(result) < len(long_text):
        # Either ends at a word boundary or we truncated properly
        assert not result[-1].isalpha() or long_text[len(result)].startswith(" ") or len(result) >= len(long_text)


def test_whisper_fallback_non_empty():
    """Empty transcript → WhisperBrief with non-empty fallback text."""
    entries = []
    normalized = _normalize(entries)
    analysis = _extract_rules(normalized)
    brief = _build_whisper_brief(analysis)
    assert brief.text.strip(), "WhisperBrief must not be empty"
    assert len(brief.text) > 0


# ── ManagerBrief structure ────────────────────────────────────────────────────

def test_manager_brief_hot_on_strong_intent():
    """Strong purchase intent signals → temperature='hot'."""
    entries = [_user("Да, готов, записывайте, давайте оформим прямо сейчас")]
    normalized = _normalize(entries)
    analysis = _extract_rules(normalized)
    brief = _build_manager_brief(analysis)
    assert brief.temperature == "hot"
    assert brief.recommended_first_line  # must be non-empty


def test_manager_brief_cold_on_objections_only():
    """Multiple objections with no positive signals → temperature='cold'."""
    entries = [_user("Дорого, не готов, не интересно, нет денег")]
    normalized = _normalize(entries)
    analysis = _extract_rules(normalized)
    brief = _build_manager_brief(analysis)
    assert brief.temperature == "cold"


def test_recommended_first_line_always_non_empty():
    """recommended_first_line must always be a non-empty string."""
    # Even empty transcript
    entries = []
    normalized = _normalize(entries)
    analysis = _extract_rules(normalized)
    brief = _build_manager_brief(analysis)
    assert brief.recommended_first_line.strip(), "recommended_first_line must not be empty"


# ── Golden integration test ───────────────────────────────────────────────────

def test_golden_full_transcript():
    """
    10-line realistic transcript → verify ManagerBrief structure is correct.

    Scenario: Customer interested in CRM software, has budget ~300k, price concern.
    Expected: warm or hot temperature, budget extracted, objection present.
    """
    entries = [
        _bot("Добрый день! Расскажите о вашем бизнесе и задачах."),
        _user("Добрый день. У нас компания 50 человек, ищем CRM систему."),
        _bot("Отлично! Какой у вас бюджет?"),
        _user("Планируем потратить около 300 тысяч рублей."),
        _bot("Хорошо. У нас есть решения от 150 до 500 тысяч."),
        _user("Интересно, расскажите подробнее о функциях."),
        _bot("Конечно! Автоматизация продаж, аналитика, интеграция с 1C."),
        _user("Звучит хорошо. Но дороговато немного, если честно."),
        _bot("Понимаю. Можем рассмотреть рассрочку на 6 месяцев."),
        _user("Хорошо, пришлите коммерческое предложение, подумаем."),
    ]
    analysis = svc.analyze_sync(entries)

    # Quality should be "good" or "sparse" (depends on exact word count threshold)
    assert analysis.quality.data_quality in ("sparse", "good")

    # Budget should be extracted
    assert analysis.budget_normalized is not None
    assert "300" in analysis.budget_normalized or "тыс" in (analysis.budget_raw or "").lower()

    # Both interest and price concern
    assert analysis.positive_signals or analysis.strong_intent_signals
    assert analysis.objections

    # Sentiment mixed (interest + objection)
    assert analysis.sentiment in ("mixed", "positive")

    # ManagerBrief
    brief = _build_manager_brief(analysis)
    assert brief.temperature in ("warm", "hot")
    assert brief.objection is not None
    assert brief.customer_intent
    assert brief.recommended_first_line


# ── Backward-compat regression tests ─────────────────────────────────────────

def test_backward_compat_generate_summary_still_works():
    """Existing generate_summary() returns CallSummary with correct fields."""
    entries = [
        _user("Мне интересно ваше предложение, бюджет 100к"),
        _user("Но есть вопрос — немного дорого"),
    ]
    summary = svc.generate_summary(entries)
    assert isinstance(summary, CallSummary)
    assert summary.budget is not None
    assert summary.objections  # "дорого"
    assert summary.positive_signals  # "интересно"
    assert summary.sentiment == "mixed"


def test_backward_compat_generate_whisper_still_works():
    """generate_whisper(summary) returns str ≤ 150 chars."""
    summary = CallSummary(
        budget="500 000 руб",
        objections=["дорого"],
        positive_signals=["интересно"],
        sentiment="mixed",
    )
    whisper = svc.generate_whisper(summary)
    assert isinstance(whisper, str)
    assert len(whisper) <= 150
    assert whisper.strip()


def test_key_topic_now_populated():
    """key_topic must no longer be always None — regression against old bug."""
    entries = [_user("Интересно, хочу узнать подробнее")]
    summary = svc.generate_summary(entries)
    assert summary.key_topic is not None, "key_topic must be populated for non-empty transcripts"


# ── Normalization edge cases ──────────────────────────────────────────────────

def test_system_tool_roles_filtered():
    """SYSTEM and TOOL role entries must not trigger signal detection."""
    entries = [
        _FakeEntry(role=TranscriptRole.SYSTEM, text="Инициализация сессии"),
        _FakeEntry(role=TranscriptRole.TOOL, text="дорого не готов нет денег"),
    ]
    summary = svc.generate_summary(entries)
    assert summary.objections == []
    assert summary.sentiment == "neutral"


def test_duplicate_consecutive_utterances_deduplicated():
    """Consecutive identical utterances from same role are deduplicated."""
    entries = [
        _user("Интересно"),
        _user("интересно"),  # duplicate (same after normalize)
    ]
    normalized = _normalize(entries)
    assert len(normalized) == 1


def test_bot_objection_keyword_not_counted():
    """Bot utterance with objection keyword must NOT trigger objection."""
    entries = [_bot("Многие клиенты говорят: дорого, но потом видят ценность")]
    summary = svc.generate_summary(entries)
    assert summary.objections == []
