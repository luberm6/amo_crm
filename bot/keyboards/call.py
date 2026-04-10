"""
Telegram inline keyboards and live-card text formatter for call management.
Card format (mobile-optimised, HTML):
  🔵 <b>+79991234567</b>
  IN_PROGRESS · vapi · 2м 15с
  💬 <i>Разговор:</i>
  🤖 Здравствуйте! Меня зовут Алекс...
  👤 Да, слушаю
  🤖 Расскажите о вашем бюджете?
  👤 Около 50 тысяч рублей
  🤖 Отлично! У нас есть несколько...
  📌 <i>Директива:</i> Уточни бюджет
  <code>abc12345-1234-…</code>
Buttons (2×3 grid for active call):
  [💰 Бюджет] [🤝 Мягче]
  [🎯 Дожим]  [👤 Менеджер]
  [🔄 Обновить] [🛑 Стоп]
Callback data format (max 64 bytes):
  steer|{preset}|{call_id}     e.g. steer|budget|550e8400-…  (≤51 bytes)
  stop|{call_id}               (≤41 bytes)
  refresh|{call_id}            (≤44 bytes)
  open|{call_id}               (≤41 bytes)  ← from /active list
"""
from __future__ import annotations
from typing import Optional
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
# ── Steering presets ──────────────────────────────────────────────────────────
# Keys must be ≤ 7 chars so callback data stays within 64 bytes.
STEERING_PRESETS: dict[str, str] = {
    "budget": "Мягко уточни бюджет клиента — спроси, на какую сумму он рассчитывает",
    "soft": "Снизь давление, дай клиенту выговориться и почувствовать себя услышанным",
    "close": "Усиль дожим: напомни о срочности предложения и мягко подтолкни к решению",
    "manager": "Предложи клиенту переключиться на живого специалиста для детального разговора",
}
# ── Visual helpers ────────────────────────────────────────────────────────────
STATUS_EMOJI: dict[str, str] = {
    "CREATED": "🆕",
    "QUEUED": "⏳",
    "DIALING": "📡",
    "RINGING": "🔔",
    "IN_PROGRESS": "🔵",
    "NEEDS_TRANSFER": "⚡",
    "TRANSFERRING": "🔄",
    "MANAGER_BRIEFING": "👤",
    "CONNECTED_TO_MANAGER": "🟢",
    "COMPLETED": "✅",
    "FAILED": "❌",
    "STOPPED": "🛑",
}

# Human-readable transfer phase labels for bot display
TRANSFER_PHASE_LABEL: dict[str, str] = {
    "INITIATED": "🔍 Ищем менеджера…",
    "CALLING_MANAGER": "📞 Звоним менеджеру…",
    "BRIEFING": "📋 Идёт briefing…",
    "CONNECTED": "🟢 Перевод выполнен",
    "FAILED_NO_ANSWER": "📵 Менеджер не ответил",
    "FAILED_ALL_UNAVAILABLE": "⚠️ Нет доступных менеджеров",
    "CALLER_DROPPED": "📴 Клиент сбросил трубку",
    "BRIDGE_FAILED": "🔌 Соединение не удалось",
    "TIMED_OUT": "⏱ Превышено время ожидания",
}
ROLE_ICON: dict[str, str] = {
    "assistant": "🤖",
    "user": "👤",
    "system": "⚙️",
    "tool": "🔧",
}
def _fmt_duration(seconds: Optional[int]) -> str:
    if seconds is None:
        return "—"
    m, s = divmod(seconds, 60)
    return f"{m}м {s}с" if m else f"{s}с"
