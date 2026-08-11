import asyncio
import logging
import os

import aiohttp
from aiohttp import web

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    BotCommand,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    ErrorEvent,
)

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
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger(__name__)


# ============================================================
# DISPATCHER
# ============================================================

def build_dispatcher() -> Dispatcher:

    dp = Dispatcher(storage=storage)

    # Force Join middleware
    force_join = ForceJoinMiddleware()

    dp.message.outer_middleware(force_join)
    dp.callback_query.outer_middleware(force_join)

    # Routers
    dp.include_router(admin.router)
    dp.include_router(customer.router)

    # Telegram "message is not modified" error
    @dp.errors()
    async def handle_errors(event: ErrorEvent) -> bool:

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

        return False

    return dp


# ============================================================
# BOT COMMANDS
# ============================================================

async def configure_bot_commands(bot: Bot) -> None:

    # Normal users
    await bot.set_my_commands(
        [
            BotCommand(
                command="start",
                description="Асосӣ меню",
            )
        ],
        scope=BotCommandScopeDefault(),
    )

    # Admin commands
    admin_commands = [
        BotCommand(
            command="start",
            description="Асосӣ меню",
        ),
        BotCommand(
            command="admin",
            description="Панели админ",
        ),
    ]

    for admin_id in config.admin_user_ids:

        try:

            await bot.set_my_commands(
                admin_commands,
                scope=BotCommandScopeChat(
                    chat_id=admin_id
                ),
            )

        except Exception as exc:

            logger.warning(
                "Could not set admin commands for %s: %s",
                admin_id,
                exc,
            )


# ============================================================
# KEEP ALIVE
# ============================================================

async def self_ping_loop(
    public_url: str,
    interval_seconds: int = 600,
) -> None:

    health_url = (
        public_url.rstrip("/")
        + "/health"
    )

    timeout = aiohttp.ClientTimeout(
        total=15
    )

    async with aiohttp.ClientSession(
        timeout=timeout
    ) as session:

        while True:

            try:

                await asyncio.sleep(
                    interval_seconds
                )

                async with session.get(
                    health_url
                ) as response:

                    logger.info(
                        "KEEPALIVE -> %s -> HTTP %s",
                        health_url,
                        response.status,
                    )

            except asyncio.CancelledError:

                raise

            except Exception as exc:

                logger.warning(
                    "KEEPALIVE ERROR: %s",
                    exc,
                )


# ============================================================
# WEBHOOK MODE
# ============================================================

