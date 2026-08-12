import asyncio
import logging

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


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger(__name__)


# =========================================================
# DISPATCHER
# =========================================================

def build_dispatcher() -> Dispatcher:

    dp = Dispatcher(storage=storage)

    # -----------------------------------------------------
    # Force Join Middleware
    # -----------------------------------------------------

    force_join = ForceJoinMiddleware()

    dp.message.outer_middleware(force_join)
    dp.callback_query.outer_middleware(force_join)

    # -----------------------------------------------------
    # Routers
    # -----------------------------------------------------

    dp.include_router(admin.router)
    dp.include_router(customer.router)

    # -----------------------------------------------------
    # Telegram "message is not modified" error
    # -----------------------------------------------------

    @dp.errors()
    async def handle_errors(event: ErrorEvent) -> bool:

        if (
            isinstance(event.exception, TelegramBadRequest)
            and "message is not modified"
            in str(event.exception).lower()
        ):

            callback = event.update.callback_query

            if callback is not None:

                try:
                    await callback.answer()
                except Exception:
                    pass

            return True

        # Any other unhandled exception from inside a handler: log it and
        # tell aiogram it was "handled" (return True) so this one bad
        # update can't take down the whole polling loop — without this,
        # an unexpected error while processing a single button tap or
        # message would otherwise propagate and stop the entire bot for
        # every user until Render restarts it.
        logger.exception(
            "Unhandled error while processing update: %s", event.exception
        )
        return True

    return dp


# =========================================================
# BOT COMMANDS
# =========================================================

async def configure_bot_commands(bot: Bot) -> None:

    # -----------------------------------------------------
    # Default users
    # -----------------------------------------------------

    await bot.set_my_commands(
        [
            BotCommand(
                command="start",
                description="Асосӣ меню",
            )
        ],
        scope=BotCommandScopeDefault(),
    )

    # -----------------------------------------------------
    # Admin commands
    # -----------------------------------------------------

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

            # Telegram requires the bot to already have a chat with this
            # user (they must have messaged it at least once) before a
            # per-chat command scope can be set for them — an admin who
            # hasn't pressed /start yet just keeps the default menu (still
            # works fine by typing /admin manually) until they do.
            logger.warning(
                "Admin commands error for %s: %s",
                admin_id,
                exc,
            )


# =========================================================
# HEALTH SERVER
# =========================================================

async def start_http_server(bot: Bot):

    app = web.Application()

    # -----------------------------------------------------
    # Health
    # -----------------------------------------------------

    async def health(
        request: web.Request,
    ) -> web.Response:

        return web.Response(
            text="OK",
            status=200,
        )

    # -----------------------------------------------------
    # Root
    # -----------------------------------------------------

    async def root(
        request: web.Request,
    ) -> web.Response:

        return web.Response(
            text="Telegram bot is running",
            status=200,
        )

    app.router.add_get(
        "/",
        root,
    )

    app.router.add_get(
        "/health",
        health,
    )

    # -----------------------------------------------------
    # SMS webhook
    # -----------------------------------------------------

    try:

        register_sms_webhook(
            app,
            bot,
        )

    except Exception as exc:

        logger.warning(
            "SMS webhook registration skipped: %s",
            exc,
        )

    # -----------------------------------------------------
    # Start server
    # -----------------------------------------------------

    runner = web.AppRunner(app)

    await runner.setup()

    port = int(config.port)

    site = web.TCPSite(
        runner,
        host="0.0.0.0",
        port=port,
    )

    await site.start()

    logger.info(
        "HTTP SERVER STARTED ON PORT %s",
        port,
    )

    logger.info(
        "HEALTH: /health",
    )

    return runner


# =========================================================
# KEEP ALIVE
# =========================================================