def format_call_card(card: dict) -> str:
    """
    Render a CallCardView dict to HTML text for the Telegram live card.
    Keeps lines short for comfortable mobile reading.
    """
    status = card["status"]
    emoji = STATUS_EMOJI.get(status, "•")
    duration = _fmt_duration(card.get("duration_seconds"))
    lines: list[str] = [
        f"{emoji} <b>{card['phone']}</b>",
        f"<b>{status}</b> · {card['mode']} · {duration}",
    ]
    # Show detailed transfer phase when a transfer is in progress or ended
    transfer_status = card.get("transfer_status")
    if transfer_status and transfer_status in TRANSFER_PHASE_LABEL:
        lines.append(TRANSFER_PHASE_LABEL[transfer_status])
    if card.get("transfer_failure_reason"):
        reason = card["transfer_failure_reason"]
        if len(reason) > 120:
            reason = reason[:117] + "…"
        lines.append(f"<i>Причина: {reason}</i>")
    # ── Transcript tail ───────────────────────────────────────────────────────
    entries = card.get("transcript_tail", [])
    if entries:
        lines.append("")
        lines.append("💬 <i>Разговор:</i>")
        for e in entries:
            icon = ROLE_ICON.get(e["role"], "•")
            text = e["text"]
            # Trim long utterances for mobile readability
            if len(text) > 110:
                text = text[:107] + "…"
            lines.append(f"{icon} {text}")
    else:
        lines.append("")
        lines.append("<i>Транскрипт пока пуст</i>")
    # ── Last instruction ──────────────────────────────────────────────────────
    if card.get("last_instruction"):
        lines.append("")
        lines.append(f"📌 <i>Директива:</i> {card['last_instruction']}")
    # ── Summary / sentiment (post-call) ───────────────────────────────────────
    if card.get("summary"):
        lines.append("")
        summary = card["summary"]
        if len(summary) > 160:
            summary = summary[:157] + "…"
        lines.append(f"📋 <i>Итог:</i> {summary}")
    if card.get("sentiment"):
        lines.append(f"💡 Настрой: {card['sentiment']}")
    # ── Footer: call ID ───────────────────────────────────────────────────────
    lines.append("")
    lines.append(f"<code>{card['id']}</code>")
    return "\n".join(lines)
def build_active_keyboard(calls: list[dict]) -> InlineKeyboardMarkup:
    """
    Build a keyboard for the /active list.
    Each active call gets an "Open" button that triggers the live card.
    """
    builder = InlineKeyboardBuilder()
    for call in calls:
        emoji = STATUS_EMOJI.get(call["status"], "•")
        phone_short = call["phone"][-7:]  # last 7 digits fit in button
        label = f"{emoji} …{phone_short}  {call['status']}"
        builder.button(
            text=label,
            callback_data=f"open|{call['id']}",
        )
    builder.adjust(1)
    return builder.as_markup()
# Call statuses where warm transfer is in progress — show reduced keyboard
_TRANSFER_IN_PROGRESS_STATUSES = {
    "TRANSFERRING",
    "MANAGER_BRIEFING",
    "CONNECTED_TO_MANAGER",
}


def build_call_card_keyboard(
    call_id: str,
    is_active: bool,
    status: str = "",
) -> InlineKeyboardMarkup:
    """
    Build the inline keyboard for the live call card.

    Active call (normal): 7 buttons — 2+2+2+1 grid
      [💰 Бюджет]    [🤝 Мягче]
      [🎯 Дожим]     [👤 Менеджер]
      [🔀 Перевести] [🔄 Обновить]
      [🛑 Стоп]

    Transfer in progress (TRANSFERRING / MANAGER_BRIEFING / CONNECTED_TO_MANAGER):
      [🔄 Обновить]
      [🛑 Стоп]

    Ended call: refresh only.

    Callback `transfer|{call_id}` = 9 + 36 = 45 bytes — within 64-byte limit.
    """
    builder = InlineKeyboardBuilder()
    if not is_active:
        builder.button(text="🔄 Обновить", callback_data=f"refresh|{call_id}")
        builder.adjust(1)
        return builder.as_markup()

    if status in _TRANSFER_IN_PROGRESS_STATUSES:
        # Reduced layout during transfer — no steering, just monitor and stop
        builder.button(text="🔄 Обновить", callback_data=f"refresh|{call_id}")
        builder.button(text="🛑 Стоп", callback_data=f"stop|{call_id}")
        builder.adjust(1)
        return builder.as_markup()

    # Full active-call layout
    builder.button(text="💰 Бюджет", callback_data=f"steer|budget|{call_id}")
    builder.button(text="🤝 Мягче", callback_data=f"steer|soft|{call_id}")
    builder.button(text="🎯 Дожим", callback_data=f"steer|close|{call_id}")
    builder.button(text="👤 Менеджер", callback_data=f"steer|manager|{call_id}")
    builder.button(text="🔀 Перевести", callback_data=f"transfer|{call_id}")
    builder.button(text="🔄 Обновить", callback_data=f"refresh|{call_id}")
    builder.button(text="🛑 Стоп", callback_data=f"stop|{call_id}")
    builder.adjust(2, 2, 2, 1)
    return builder.as_markup()