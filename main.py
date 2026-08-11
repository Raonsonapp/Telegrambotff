import asyncio
import logging

import aiohttp
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

    # Force-Join middleware
    force_join = ForceJoinMiddleware()

    dp.message.outer_middleware(force_join)
    dp.callback_query.outer_middleware(force_join)

    # Routers
    dp.include_router(admin.router)
    dp.include_router(customer.router)

    @dp.errors()
    async def handle_errors(event: ErrorEvent) -> bool:
        """Handle harmless Telegram 'message is not modified' errors."""
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


async def configure_bot_commands(bot: Bot) -> None:
    """Configure Telegram command menu."""

    # Default commands
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
                scope=BotCommandScopeChat(chat_id=admin_id),
            )

        except Exception as exc:
            logger.warning(
                "Could not set admin commands for %s: %s",
                admin_id,
                exc,
            )


async def run_polling(
    bot: Bot,
    dp: Dispatcher,
) -> None:
    """Local polling mode."""

    await bot.delete_webhook(drop_pending_updates=True)

    logger.info("Starting Telegram polling...")

    await dp.start_polling(bot)


async def self_ping_loop(
    url: str,
    interval_seconds: int,
) -> None:
    """
    Ping /health every N seconds.

    This keeps the Render free instance receiving HTTP traffic.
    """

    health_url = url.rstrip("/") + "/health"

    timeout = aiohttp.ClientTimeout(total=15)

    async with aiohttp.ClientSession(timeout=timeout) as session:

        while True:

            await asyncio.sleep(interval_seconds)

            try:
                async with session.get(health_url) as response:

                    logger.info(
                        "KEEPALIVE: %s -> HTTP %s",
                        health_url,
                        response.status,
                    )

            except asyncio.CancelledError:
                raise

            except Exception as exc:

                logger.warning(
                    "KEEPALIVE failed: %s",
                    exc,
                )


async def run_webhook(
    bot: Bot,
    dp: Dispatcher,
) -> None:
    """
    Production webhook mode for Render.

    Important:
    The aiohttp server is started FIRST.
    Only after the server is listening do we register
    the Telegram webhook.
    """

    from aiogram.webhook.aiohttp_server import (
        SimpleRequestHandler,
        setup_application,
    )

    # ---------------------------------------------------------
    # URLs
    # ---------------------------------------------------------

    public_url = config.public_url.rstrip("/")

    webhook_path = config.telegram_webhook_path or "/tg-webhook"

    if not webhook_path.startswith("/"):
        webhook_path = "/" + webhook_path

    webhook_url = public_url + webhook_path

    logger.info("PUBLIC_URL: %s", public_url)
    logger.info("WEBHOOK_PATH: %s", webhook_path)
    logger.info("WEBHOOK_URL: %s", webhook_url)

    # ---------------------------------------------------------
    # AIOHTTP APP
    # ---------------------------------------------------------

    app = web.Application()

    # Health endpoint
    async def health(_request: web.Request) -> web.Response:
        return web.Response(
            text="OK",
            status=200,
        )

    app.router.add_get(
        "/health",
        health,
    )

    # Optional root endpoint
    async def root(_request: web.Request) -> web.Response:
        return web.Response(
            text="Telegram bot is running.",
            status=200,
        )

    app.router.add_get(
        "/",
        root,
    )

    # ---------------------------------------------------------
    # SMS WEBHOOK
    # ---------------------------------------------------------

    register_sms_webhook(
        app,
        bot,
    )

    # ---------------------------------------------------------
    # TELEGRAM WEBHOOK HANDLER
    # ---------------------------------------------------------

    secret_token = config.telegram_webhook_secret or None

    request_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=secret_token,
    )

    request_handler.register(
        app,
        path=webhook_path,
    )

    setup_application(
        app,
        dp,
        bot=bot,
    )

    # ---------------------------------------------------------
    # START HTTP SERVER FIRST
    # ---------------------------------------------------------

    runner = web.AppRunner(app)

    await runner.setup()

    site = web.TCPSite(
        runner,
        host=config.webhook_server_host,
        port=config.port,
    )

    await site.start()

    logger.info(
        "HTTP SERVER STARTED: %s:%s",
        config.webhook_server_host,
        config.port,
    )

    logger.info(
        "Telegram webhook route: %s",
        webhook_path,
    )

    # ---------------------------------------------------------
    # NOW REGISTER TELEGRAM WEBHOOK
    # ---------------------------------------------------------

    try:

        await bot.delete_webhook(
            drop_pending_updates=False,
        )

        await bot.set_webhook(
            url=webhook_url,
            secret_token=secret_token,
            drop_pending_updates=True,
        )

        webhook_info = await bot.get_webhook_info()

        logger.info(
            "TELEGRAM WEBHOOK REGISTERED: %s",
            webhook_info.url,
        )

        logger.info(
            "WEBHOOK PENDING UPDATES: %s",
            webhook_info.pending_update_count,
        )

        if webhook_info.last_error_message:
            logger.warning(
                "TELEGRAM LAST WEBHOOK ERROR: %s",
                webhook_info.last_error_message,
            )

    except Exception as exc:

        logger.exception(
            "FAILED TO REGISTER TELEGRAM WEBHOOK: %s",
            exc,
        )

        raise

    # ---------------------------------------------------------
    # KEEPALIVE
    # ---------------------------------------------------------

    if config.keepalive_ping_seconds > 0:

        asyncio.create_task(
            self_ping_loop(
                public_url,
                config.keepalive_ping_seconds,
            )
        )

        logger.info(
            "KEEPALIVE ENABLED: every %s seconds",
            config.keepalive_ping_seconds,
        )

    else:

        logger.info(
            "KEEPALIVE DISABLED",
        )

    # ---------------------------------------------------------
    # KEEP PROCESS ALIVE
    # ---------------------------------------------------------

    await asyncio.Event().wait()


async def init_db_with_retry(
    max_attempts: int = 5,
) -> None:
    """Initialize database with retry."""

    delay = 2

    for attempt in range(
        1,
        max_attempts + 1,
    ):

        try:

            await init_db()

            logger.info(
                "Database initialized successfully.",
            )

            return

        except Exception as exc:

            if attempt == max_attempts:

                logger.exception(
                    "Database initialization failed after %s attempts.",
                    max_attempts,
                )

                raise

            logger.warning(
                "Database initialization failed "
                "(attempt %s/%s): %s. "
                "Retrying in %s seconds...",
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


async def main() -> None:

    # ---------------------------------------------------------
    # BOT TOKEN
    # ---------------------------------------------------------

    if not config.bot_token:

        raise RuntimeError(
            "BOT_TOKEN is not set."
        )

    # ---------------------------------------------------------
    # DATABASE
    # ---------------------------------------------------------

    await init_db_with_retry()

    # ---------------------------------------------------------
    # BOT
    # ---------------------------------------------------------

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
        ),
    )

    # ---------------------------------------------------------
    # DISPATCHER
    # ---------------------------------------------------------

    dp = build_dispatcher()

    # ---------------------------------------------------------
    # COMMANDS
    # ---------------------------------------------------------

    await configure_bot_commands(bot)

    # ---------------------------------------------------------
    # MODE
    # ---------------------------------------------------------

    if config.public_url.strip():

        logger.info(
            "PUBLIC_URL detected. Starting WEBHOOK mode."
        )

        await run_webhook(
            bot,
            dp,
        )

    else:

        logger.info(
            "PUBLIC_URL is empty. Starting POLLING mode."
        )

        await run_polling(
            bot,
            dp,
        )


if __name__ == "__main__":
    asyncio.run(main())
