import asyncio
import logging

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import ErrorEvent
from aiohttp import web

from bot.config import config
from bot.db.session import init_db
from bot.fsm_storage import storage
from bot.handlers import admin, customer
from bot.middlewares import ForceJoinMiddleware
from bot.services.sms_webhook import register_sms_webhook


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger(__name__)


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=storage)

    # Force join
    force_join = ForceJoinMiddleware()

    dp.message.outer_middleware(force_join)
    dp.callback_query.outer_middleware(force_join)

    # Routers
    dp.include_router(admin.router)
    dp.include_router(customer.router)

    @dp.errors()
    async def handle_errors(event: ErrorEvent) -> bool:
        exception = event.exception

        if (
            isinstance(exception, TelegramBadRequest)
            and "message is not modified" in str(exception)
        ):
            callback = event.update.callback_query

            if callback is not None:
                try:
                    await callback.answer()
                except Exception:
                    pass

            return True

        logger.exception(
            "Unhandled Telegram update error: %s",
            exception,
        )

        return False

    return dp


async def run_http_server(bot: Bot) -> web.AppRunner:
    """
    HTTP server барои Render + SMS webhook.
    Telegram update-ҳо тавассути polling меоянд.
    """

    app = web.Application()

    async def health(_request: web.Request) -> web.Response:
        return web.Response(
            text="ALMAZSHOP BOT OK",
            status=200,
        )

    async def health_json(_request: web.Request) -> web.Response:
        return web.json_response(
            {
                "status": "ok",
                "bot": "ALMAZSHOP",
                "telegram": "polling",
            }
        )

    app.router.add_get("/", health)
    app.router.add_get("/health", health_json)

    # SMS/payment webhook remains available
    register_sms_webhook(app, bot)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(
        runner,
        host="0.0.0.0",
        port=config.port,
    )

    await site.start()

    logger.info(
        "HTTP SERVER LISTENING on port %s",
        config.port,
    )

    if config.public_url:
        logger.info(
            "PUBLIC URL: %s",
            config.public_url.rstrip("/"),
        )

    return runner


async def self_ping_loop(
    url: str,
    interval_seconds: int,
) -> None:
    if not url or interval_seconds <= 0:
        return

    timeout = aiohttp.ClientTimeout(total=15)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        while True:
            await asyncio.sleep(interval_seconds)

            try:
                async with session.get(url) as response:
                    logger.info(
                        "SELF-PING %s -> HTTP %s",
                        url,
                        response.status,
                    )

            except asyncio.CancelledError:
                raise

            except Exception as exc:
                logger.warning(
                    "SELF-PING failed: %s",
                    exc,
                )


async def run_polling(bot: Bot, dp: Dispatcher) -> None:
    """
    Main Telegram connection.

    IMPORTANT:
    Webhook is removed first.
    Telegram updates then arrive through long polling.
    """

    # Remove ANY old webhook.
    await bot.delete_webhook(
        drop_pending_updates=True,
    )

    me = await bot.get_me()

    logger.info(
        "TELEGRAM BOT: @%s (id=%s)",
        me.username,
        me.id,
    )

    logger.info(
        "TELEGRAM MODE: LONG POLLING",
    )

    logger.info(
        "USED UPDATES: %s",
        dp.resolve_used_update_types(),
    )

    try:
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
            handle_signals=True,
        )

    finally:
        logger.info("Stopping Telegram polling...")


async def main() -> None:
    if not config.bot_token:
        raise RuntimeError(
            "BOT_TOKEN is not set. "
            "Set BOT_TOKEN in Render Environment."
        )

    # Database
    await init_db()

    # Telegram bot
    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
        ),
    )

    # Dispatcher
    dp = build_dispatcher()

    http_runner = None
    ping_task = None

    try:
        # Render HTTP server
        http_runner = await run_http_server(bot)

        # Optional keepalive
        if (
            config.public_url
            and config.keepalive_ping_seconds > 0
        ):
            ping_task = asyncio.create_task(
                self_ping_loop(
                    config.public_url.rstrip("/"),
                    config.keepalive_ping_seconds,
                )
            )

            logger.info(
                "SELF-PING ENABLED: every %s seconds",
                config.keepalive_ping_seconds,
            )

        # Telegram polling
        await run_polling(bot, dp)

    finally:
        if ping_task is not None:
            ping_task.cancel()

            try:
                await ping_task
            except asyncio.CancelledError:
                pass

        if http_runner is not None:
            await http_runner.cleanup()

        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
