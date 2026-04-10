"""
Telegram bot entry point.
Runs in long-polling mode locally.
For production on Render, switch to webhook mode (see README).
Usage:
    python -m bot.main
"""
import asyncio
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from app.core.config import settings
from app.core.logging import get_logger, setup_logging
from bot.handlers import callbacks, commands
log = get_logger(__name__)
async def main() -> None:
    setup_logging()
    if not settings.telegram_bot_token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not set. "
            "Copy .env.example to .env and fill in the token."
        )
    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    # Register callback handlers BEFORE command handlers so button presses
    # are matched before falling through to any catch-all message handler.
    dp.include_router(callbacks.router)
    dp.include_router(commands.router)
    log.info("bot_starting", environment=settings.environment)
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
if __name__ == "__main__":
    asyncio.run(main())