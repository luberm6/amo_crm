"""
Telegram command handlers.
/start                     — welcome + quick help
/help                      — full command reference
/call <phone>              — initiate a new outbound AI call
/active                    — list active calls with Open buttons
/listen <call_id>          — show live call card
/steer <call_id> <text>    — send a custom steering instruction
/stop <call_id>            — stop a specific call
UX principles:
- /listen and /steer always return a fresh live card (no stale data)
- /active shows a compact list with inline Open buttons
- Error messages are human-readable, never stack traces
- Bot does not spam new messages; it prefers card edits where possible
"""
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from app.core.logging import get_logger
from bot.client import ApiError, create_call, get_active_calls, get_call_card, steer_call, stop_call
from bot.keyboards.call import (
    build_active_keyboard,
    build_call_card_keyboard,
    format_call_card,
)
log = get_logger(__name__)
router = Router()
HELP_TEXT = """
<b>🎙 AI Voice Sales Bot</b>
<b>Команды:</b>
/call <code>&lt;номер&gt;</code> — запустить AI-звонок
  Пример: <code>/call +79991234567</code>
/active — активные звонки (с кнопками управления)
/listen <code>&lt;call_id&gt;</code> — live-карточка звонка
/steer <code>&lt;call_id&gt; &lt;директива&gt;</code> — отправить инструкцию AI
  Пример: <code>/steer abc123 Уточни бюджет клиента</code>
/stop <code>&lt;call_id&gt;</code> — остановить звонок
<b>Кнопки на карточке:</b>
💰 Бюджет — спросить о бюджете клиента
🤝 Мягче — снизить давление
🎯 Дожим — усилить закрытие сделки
👤 Менеджер — предложить живого специалиста
🔄 Обновить — обновить карточку
🛑 Стоп — завершить звонок
""".strip()
# ── /start ────────────────────────────────────────────────────────────────────
@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(HELP_TEXT, parse_mode="HTML")
# ── /help ─────────────────────────────────────────────────────────────────────
@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT, parse_mode="HTML")
# ── /call ─────────────────────────────────────────────────────────────────────
@router.message(Command("call"))
async def cmd_call(message: Message) -> None:
    """
    /call <phone>
    Initiates an outbound call. If Vapi is configured, the call starts
    immediately. Otherwise runs against StubEngine (for development).
    After creation, shows the live card with control buttons.
    """
    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2 or not args[1].strip():
        await message.answer(
            "Укажи номер телефона:\n<code>/call +79991234567</code>",
            parse_mode="HTML",
        )
        return
    raw_phone = args[1].strip()
    try:
        call = await create_call(raw_phone)
    except ApiError as exc:
        if exc.status_code == 429:
            await message.answer("⏳ Слишком много запросов. Подожди немного и попробуй снова.")
        else:
            await message.answer(f"❌ {exc.message}")
        return
    call_id = call["id"]
    log.info("bot.call.created", call_id=call_id, phone=call["phone"])
    # Show the live card right away
    try:
        card = await get_call_card(call_id)
    except ApiError:
        # Card fetch failed — show minimal confirmation
        await message.answer(
            f"✅ Звонок создан\n"
            f"ID: <code>{call_id}</code>\n"
            f"Статус: {call['status']}",
            parse_mode="HTML",
        )
        return
    text = format_call_card(card)
    keyboard = build_call_card_keyboard(call_id, card["is_active"])
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
# ── /active ───────────────────────────────────────────────────────────────────
@router.message(Command("active"))
async def cmd_active(message: Message) -> None:
    """
    /active
    Lists all active calls. Each call has an "Open" button that shows
    the live card when pressed.
    """
    try:
        data = await get_active_calls()
    except ApiError as exc:
        await message.answer(f"❌ {exc.message}")
        return
    calls = data.get("items", [])
    total = data.get("total", 0)
    if not calls:
        await message.answer("Активных звонков нет.")
        return
    lines = [f"📋 <b>Активные звонки ({total}):</b>"]
    for i, call in enumerate(calls, 1):
        from bot.keyboards.call import STATUS_EMOJI, _fmt_duration
        emoji = STATUS_EMOJI.get(call["status"], "•")
        dur = _fmt_duration(None)  # /active doesn't include duration — use /listen for that
        lines.append(f"{i}. {emoji} {call['phone']} · {call['status']}")
    keyboard = build_active_keyboard(calls)
    await message.answer(
        "\n".join(lines) + "\n\n<i>Нажми кнопку, чтобы открыть карточку</i>",
        reply_markup=keyboard,
        parse_mode="HTML",
    )
