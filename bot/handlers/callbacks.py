"""
Inline button callback handlers.
All button presses on call cards are handled here.
Each handler:
  1. Answers the callback (removes Telegram loading spinner)
  2. Executes the action (steer / stop / refresh)
  3. Edits the card message with fresh data from the backend
Callback data format:
  open|{call_id}               — open live card (from /active list)
  refresh|{call_id}            — refresh the card in place
  steer|{preset}|{call_id}     — send a preset steering instruction
  stop|{call_id}               — stop the call
"""
from __future__ import annotations
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery
from app.core.logging import get_logger
from bot.client import ApiError, get_call_card, initiate_transfer, steer_call, stop_call
from bot.keyboards.call import (
    STEERING_PRESETS,
    build_call_card_keyboard,
    format_call_card,
)
log = get_logger(__name__)
router = Router()
# ── Helpers ───────────────────────────────────────────────────────────────────
async def _render_card(call_id: str) -> tuple[str, object]:
    """Fetch fresh card data and build (text, keyboard) tuple."""
    card = await get_call_card(call_id)
    text = format_call_card(card)
    keyboard = build_call_card_keyboard(call_id, card["is_active"], card.get("status", ""))
    return text, keyboard
async def _edit_card(callback: CallbackQuery, call_id: str) -> None:
    """Edit the callback message with a freshly fetched call card."""
    try:
        card = await get_call_card(call_id)
    except ApiError as exc:
        await callback.answer(f"Ошибка: {exc.message}", show_alert=True)
        return
    text = format_call_card(card)
    keyboard = build_call_card_keyboard(call_id, card["is_active"], card.get("status", ""))
    try:
        await callback.message.edit_text(
            text, reply_markup=keyboard, parse_mode="HTML"
        )
    except TelegramBadRequest as exc:
        # "message is not modified" — data hasn't changed, that's fine
        if "not modified" not in str(exc).lower():
            raise
    await callback.answer()
# ── open|{call_id} ────────────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("open|"))
async def cb_open_card(callback: CallbackQuery) -> None:
    """
    Open a live call card as a new message.
    Triggered from /active list buttons.
    """
    call_id = callback.data.split("|", 1)[1]
    await callback.answer()
    try:
        card = await get_call_card(call_id)
    except ApiError as exc:
        await callback.message.answer(f"❌ {exc.message}")
        return
    text = format_call_card(card)
    keyboard = build_call_card_keyboard(call_id, card["is_active"], card.get("status", ""))
    await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
# ── refresh|{call_id} ────────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("refresh|"))
async def cb_refresh(callback: CallbackQuery) -> None:
    """Refresh the live card in place with the latest data from backend."""
    call_id = callback.data.split("|", 1)[1]
    await _edit_card(callback, call_id)
# ── steer|{preset}|{call_id} ─────────────────────────────────────────────────
@router.callback_query(F.data.startswith("steer|"))
async def cb_steer(callback: CallbackQuery) -> None:
    """
    Send a preset steering instruction and refresh the card.
    The issued_by is set to the Telegram user ID so the audit trail
    records which manager sent the directive.
    """
    _, preset, call_id = callback.data.split("|", 2)
    instruction = STEERING_PRESETS.get(preset)
    if not instruction:
        await callback.answer("Неизвестная директива", show_alert=True)
        return
    issued_by = str(callback.from_user.id) if callback.from_user else "telegram"
    # Optimistic feedback — answer immediately, then execute
    await callback.answer("📌 Директива отправлена")
    try:
        await steer_call(call_id, instruction, issued_by)
    except ApiError as exc:
        if exc.status_code == 429:
            await callback.message.answer("⏳ Слишком много запросов. Подожди и попробуй снова.")
        else:
            await callback.message.answer(f"❌ Не удалось отправить директиву: {exc.message}")
        return
    log.info(
        "bot.steer.preset",
        preset=preset,
        call_id=call_id,
        issued_by=issued_by,
    )
    # Refresh card to show updated last_instruction
    await _edit_card(callback, call_id)
# ── stop|{call_id} ───────────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("stop|"))
async def cb_stop(callback: CallbackQuery) -> None:
    """Stop the call and refresh the card to show terminal status."""
    call_id = callback.data.split("|", 1)[1]
    await callback.answer("🛑 Останавливаем звонок…")
    try:
        await stop_call(call_id)
    except ApiError as exc:
        await callback.message.answer(f"❌ Не удалось остановить: {exc.message}")
        return
    log.info("bot.stop", call_id=call_id)
    # Refresh card — should now show STOPPED status without control buttons
    await _edit_card(callback, call_id)
# ── transfer|{call_id} ───────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("transfer|"))
async def cb_transfer(callback: CallbackQuery) -> None:
    """
    Initiate warm transfer to a manager.
    Shows optimistic "Переводим…" feedback, calls backend, then refreshes card.
    On error shows an alert — does NOT refresh (card state unchanged).
    """
    call_id = callback.data.split("|", 1)[1]
    await callback.answer("🔀 Переводим на менеджера…")
    try:
        await initiate_transfer(call_id)
    except ApiError as exc:
        if exc.status_code == 429:
            await callback.message.answer("⏳ Слишком много запросов. Подожди и попробуй снова.")
        else:
            await callback.message.answer(f"❌ Не удалось перевести: {exc.message}")
        return
    log.info("bot.transfer", call_id=call_id)
    # Refresh card — should now show TRANSFERRING or CONNECTED_TO_MANAGER status
    await _edit_card(callback, call_id)