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


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger(__name__)


# ============================================================
# DISPATCHER
# ============================================================

def build_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=storage)

    # --------------------------------------------------------
    # Force Join middleware
    # --------------------------------------------------------

    force_join = ForceJoinMiddleware()

    dp.message.outer_middleware(force_join)
    dp.callback_query.outer_middleware(force_join)

    # --------------------------------------------------------
    # Routers
    # --------------------------------------------------------

    dp.include_router(admin.router)
    dp.include_router(customer.router)

    # --------------------------------------------------------
    # Error handler
    # --------------------------------------------------------

    @dp.errors()
    async def handle_stale_edit(event: ErrorEvent) -> bool:
        """
        Ignore Telegram's:
        'message is not modified'

        This prevents callback buttons from getting stuck.
        """

        if (
            isinstance(event.exception, TelegramBadRequest)
            and "message is not modified" in str(event.exception)
        ):
            callback = event.update.callback_query

            if callback is not None:
                try:
                    await callback.answer()
                except Exception:
                    pass

            return True

        logger.exception(
            "Unhandled Telegram error: %s",
            event.exception,
        )

        return False

    return dp


# ============================================================
# HEALTH SERVER FOR RENDER
# ============================================================

async def create_http_server(bot: Bot):
    """
    Render Web Service needs an HTTP server listening on $PORT.

    Telegram bot itself works through polling.
    This server exists so Render sees the service as healthy.
    """

    app = web.Application()

    # --------------------------------------------------------
    # Health endpoint
    # --------------------------------------------------------

    async def health(_request: web.Request) -> web.Response:
        return web.Response(
            text="OK",
            status=200,
            content_type="text/plain",
        )

    app.router.add_get("/health", health)

    # --------------------------------------------------------
    # Root endpoint
    # --------------------------------------------------------

    async def root(_request: web.Request) -> web.Response:
        return web.Response(
            text="ALMAZSHOP Telegram Bot is running.",
            status=200,
            content_type="text/plain",
        )

    app.router.add_get("/", root)

    # --------------------------------------------------------
    # SMS webhook
    # --------------------------------------------------------

    register_sms_webhook(app, bot)

    # --------------------------------------------------------
    # Start server
    # --------------------------------------------------------

    runner = web.AppRunner(app)
    await runner.setup()

    port = config.port

    site = web.TCPSite(
        runner,
        host="0.0.0.0",
        port=port,
    )

    await site.start()

    logger.info(
        "HTTP server listening on 0.0.0.0:%s",
        port,
    )

    logger.info(
        "Health endpoint: /health"
    )

    logger.info(
        "Telegram mode: POLLING"
    )

    return runner


# ============================================================
# SELF PING
# ============================================================

async def _self_ping_loop(
    url: str,
    interval_seconds: int,
) -> None:
    """
    Keep Render service active by requesting /health.

    Important:
    We ping /health, NOT /tg-webhook and NOT the root URL.
    """

    if not url:
        logger.warning(
            "PUBLIC_URL is empty. Self-ping disabled."
        )
        return

    health_url = url.rstrip("/") + "/health"

    logger.info(
        "Self-ping enabled: %s every %ss",
        health_url,
        interval_seconds,
    )

    timeout = aiohttp.ClientTimeout(total=10)

    async with aiohttp.ClientSession(timeout=timeout) as session:

        while True:

            await asyncio.sleep(interval_seconds)

            try:

                async with session.get(health_url) as response:

                    logger.info(
                        "Self-ping %s -> HTTP %s",
                        health_url,
                        response.status,
                    )

            except asyncio.CancelledError:
                raise

            except Exception as exc:

                logger.warning(
                    "Self-ping failed: %s",
                    exc,
                )


# ============================================================
# POLLING MODE
# ============================================================

