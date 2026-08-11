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
    format="%(asctime)s | %(levelname)s | %(message)s",
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
    async def handle_stale_edit(event: ErrorEvent) -> bool:
        """
        Ignore 'message is not modified' errors.
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

        return False

    return dp


async def configure_bot_commands(bot: Bot) -> None:
    """
    Configure Telegram commands.
    """

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

    # Admins
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


async def run_polling(
    bot: Bot,
    dp: Dispatcher,
) -> None:
    """
    Local polling mode.
    """

    logger.info("Starting polling mode...")

    await bot.delete_webhook(
        drop_pending_updates=True
    )

    await dp.start_polling(bot)


async def self_ping_loop(
    public_url: str,
    interval_seconds: int,
) -> None:
    """
    Send a request every N seconds to keep the Render service active.
    """

    health_url = public_url.rstrip("/") + "/health"

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


async def run_webhook(
    bot: Bot,
    dp: Dispatcher,
) -> None:
    """
    Render production webhook mode.

    IMPORTANT:
    The HTTP server and /tg-webhook route are started FIRST.
    Only after that do we tell Telegram to use the webhook.
    """

    from aiogram.webhook.aiohttp_server import (
        SimpleRequestHandler,
        setup_application,
    )

    # ---------------------------------------------------------
    # URL
    # ---------------------------------------------------------

    public_url = config.public_url.strip().rstrip("/")

    webhook_path = (
        config.telegram_webhook_path.strip()
        or "/tg-webhook"
    )

    if not webhook_path.startswith("/"):
        webhook_path = "/" + webhook_path

    webhook_url = (
        public_url + webhook_path
    )

    logger.info(
        "PUBLIC_URL = %s",
        public_url,
    )

    logger.info(
        "WEBHOOK_PATH = %s",
        webhook_path,
    )

    logger.info(
        "WEBHOOK_URL = %s",
        webhook_url,
    )

    # ---------------------------------------------------------
    # Create aiohttp application
    # ---------------------------------------------------------

    app = web.Application()

    # ---------------------------------------------------------
    # Health endpoints
    # ---------------------------------------------------------

    async def health(
        _request: web.Request,
    ) -> web.Response:
        return web.Response(
            text="OK",
            status=200,
        )

    # /health
    app.router.add_get(
        "/health",
        health,
    )

    # / also returns OK
    app.router.add_get(
        "/",
        health,
    )

    # ---------------------------------------------------------
    # SMS webhook
    # ---------------------------------------------------------

    register_sms_webhook(
        app,
        bot,
    )

    # ---------------------------------------------------------
    # Telegram webhook handler
    # ---------------------------------------------------------

    webhook_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=(
            config.telegram_webhook_secret
            or None
        ),
    )

    # IMPORTANT:
    # Register /tg-webhook BEFORE set_webhook()
    webhook_handler.register(
        app,
        path=webhook_path,
    )

    # ---------------------------------------------------------
    # Connect aiohttp with aiogram
    # ---------------------------------------------------------

    setup_application(
        app,
        dp,
        bot=bot,
    )

    # ---------------------------------------------------------
    # Start HTTP server
    # ---------------------------------------------------------

    runner = web.AppRunner(
        app
    )

    await runner.setup()

    site = web.TCPSite(
        runner,
        host="0.0.0.0",
        port=config.port,
    )

    await site.start()

    logger.info(
        "HTTP SERVER STARTED"
    )

    logger.info(
        "LISTENING ON PORT: %s",
        config.port,
    )

    logger.info(
        "WEBHOOK ROUTE READY: %s",
        webhook_path,
    )

    # ---------------------------------------------------------
    # IMPORTANT:
    # Server is READY now.
    # ONLY NOW set Telegram webhook.
    # ---------------------------------------------------------

    try:

        await bot.set_webhook(
            url=webhook_url,
            secret_token=(
                config.telegram_webhook_secret
                or None
            ),
            drop_pending_updates=True,
        )

        logger.info(
            "TELEGRAM WEBHOOK SET SUCCESSFULLY"
        )

        logger.info(
            "TELEGRAM WEBHOOK URL: %s",
            webhook_url,
        )

    except Exception as exc:

        logger.exception(
            "FAILED TO SET TELEGRAM WEBHOOK: %s",
            exc,
        )

        raise

    # ---------------------------------------------------------
    # Get Telegram webhook status
    # ---------------------------------------------------------

    try:

        info = await bot.get_webhook_info()

        logger.info(
            "WEBHOOK INFO:"
        )

        logger.info(
            "URL: %s",
            info.url,
        )

        logger.info(
            "PENDING UPDATES: %s",
            info.pending_update_count,
        )

        if info.last_error_message:

            logger.warning(
                "LAST TELEGRAM ERROR: %s",
                info.last_error_message,
            )

    except Exception as exc:

        logger.warning(
            "Could not get webhook info: %s",
            exc,
        )

    # ---------------------------------------------------------
    # Keepalive
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
            "KEEPALIVE DISABLED"
        )

    # ---------------------------------------------------------
    # Keep server alive
    # ---------------------------------------------------------

    logger.info(
        "BOT IS RUNNING IN WEBHOOK MODE"
    )

    await asyncio.Event().wait()


async def init_db_with_retry(
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

            logger.info(
                "Retrying in %s seconds...",
                delay,
            )

            await asyncio.sleep(
                delay
            )

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
            parse_mode=ParseMode.HTML
        ),
    )

    # ---------------------------------------------------------
    # DISPATCHER
    # ---------------------------------------------------------

    dp = build_dispatcher()

    # ---------------------------------------------------------
    # COMMANDS
    # ---------------------------------------------------------

    await configure_bot_commands(
        bot
    )

    # ---------------------------------------------------------
    # WEBHOOK / POLLING
    # ---------------------------------------------------------

    if config.public_url.strip():

        logger.info(
            "PUBLIC_URL FOUND"
        )

        logger.info(
            "STARTING WEBHOOK MODE"
        )

        await run_webhook(
            bot,
            dp,
        )

    else:

        logger.info(
            "PUBLIC_URL EMPTY"
        )

        logger.info(
            "STARTING POLLING MODE"
        )

        await run_polling(
            bot,
            dp,
        )


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