async def run_webhook(
    bot: Bot,
    dp: Dispatcher,
) -> None:

    from aiogram.webhook.aiohttp_server import (
        SimpleRequestHandler,
        setup_application,
    )

    # --------------------------------------------------------
    # PUBLIC URL
    # --------------------------------------------------------

    public_url = (
        config.public_url
        .strip()
        .rstrip("/")
    )

    if not public_url:

        raise RuntimeError(
            "PUBLIC_URL is empty."
        )

    # --------------------------------------------------------
    # IMPORTANT:
    # DO NOT TAKE WEBHOOK PATH FROM OLD CONFIG.
    # FIXED PATH:
    # --------------------------------------------------------

    webhook_path = "/tg-webhook"

    webhook_url = (
        public_url
        + webhook_path
    )

    logger.info(
        "=============================================="
    )

    logger.info(
        "PUBLIC URL: %s",
        public_url,
    )

    logger.info(
        "WEBHOOK PATH: %s",
        webhook_path,
    )

    logger.info(
        "WEBHOOK URL: %s",
        webhook_url,
    )

    logger.info(
        "=============================================="
    )

    # --------------------------------------------------------
    # AIOHTTP APP
    # --------------------------------------------------------

    app = web.Application()

    # --------------------------------------------------------
    # HEALTH
    # --------------------------------------------------------

    async def health(
        _request: web.Request,
    ) -> web.Response:

        return web.Response(
            text="OK",
            status=200,
        )

    # Health
    app.router.add_get(
        "/health",
        health,
    )

    # Root
    app.router.add_get(
        "/",
        health,
    )

    # --------------------------------------------------------
    # SMS WEBHOOK
    # --------------------------------------------------------

    try:

        register_sms_webhook(
            app,
            bot,
        )

    except Exception as exc:

        logger.warning(
            "SMS webhook registration failed: %s",
            exc,
        )

    # --------------------------------------------------------
    # TELEGRAM WEBHOOK
    # --------------------------------------------------------

    webhook_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=(
            config.telegram_webhook_secret
            or None
        ),
    )

    # VERY IMPORTANT
    # This creates:
    #
    # POST /tg-webhook
    #

    webhook_handler.register(
        app,
        path=webhook_path,
    )

    # --------------------------------------------------------
    # AIOGRAM APPLICATION
    # --------------------------------------------------------

    setup_application(
        app,
        dp,
        bot=bot,
    )

    # --------------------------------------------------------
    # RENDER PORT
    # --------------------------------------------------------

    # Render gives the port through $PORT.
    # Never rely only on config.port.

    render_port = os.getenv(
        "PORT"
    )

    if render_port:

        port = int(
            render_port
        )

    else:

        port = int(
            config.port
        )

    logger.info(
        "RENDER PORT: %s",
        port,
    )

    # --------------------------------------------------------
    # START HTTP SERVER FIRST
    # --------------------------------------------------------

    runner = web.AppRunner(
        app
    )

    await runner.setup()

    site = web.TCPSite(
        runner,
        host="0.0.0.0",
        port=port,
    )

    await site.start()

    logger.info(
        "=============================================="
    )

    logger.info(
        "HTTP SERVER STARTED"
    )

    logger.info(
        "LISTENING: 0.0.0.0:%s",
        port,
    )

    logger.info(
        "HEALTH: %s/health",
        public_url,
    )

    logger.info(
        "WEBHOOK ROUTE: POST %s",
        webhook_path,
    )

    logger.info(
        "=============================================="
    )

    # --------------------------------------------------------
    # SET TELEGRAM WEBHOOK AFTER SERVER STARTED
    # --------------------------------------------------------

    try:

        await bot.delete_webhook(
            drop_pending_updates=True
        )

        await asyncio.sleep(1)

        await bot.set_webhook(
            url=webhook_url,
            secret_token=(
                config.telegram_webhook_secret
                or None
            ),
            drop_pending_updates=True,
        )

        logger.info(
            "=============================================="
        )

        logger.info(
            "TELEGRAM WEBHOOK SET SUCCESSFULLY"
        )

        logger.info(
            "WEBHOOK: %s",
            webhook_url,
        )

        logger.info(
            "=============================================="
        )

    except Exception:

        logger.exception(
            "FAILED TO SET TELEGRAM WEBHOOK"
        )

        raise

    # --------------------------------------------------------
    # VERIFY TELEGRAM WEBHOOK
    # --------------------------------------------------------

    try:

        webhook_info = (
            await bot.get_webhook_info()
        )

        logger.info(
            "TELEGRAM WEBHOOK CHECK"
        )

        logger.info(
            "URL: %s",
            webhook_info.url,
        )

        logger.info(
            "PENDING: %s",
            webhook_info.pending_update_count,
        )

        logger.info(
            "LAST ERROR: %s",
            webhook_info.last_error_message,
        )

    except Exception as exc:

        logger.warning(
            "Could not check webhook: %s",
            exc,
        )

    # --------------------------------------------------------
    # KEEP ALIVE EVERY 10 MINUTES
    # --------------------------------------------------------

    asyncio.create_task(
        self_ping_loop(
            public_url,
            600,
        )
    )

    logger.info(
        "KEEPALIVE ENABLED: every 600 seconds"
    )

    # --------------------------------------------------------
    # BOT RUNNING
    # --------------------------------------------------------

    logger.info(
        "=============================================="
    )

    logger.info(
        "BOT IS RUNNING"
    )

    logger.info(
        "WEBHOOK MODE ACTIVE"
    )

    logger.info(
        "=============================================="
    )

    # Keep process alive
    await asyncio.Event().wait()


# ============================================================
# POLLING
# ============================================================

async def run_polling(
    bot: Bot,
    dp: Dispatcher,
) -> None:

    logger.info(
        "STARTING POLLING MODE"
    )

    await bot.delete_webhook(
        drop_pending_updates=True
    )

    await dp.start_polling(
        bot
    )


# ============================================================
# DATABASE
# ============================================================

async def init_db_with_retry(
    max_attempts: int = 5,
) -> None:

    delay = 2

    for attempt in range(
        1,
        max_attempts + 1,
    ):

        try:

            await init_db()

            logger.info(
                "DATABASE INITIALIZED"
            )

            return

        except Exception as exc:

            if attempt == max_attempts:

                logger.exception(
                    "DATABASE FAILED AFTER %s ATTEMPTS",
                    max_attempts,
                )

                raise

            logger.warning(
                "DATABASE ERROR %s/%s: %s",
                attempt,
                max_attempts,
                exc,
            )

            await asyncio.sleep(
                delay
            )

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
            "BOT_TOKEN is not set."
        )

    # --------------------------------------------------------
    # DATABASE
    # --------------------------------------------------------

    await init_db_with_retry()

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
    # COMMANDS
    # --------------------------------------------------------

    await configure_bot_commands(
        bot
    )

    # --------------------------------------------------------
    # WEBHOOK
    # --------------------------------------------------------

    if config.public_url.strip():

        logger.info(
            "PUBLIC_URL FOUND"
        )

        await run_webhook(
            bot,
            dp,
        )

    # --------------------------------------------------------
    # POLLING
    # --------------------------------------------------------

    else:

        logger.info(
            "PUBLIC_URL EMPTY"
        )

        await run_polling(
            bot,
            dp,
        )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    try:

        asyncio.run(
            main()
        )

    except KeyboardInterrupt:

        logger.info(
            "BOT STOPPED"
        )

    except Exception:

        logger.exception(
            "FATAL ERROR"
        )

        raise