async def keepalive_loop():
    """Render's free tier spins a web service down after ~15 minutes with
    NO incoming HTTP request — polling mode makes zero inbound HTTP calls
    on its own (it's the bot calling OUT to Telegram, not Telegram calling
    in), so without this, Render sees "no traffic" and kills the process
    regardless of the bot being busy internally. Pinging our own /health
    on a schedule shorter than 15 minutes is what keeps the free instance
    alive. Interval is config.keepalive_ping_seconds (KEEPALIVE_PING_SECONDS
    env var, default 300s/5min — comfortably under the 15-minute cutoff
    even if a single ping is briefly delayed)."""

    interval = config.keepalive_ping_seconds

    if interval <= 0:
        logger.info("KEEPALIVE_PING_SECONDS=0 — keepalive disabled by config.")
        return

    public_url = (
        config.public_url
        .strip()
        .rstrip("/")
    )

    if not public_url:

        # This is exactly the "bot dies every 15 minutes" symptom: with no
        # PUBLIC_URL, this whole loop is a no-op and nothing ever stops
        # Render's own free-tier spin-down timer. Set PUBLIC_URL in
        # Render's Environment tab to this service's real https URL.
        logger.warning(
            "PUBLIC_URL is empty — keepalive DISABLED, Render's free "
            "instance WILL spin down after ~15 minutes of inactivity. "
            "Set PUBLIC_URL in Render's Environment tab to fix this."
        )

        return

    health_url = (
        public_url + "/health"
    )

    logger.info(
        "KEEPALIVE STARTED: every %s seconds -> %s", interval, health_url
    )

    timeout = aiohttp.ClientTimeout(
        total=20
    )

    async with aiohttp.ClientSession(
        timeout=timeout
    ) as session:

        while True:

            try:

                async with session.get(
                    health_url
                ) as response:

                    logger.info(
                        "KEEPALIVE -> %s -> HTTP %s",
                        health_url,
                        response.status,
                    )

            except asyncio.CancelledError:

                logger.info(
                    "KEEPALIVE STOPPED"
                )

                raise

            except Exception as exc:

                logger.warning(
                    "KEEPALIVE ERROR: %s",
                    exc,
                )

            await asyncio.sleep(
                interval
            )


# =========================================================
# DATABASE
# =========================================================

async def init_database():

    max_attempts = 5
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

            logger.error(
                "DATABASE ERROR %s/%s: %s",
                attempt,
                max_attempts,
                exc,
            )

            if attempt >= max_attempts:

                raise

            await asyncio.sleep(
                delay
            )

            delay = min(
                delay * 2,
                30,
            )


# =========================================================
# POLLING
# =========================================================

async def run_bot_polling(
    bot: Bot,
    dp: Dispatcher,
):

    # -----------------------------------------------------
    # VERY IMPORTANT
    # -----------------------------------------------------
    # Delete old Telegram webhook.
    #
    # This fixes:
    # "Wrong response from the webhook: 404 Not Found"
    # -----------------------------------------------------

    logger.info(
        "DELETING OLD TELEGRAM WEBHOOK..."
    )

    await bot.delete_webhook(
        drop_pending_updates=True
    )

    logger.info(
        "OLD WEBHOOK DELETED"
    )

    # -----------------------------------------------------
    # Check webhook
    # -----------------------------------------------------

    try:

        info = await bot.get_webhook_info()

        logger.info(
            "WEBHOOK URL AFTER DELETE: %s",
            info.url,
        )

    except Exception as exc:

        logger.warning(
            "Could not check webhook: %s",
            exc,
        )

    # -----------------------------------------------------
    # Start polling
    # -----------------------------------------------------

    logger.info(
        "========================================"
    )

    logger.info(
        "TELEGRAM BOT STARTING..."
    )

    logger.info(
        "MODE: POLLING"
    )

    logger.info(
        "========================================"
    )

    await dp.start_polling(
        bot,
        allowed_updates=dp.resolve_used_update_types(),
    )


# =========================================================
# MAIN
# =========================================================

async def main():

    logger.info(
        "========================================"
    )

    logger.info(
        "STARTING TELEGRAM BOT"
    )

    logger.info(
        "========================================"
    )

    # -----------------------------------------------------
    # Token
    # -----------------------------------------------------

    if not config.bot_token:

        raise RuntimeError(
            "BOT_TOKEN is not set."
        )

    # -----------------------------------------------------
    # Database
    # -----------------------------------------------------

    await init_database()

    # -----------------------------------------------------
    # Bot
    # -----------------------------------------------------

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML
        ),
    )

    # -----------------------------------------------------
    # Dispatcher
    # -----------------------------------------------------

    dp = build_dispatcher()

    # -----------------------------------------------------
    # Commands
    # -----------------------------------------------------

    await configure_bot_commands(
        bot
    )

    # -----------------------------------------------------
    # HTTP server
    # -----------------------------------------------------

    runner = await start_http_server(
        bot
    )

    # -----------------------------------------------------
    # Keepalive
    # -----------------------------------------------------

    keepalive_task = asyncio.create_task(
        keepalive_loop()
    )

    # -----------------------------------------------------
    # Start Telegram polling
    # -----------------------------------------------------

    try:

        await run_bot_polling(
            bot,
            dp,
        )

    finally:

        keepalive_task.cancel()

        try:

            await keepalive_task

        except asyncio.CancelledError:

            pass

        await runner.cleanup()

        await bot.session.close()

        logger.info(
            "BOT STOPPED"
        )


# =========================================================
# ENTRY POINT
# =========================================================

if __name__ == "__main__":

    try:

        asyncio.run(
            main()
        )

    except KeyboardInterrupt:

        logger.info(
            "BOT STOPPED BY USER"
        )

    except Exception as exc:

        logger.exception(
            "FATAL ERROR: %s",
            exc,
        )

        raise
