"""
SummaryService — hybrid call summary and whisper generation pipeline.

Architecture:
  Stage 1: normalize(entries) → list[_NormalizedUtterance]
           Clean text, filter SYSTEM/TOOL roles, deduplicate, split into words.

  Stage 2: _extract_rules(normalized) → CallAnalysis
           Negation-aware keyword matching, budget extraction with normalization,
           quality scoring, key_topic heuristic.

  Stage 3 (optional): _enhance_with_llm(analysis, text) → CallAnalysis
           Gemini REST API call for key_topic and customer_intent refinement.
           Always best-effort — rule-based result returned on any error.

  Build outputs:
    build_manager_brief(analysis) → ManagerBrief
    build_whisper_brief(analysis) → WhisperBrief  (≤150 chars, word-boundary)
    build_call_outcome(analysis) → CallOutcome

Backward compatibility:
  generate_summary(entries) → CallSummary   (unchanged signature)
  generate_whisper(summary) → str           (unchanged signature, improved whisper)
  summary.as_text()                         (unchanged)

  These three are called by TransferService.initiate_transfer() and must continue
  to work without modification to transfer_service.py.

Negation handling:
  Scan a 3-word window before each keyword match.
  Negation prefixes: {"не", "нет", "никак", "совсем не", "вовсе не"}.
  Negated positive keyword → treated as objection.
  Negated objection keyword → ignored.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from app.models.transcript import TranscriptEntry, TranscriptRole
from app.services.summary_schemas import (
    CallAnalysis,
    CallOutcome,
    ManagerBrief,
    SummaryQuality,
    WhisperBrief,
)


# ── Budget pattern ─────────────────────────────────────────────────────────────
# Matches: 50к, 100k, 1.5 млн, 200 тысяч, 500 000 руб, 50000₽
_AMOUNT_RE = re.compile(
    r"\b(\d[\d\s]*(?:[.,]\d+)?)\s*"
    r"(?:к\b|k\b|тыс(?:яч(?:и|ей)?)?\b|млн\b|мил(?:лион(?:а|ов)?)?\b"
    r"|руб(?:л(?:ей|я|ь))?\b|₽)",
    re.IGNORECASE | re.UNICODE,
)

# ── Negation tokens ────────────────────────────────────────────────────────────
# Checked in a 3-word window before the matched keyword
_NEGATION_TOKENS: frozenset[str] = frozenset([
    "не", "нет", "никак", "вовсе", "совсем", "ничуть", "отнюдь",
])

# ── Objection keywords ─────────────────────────────────────────────────────────
# Checked in USER utterances only
_OBJECTION_KEYWORDS: frozenset[str] = frozenset([
    # Price / budget resistance
    "дорого", "дорогновато", "дороговато", "дорогой", "слишком дорого",
    # Not ready / timing
    "не готов", "не сейчас", "не актуально", "пока не нужно", "давайте позже",
    "перезвоните", "позвоните позже", "пока подождём",
    # Uncertainty
    "подумаю", "нужно подумать", "надо подумать", "посмотрим", "не уверен",
    # Decision process
    "посоветуюсь", "надо посовещаться", "спрошу у", "согласую",
    # Comparison / alternatives
    "сравниваю", "другие предложения", "смотрю варианты", "посмотрю",
    # Financial / can't
    "не могу", "нет денег", "нет бюджета",
    # Rejection
    "не интересно", "не подходит", "не актуально сейчас", "уже есть",
])

# ── Positive signal keywords ───────────────────────────────────────────────────
# Checked in USER utterances only
_POSITIVE_KEYWORDS: frozenset[str] = frozenset([
    # Interest
    "интересно", "расскажите", "подробнее", "хочу узнать", "любопытно",
    # Information request
    "пришлите", "отправьте", "скиньте", "пришлите информацию",
    # Progress / next step
    "как оформить", "что нужно", "когда можно", "запишите",
    # Affirmation
    "хорошо", "окей", "ладно", "понял", "понятно", "договорились",
    # Positive evaluation
    "нравится", "подходит", "удобно", "устраивает", "всё устраивает",
    "меня устраивает", "звучит хорошо",
    # Intent signals
    "хочу", "хотел бы", "давайте", "согласен",
])

# ── Strong intent signals (subset of positive — purchase readiness) ────────────
_STRONG_INTENT_KEYWORDS: frozenset[str] = frozenset([
    "готов купить", "хочу оформить", "хочу купить", "оформляйте", "записывайте",
    "давайте оформим", "давайте договоримся", "беру", "купим", "оформим",
    "готов", "договорились", "согласен",
])


# ── Internal normalization dataclass ──────────────────────────────────────────
@dataclass
class _NormalizedUtterance:
    role: TranscriptRole
    text: str           # lowercased, stripped, whitespace-collapsed
    words: list[str]    # pre-split for negation scanning
    is_user: bool


# ── Backward-compatible public summary dataclass ──────────────────────────────
@dataclass
class CallSummary:
    """
    Structured summary — backward-compatible with TransferService callsites.

    TransferService calls:
        summary_obj = svc.generate_summary(entries)
        whisper_text = svc.generate_whisper(summary_obj)
        text = summary_obj.as_text()
    These three must continue to work unchanged.
    """
    budget: Optional[str] = None
    objections: list[str] = field(default_factory=list)
    positive_signals: list[str] = field(default_factory=list)
    key_topic: Optional[str] = None      # NOW populated (was always None before)
    sentiment: str = "neutral"

    def as_text(self) -> str:
        """
        Compact multi-line Russian string for manager card and TransferRecord.summary.
        Unchanged format for backward compatibility.
        """
        lines: list[str] = []
        if self.budget:
            lines.append(f"Бюджет: {self.budget}")
        if self.objections:
            lines.append(f"Возражения: {'; '.join(self.objections[:3])}")
        if self.positive_signals:
            lines.append(f"Интерес: {'; '.join(self.positive_signals[:2])}")
        if self.key_topic:
            lines.append(f"Тема: {self.key_topic}")
        lines.append(f"Настрой: {self.sentiment}")
        return "\n".join(lines) if lines else "Нет данных"


# ── Pipeline helpers ───────────────────────────────────────────────────────────

def _normalize_budget(raw: str) -> str:
    """
    Normalize a raw budget string to canonical form.

    "50к" → "50 000 руб."
    "1.5 млн" → "1 500 000 руб."
    "200 000 руб" → "200 000 руб."
    "50000₽" → "50 000 руб."
    """
    # Extract numeric part (handle "50 000" with internal space and "1.5")
    clean = re.sub(r"\s+", "", raw)  # collapse whitespace first
    num_match = re.match(r"([\d.,]+)", clean)
    if not num_match:
        return raw.strip()

    num_str = num_match.group(1).replace(",", ".")
    try:
        num = float(num_str)
    except ValueError:
        return raw.strip()

    # Detect multiplier from original raw string
    lower = raw.lower()
    if re.search(r"млн|мил", lower):
        num *= 1_000_000
    elif re.search(r"тыс|к\b|k\b", lower):
        num *= 1_000

    int_num = int(num)
    # Format with space thousands separator
    formatted = f"{int_num:,}".replace(",", " ")
    return f"{formatted} руб."


def _truncate_at_word_boundary(text: str, max_len: int) -> str:
    """
    Truncate text at a word boundary, not mid-character.
    Removes trailing punctuation/spaces after truncation.
    """
    if len(text) <= max_len:
        return text
    truncated = text[:max_len]
    last_space = truncated.rfind(" ")
    if last_space > max_len // 2:
        truncated = truncated[:last_space]
    return truncated.rstrip(".,;:— ")


def _has_negation_before(words: list[str], keyword_start: int, window: int = 3) -> bool:
    """
    Check if any negation token appears within `window` words before keyword_start.
    Handles multi-word negation tokens like "совсем не" by checking individual tokens.

    Special case: "нет" at position 0 or 1 is usually a reply starter ("Нет, это дорого")
    not a negation of the keyword, so it's excluded from the check.
    """
    look_from = max(0, keyword_start - window)
    window_words = words[look_from:keyword_start]

    for i, w in enumerate(window_words):
        if w in _NEGATION_TOKENS:
            # Special case: "нет" at utterance start (position 0 or 1 in original words)
            # is usually a reply opener, not a negation of the following keyword
            actual_pos = look_from + i
            if w == "нет" and actual_pos <= 1:
                continue
            return True
    return False


def _find_keyword_in_words(words: list[str], keyword: str) -> Optional[int]:
    """
    Find first occurrence of keyword (may be multi-word) in words list.
    Returns the start index or None.
    """
    kw_parts = keyword.split()
    kw_len = len(kw_parts)
    for i in range(len(words) - kw_len + 1):
        if words[i:i + kw_len] == kw_parts:
            return i
    return None


# ── Main pipeline stages ───────────────────────────────────────────────────────

def _normalize(entries: list[TranscriptEntry]) -> list[_NormalizedUtterance]:
    """
    Stage 1: clean, filter, deduplicate transcript entries.
    Filters SYSTEM and TOOL roles. Deduplicates consecutive same-role identical text.
    """
    result: list[_NormalizedUtterance] = []
    seen_last: tuple[Optional[TranscriptRole], str] = (None, "")

    for entry in entries:
        # Filter non-speech roles
        if entry.role in (TranscriptRole.SYSTEM, TranscriptRole.TOOL):
            continue

        # Normalize: lowercase, collapse whitespace
        text = re.sub(r"\s+", " ", entry.text.lower()).strip()
        if not text:
            continue

        # Deduplicate consecutive same-role identical utterances
        if (entry.role, text) == seen_last:
            continue
        seen_last = (entry.role, text)

        words = re.sub(r'[.,!?;:«»"\'—\-]', ' ', text).split()
        result.append(_NormalizedUtterance(
            role=entry.role,
            text=text,
            words=words,
            is_user=(entry.role == TranscriptRole.USER),
        ))

    return result


def _extract_rules(normalized: list[_NormalizedUtterance]) -> CallAnalysis:
    """
    Stage 2: negation-aware keyword extraction, budget detection, quality scoring.
    """
    analysis = CallAnalysis()

    user_utterances = [u for u in normalized if u.is_user]
    all_texts = [u.text for u in normalized]
    user_texts = [u.text for u in user_utterances]

    # Word count for quality
    user_word_count = sum(len(u.words) for u in user_utterances)

    # ── Budget extraction ──────────────────────────────────────────────────
    # Prefer user utterances, fall back to all text
    for text in user_texts + all_texts:
        m = _AMOUNT_RE.search(text)
        if m:
            analysis.budget_raw = m.group(0).strip()
            analysis.budget_normalized = _normalize_budget(analysis.budget_raw)
            break

    # ── Objection and positive signal extraction (negation-aware) ─────────
    for utt in user_utterances:
        words = utt.words

        # Strong intent (checked first — highest priority)
        for kw in _STRONG_INTENT_KEYWORDS:
            if kw in utt.text:
                kw_idx = _find_keyword_in_words(words, kw)
                if kw_idx is not None:
                    negated = _has_negation_before(words, kw_idx)
                    if not negated and kw not in analysis.strong_intent_signals:
                        analysis.strong_intent_signals.append(kw)
                    elif negated and kw not in analysis.objections:
                        analysis.objections.append(kw)

        # Objections
        for kw in _OBJECTION_KEYWORDS:
            if kw in utt.text:
                kw_idx = _find_keyword_in_words(words, kw)
                if kw_idx is not None:
                    negated = _has_negation_before(words, kw_idx)
                    if not negated and kw not in analysis.objections:
                        analysis.objections.append(kw)
                    # Negated objection → skip (not an objection)

        # Positive signals
        for kw in _POSITIVE_KEYWORDS:
            if kw in utt.text:
                kw_idx = _find_keyword_in_words(words, kw)
                if kw_idx is not None:
                    negated = _has_negation_before(words, kw_idx)
                    if not negated and kw not in analysis.positive_signals:
                        analysis.positive_signals.append(kw)
                    elif negated and kw not in analysis.objections:
                        # Negated positive → treated as objection
                        analysis.objections.append(kw)

    # ── Sentiment heuristic ────────────────────────────────────────────────
    has_obj = bool(analysis.objections)
    has_pos = bool(analysis.positive_signals or analysis.strong_intent_signals)
    if has_pos and not has_obj:
        analysis.sentiment = "positive"
    elif has_obj and not has_pos:
        analysis.sentiment = "negative"
    elif has_pos and has_obj:
        analysis.sentiment = "mixed"
    # else neutral (default)

    # ── Key topic heuristic ────────────────────────────────────────────────
    if analysis.strong_intent_signals:
        analysis.key_topic = "покупка"
    elif analysis.objections:
        analysis.key_topic = analysis.objections[0]
    elif analysis.positive_signals:
        analysis.key_topic = "интерес к продукту"
    # else stays None (no signal at all)

    # ── Quality scoring ────────────────────────────────────────────────────
    n_corroborating = len(analysis.positive_signals) + len(analysis.strong_intent_signals) + len(analysis.objections)
    n_conflicting = 1 if (has_pos and has_obj) else 0
    analysis.quality = SummaryQuality.from_word_count(
        word_count=user_word_count,
        basis="rules",
        corroborating=n_corroborating,
        conflicting=n_conflicting,
    )
    analysis.raw_word_count = user_word_count

    return analysis


async def _enhance_with_llm(analysis: CallAnalysis, full_text: str) -> CallAnalysis:
    """
    Stage 3 (optional): Gemini REST API call to refine key_topic and customer_intent.
    Always best-effort — returns unmodified analysis on any error.
    """
    import json
    import httpx
    from app.core.config import settings

    prompt = (
        "Ты — аналитик звонков. Проанализируй транскрипт разговора AI-ассистента с клиентом.\n"
        "Ответь строго в формате JSON (без markdown-блоков):\n"
        '{"key_topic": "...", "customer_intent": "...", "confidence_boost": 0.0}\n\n'
        "key_topic — главная тема разговора (1–5 слов, по-русски)\n"
        "customer_intent — намерение клиента (1 предложение, по-русски)\n"
        "confidence_boost — насколько изменить уверенность, от -0.1 до +0.1\n\n"
        f"Транскрипт:\n{full_text[:3000]}"
    )

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{settings.gemini_model_id}:generateContent"
        f"?key={settings.gemini_api_key}"
    )

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 256},
    }

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.post(url, json=payload)
        if resp.status_code != 200:
            return analysis

        data = resp.json()
        text_out = (
            data.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "")
        )
        parsed = json.loads(text_out.strip())

        if "key_topic" in parsed and parsed["key_topic"]:
            analysis.key_topic = str(parsed["key_topic"])[:80]
        boost = float(parsed.get("confidence_boost", 0.0))
        boost = max(-0.1, min(0.1, boost))

        q = analysis.quality
        analysis.quality = SummaryQuality(
            confidence=max(0.0, min(1.0, q.confidence + boost)),
            basis="hybrid",
            data_quality=q.data_quality,
            word_count=q.word_count,
        )
    except Exception:
        # LLM failure must never crash the pipeline
        pass

    return analysis


def _build_manager_brief(analysis: CallAnalysis) -> ManagerBrief:
    """Build ManagerBrief from CallAnalysis."""

    # ── Temperature ────────────────────────────────────────────────────────
    if analysis.strong_intent_signals and not analysis.objections:
        temperature = "hot"
    elif analysis.objections and not analysis.positive_signals and not analysis.strong_intent_signals:
        temperature = "cold"
    else:
        temperature = "warm"

    # ── Customer intent ────────────────────────────────────────────────────
    if temperature == "hot":
        intent_phrase = f"Клиент готов к покупке — {analysis.strong_intent_signals[0]}"
    elif temperature == "cold" and analysis.objections:
        intent_phrase = f"Клиент сомневается: {analysis.objections[0]}"
    elif analysis.budget_normalized:
        intent_phrase = f"Клиент интересуется, бюджет {analysis.budget_normalized}"
    elif analysis.positive_signals:
        intent_phrase = "Клиент проявляет интерес к продукту"
    else:
        intent_phrase = "Характер интереса клиента неизвестен"

    # ── Primary objection ──────────────────────────────────────────────────
    primary_objection: Optional[str] = analysis.objections[0] if analysis.objections else None

    # ── Next best action ───────────────────────────────────────────────────
    if temperature == "hot":
        next_action = "Перейдите к оформлению сделки"
    elif temperature == "cold":
        price_objections = {"дорого", "дорогновато", "дороговато", "нет денег", "нет бюджета"}
        has_price_obj = bool(set(analysis.objections) & price_objections)
        if has_price_obj:
            next_action = "Предложите рассрочку или альтернативный вариант"
        else:
            next_action = "Выслушайте возражение, не давите"
    elif analysis.budget_normalized:
        next_action = f"Предложите вариант в рамках бюджета {analysis.budget_normalized}"
    else:
        next_action = "Поддержите интерес, уточните ключевую потребность"

    # ── Recommended first line ─────────────────────────────────────────────
    name_part = f", {analysis.customer_name}!" if analysis.customer_name else "!"
    if temperature == "hot":
        first_line = "Отлично, давайте оформим — вам удобно прямо сейчас?"
    elif temperature == "cold":
        first_line = "Понимаю, давайте разберёмся что не подходит — расскажите подробнее?"
    elif analysis.key_topic and analysis.key_topic != "интерес к продукту":
        first_line = f"Добрый день{name_part} Продолжим — хотите узнать подробнее о {analysis.key_topic}?"
    else:
        first_line = f"Добрый день{name_part} Расскажите, что для вас важнее всего?"

    # ── AI already covered ─────────────────────────────────────────────────
    ai_covered: list[str] = []
    if analysis.budget_raw:
        ai_covered.append("бюджет")
    if analysis.objections:
        ai_covered.append("возражения клиента")
    if analysis.strong_intent_signals:
        ai_covered.append("намерение купить")
    elif analysis.positive_signals:
        ai_covered.append("интерес к предложению")
    if analysis.key_topic:
        ai_covered.append(analysis.key_topic)

    return ManagerBrief(
        customer_intent=intent_phrase,
        temperature=temperature,
        objection=primary_objection,
        next_best_action=next_action,
        ai_covered=list(dict.fromkeys(ai_covered)),  # deduplicate, preserve order
        recommended_first_line=first_line,
        quality=analysis.quality,
    )


def _build_whisper_brief(analysis: CallAnalysis) -> WhisperBrief:
    """
    Build WhisperBrief: ≤150 chars, word-boundary truncation.

    Priority: intent → barrier → action.
    Never asks manager to "find out" what customer wants — that is the AI's job.
    """
    _MAX_WHISPER = 150

    # Intent phrase
    if analysis.strong_intent_signals:
        intent = f"Готов: {analysis.strong_intent_signals[0]}"
    elif analysis.positive_signals:
        intent = f"Интерес: {analysis.positive_signals[0]}"
    elif analysis.quality.data_quality == "empty":
        text = "Клиент на линии."
        return WhisperBrief(text=text, quality=analysis.quality)
    else:
        intent = "Клиент выслушал предложение"

    # Barrier phrase
    barrier = f"Барьер: {analysis.objections[0]}" if analysis.objections else None

    # Action phrase
    if analysis.strong_intent_signals:
        action = "Оформляйте!"
    elif analysis.objections:
        price_objs = {"дорого", "дорогновато", "дороговато", "нет денег"}
        if set(analysis.objections) & price_objs:
            action = "Предложите рассрочку."
        else:
            action = "Не давите, слушайте."
    else:
        action = "Уточните потребности."

    # Assemble with priority
    if barrier:
        text = f"{intent}. {barrier}. {action}"
    else:
        text = f"{intent}. {action}"

    text = _truncate_at_word_boundary(text, _MAX_WHISPER)
    return WhisperBrief(text=text, quality=analysis.quality)


def _build_call_outcome(analysis: CallAnalysis) -> CallOutcome:
    """Build CallOutcome for DB/CRM persistence."""
    key_facts: list[str] = []
    if analysis.budget_normalized:
        key_facts.append(f"Бюджет: {analysis.budget_normalized}")
    if analysis.strong_intent_signals:
        key_facts.append(f"Готовность: {analysis.strong_intent_signals[0]}")
    if analysis.objections:
        key_facts.append(f"Возражение: {analysis.objections[0]}")
    if analysis.positive_signals:
        key_facts.append(f"Интерес: {analysis.positive_signals[0]}")
    if analysis.key_topic:
        key_facts.append(f"Тема: {analysis.key_topic}")

    return CallOutcome(
        intent=analysis.key_topic,
        sentiment=analysis.sentiment,
        confidence=analysis.quality.confidence,
        budget=analysis.budget_normalized,
        key_facts=key_facts[:5],
        basis=analysis.quality.basis,
    )


def _analysis_to_call_summary(analysis: CallAnalysis) -> "CallSummary":
    """Map CallAnalysis → CallSummary for backward-compat path."""
    return CallSummary(
        budget=analysis.budget_normalized or analysis.budget_raw,
        objections=list(analysis.objections),
        positive_signals=list(analysis.positive_signals),
        key_topic=analysis.key_topic,
        sentiment=analysis.sentiment,
    )


def _entries_to_text(entries: list[TranscriptEntry]) -> str:
    """Serialize transcript to plain text for LLM prompt."""
    lines = []
    for e in entries:
        if e.role in (TranscriptRole.SYSTEM, TranscriptRole.TOOL):
            continue
        role_str = "Клиент" if e.role == TranscriptRole.USER else "AI"
        lines.append(f"{role_str}: {e.text}")
    return "\n".join(lines)


# ── Public SummaryService ──────────────────────────────────────────────────────

class SummaryService:
    """
    Hybrid call summary and whisper generation.
    Stateless — safe to instantiate once and reuse.

    Backward-compatible public API (called by TransferService, unchanged):
        generate_summary(entries) → CallSummary
        generate_whisper(summary) → str

    New async API (for future callers or upgraded TransferService):
        analyze(entries) → CallAnalysis
        build_manager_brief(analysis) → ManagerBrief
        build_whisper_brief(analysis) → WhisperBrief
        build_call_outcome(analysis) → CallOutcome
    """

    # ── Backward-compatible API ────────────────────────────────────────────

    def generate_summary(
        self, entries: list[TranscriptEntry]
    ) -> CallSummary:
        """
        Scan transcript entries and produce a CallSummary.
        Synchronous, rule-based only. key_topic is now populated.
        Backward-compatible signature for TransferService.
        """
        normalized = _normalize(entries)
        analysis = _extract_rules(normalized)
        return _analysis_to_call_summary(analysis)

    def generate_whisper(self, summary: CallSummary) -> str:
        """
        Generate a whisper string from a CallSummary.
        ≤150 chars, word-boundary truncation.
        Backward-compatible signature for TransferService.
        """
        # Reconstruct a minimal CallAnalysis from CallSummary
        analysis = CallAnalysis(
            budget_normalized=summary.budget,
            budget_raw=summary.budget,
            objections=list(summary.objections),
            positive_signals=list(summary.positive_signals),
            sentiment=summary.sentiment,
            key_topic=summary.key_topic,
        )
        # Minimal quality (word_count unknown in this path)
        analysis.quality = SummaryQuality.from_word_count(
            word_count=50 if (summary.objections or summary.positive_signals or summary.budget) else 0,
            basis="rules",
        )
        brief = _build_whisper_brief(analysis)
        return brief.text

    # ── New async pipeline ─────────────────────────────────────────────────

    async def analyze(
        self, entries: list[TranscriptEntry]
    ) -> CallAnalysis:
        """
        Full pipeline: normalize → extract rules → optional LLM enhancement.
        Async — suitable for background tasks and upgraded TransferService.
        """
        from app.core.config import settings
        normalized = _normalize(entries)
        analysis = _extract_rules(normalized)
        if settings.summary_llm_enabled:
            full_text = _entries_to_text(entries)
            analysis = await _enhance_with_llm(analysis, full_text)
        return analysis

    def analyze_sync(self, entries: list[TranscriptEntry]) -> CallAnalysis:
        """
        Synchronous rule-only pipeline. Same as analyze() without LLM stage.
        For use in synchronous contexts or when LLM is not configured.
        """
        normalized = _normalize(entries)
        return _extract_rules(normalized)

    def build_manager_brief(self, analysis: CallAnalysis) -> ManagerBrief:
        return _build_manager_brief(analysis)

    def build_whisper_brief(self, analysis: CallAnalysis) -> WhisperBrief:
        return _build_whisper_brief(analysis)

    def build_call_outcome(self, analysis: CallAnalysis) -> CallOutcome:
        return _build_call_outcome(analysis)