async def run_polling(
    bot: Bot,
    dp: Dispatcher,
) -> None:
    """
    Production mode for Render.

    Webhook is completely disabled.
    Telegram updates are received using long polling.

    At the same time an aiohttp server listens on Render's port,
    so Render keeps the Web Service alive.
    """

    # --------------------------------------------------------
    # VERY IMPORTANT:
    # Remove old Telegram webhook.
    # --------------------------------------------------------

    logger.info(
        "Removing existing Telegram webhook..."
    )

    try:

        await bot.delete_webhook(
            drop_pending_updates=True
        )

        logger.info(
            "Telegram webhook removed successfully."
        )

    except Exception as exc:

        logger.error(
            "Could not delete Telegram webhook: %s",
            exc,
        )

    # --------------------------------------------------------
    # Verify webhook status
    # --------------------------------------------------------

    try:

        webhook_info = await bot.get_webhook_info()

        logger.info(
            "Telegram webhook URL after cleanup: %s",
            webhook_info.url or "(empty)",
        )

        logger.info(
            "Pending updates: %s",
            webhook_info.pending_update_count,
        )

    except Exception as exc:

        logger.warning(
            "Could not read webhook info: %s",
            exc,
        )

    # --------------------------------------------------------
    # Start Render HTTP server
    # --------------------------------------------------------

    runner = await create_http_server(bot)

    # --------------------------------------------------------
    # Self-ping
    # --------------------------------------------------------

    self_ping_task = None

    if config.public_url and config.keepalive_ping_seconds > 0:

        self_ping_task = asyncio.create_task(
            _self_ping_loop(
                config.public_url,
                config.keepalive_ping_seconds,
            )
        )

    # --------------------------------------------------------
    # Start Telegram polling
    # --------------------------------------------------------

    logger.info(
        "Starting Telegram polling..."
    )

    try:

        await dp.start_polling(
            bot
        )

    finally:

        logger.info(
            "Stopping Telegram polling..."
        )

        # Cancel self-ping
        if self_ping_task is not None:

            self_ping_task.cancel()

            try:
                await self_ping_task
            except asyncio.CancelledError:
                pass

        # Cleanup HTTP server
        try:

            await runner.cleanup()

        except Exception as exc:

            logger.warning(
                "HTTP server cleanup failed: %s",
                exc,
            )


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

async def _init_db_with_retry(
    max_attempts: int = 5,
) -> None:
    """
    Initialize database with retry.
    """

    delay = 2

    for attempt in range(
        1,
        max_attempts + 1,
    ):

        try:

            logger.info(
                "Initializing database..."
            )

            await init_db()

            logger.info(
                "Database initialized successfully."
            )

            return

        except Exception as exc:

            if attempt == max_attempts:

                logger.error(
                    "init_db() failed after %s attempts: %s",
                    max_attempts,
                    exc,
                )

                raise

            logger.warning(
                "init_db() failed "
                "(attempt %s/%s): %s "
                "— retrying in %ss",
                attempt,
                max_attempts,
                exc,
                delay,
            )

            await asyncio.sleep(delay)

            delay = min(
                delay * 2,
                30,
            )


# ============================================================
# MAIN
# ============================================================

async def main() -> None:

    # --------------------------------------------------------
    # BOT TOKEN
    # --------------------------------------------------------

    if not config.bot_token:

        raise RuntimeError(
            "BOT_TOKEN is not set. "
            "Add BOT_TOKEN in Render Environment."
        )

    logger.info(
        "Starting ALMAZSHOP Telegram Bot..."
    )

    # --------------------------------------------------------
    # DATABASE
    # --------------------------------------------------------

    await _init_db_with_retry()

    # --------------------------------------------------------
    # BOT
    # --------------------------------------------------------

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML
        ),
    )

    # --------------------------------------------------------
    # DISPATCHER
    # --------------------------------------------------------

    dp = build_dispatcher()

    # --------------------------------------------------------
    # ALWAYS USE POLLING
    #
    # No PUBLIC_URL check here.
    # No webhook mode.
    # --------------------------------------------------------

    try:

        await run_polling(
            bot,
            dp,
        )

    finally:

        logger.info(
            "Closing Telegram bot session..."
        )

        await bot.session.close()

        logger.info(
            "ALMAZSHOP bot stopped."
        )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    asyncio.run(main())