# ── /listen ───────────────────────────────────────────────────────────────────
@router.message(Command("listen"))
async def cmd_listen(message: Message) -> None:
    """
    /listen <call_id>
    Opens the live call card with current status, transcript tail,
    last steering directive, and control buttons.
    Run /listen again or press 🔄 to refresh.
    """
    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2 or not args[1].strip():
        await message.answer(
            "Укажи call_id:\n<code>/listen &lt;call_id&gt;</code>",
            parse_mode="HTML",
        )
        return
    call_id = args[1].strip()
    try:
        card = await get_call_card(call_id)
    except ApiError as exc:
        if exc.status_code == 404:
            await message.answer("❌ Звонок не найден.")
        else:
            await message.answer(f"❌ {exc.message}")
        return
    text = format_call_card(card)
    keyboard = build_call_card_keyboard(call_id, card["is_active"])
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
# ── /steer ────────────────────────────────────────────────────────────────────
@router.message(Command("steer"))
async def cmd_steer(message: Message) -> None:
    """
    /steer <call_id> <instruction text>
    Sends a free-text steering directive to the AI.
    Use this for custom instructions beyond the preset buttons.
    """
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await message.answer(
            "Использование:\n"
            "<code>/steer &lt;call_id&gt; &lt;текст инструкции&gt;</code>\n\n"
            "Пример:\n"
            "<code>/steer abc123 Спроси клиента о его основных болях</code>",
            parse_mode="HTML",
        )
        return
    call_id = parts[1].strip()
    instruction = parts[2].strip()
    if not instruction:
        await message.answer("❌ Инструкция не может быть пустой.")
        return
    issued_by = str(message.from_user.id) if message.from_user else "telegram"
    try:
        await steer_call(call_id, instruction, issued_by)
    except ApiError as exc:
        if exc.status_code == 404:
            await message.answer("❌ Звонок не найден.")
        elif exc.status_code == 429:
            await message.answer("⏳ Слишком много запросов. Подожди немного и попробуй снова.")
        elif exc.status_code == 422:
            await message.answer(f"❌ {exc.message}")
        else:
            await message.answer(f"❌ {exc.message}")
        return
    log.info("bot.steer.custom", call_id=call_id, issued_by=issued_by)
    # Show updated card after steer
    try:
        card = await get_call_card(call_id)
        text = format_call_card(card)
        keyboard = build_call_card_keyboard(call_id, card["is_active"])
        await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
    except ApiError:
        await message.answer("✅ Директива отправлена.")
# ── /stop ─────────────────────────────────────────────────────────────────────
@router.message(Command("stop"))
async def cmd_stop(message: Message) -> None:
    """
    /stop <call_id>
    Terminates the active call immediately. Idempotent — safe to call
    on already-stopped calls.
    """
    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2 or not args[1].strip():
        await message.answer(
            "Укажи call_id:\n<code>/stop &lt;call_id&gt;</code>",
            parse_mode="HTML",
        )
        return
    call_id = args[1].strip()
    try:
        await stop_call(call_id)
    except ApiError as exc:
        if exc.status_code == 404:
            await message.answer("❌ Звонок не найден.")
        else:
            await message.answer(f"❌ {exc.message}")
        return
    log.info("bot.stop.command", call_id=call_id)
    # Show updated card with terminal status (no control buttons)
    try:
        card = await get_call_card(call_id)
        text = format_call_card(card)
        keyboard = build_call_card_keyboard(call_id, card["is_active"])
        await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
    except ApiError:
        await message.answer("🛑 Звонок остановлен.")