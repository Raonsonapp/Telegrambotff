import asyncio
import logging
import signal

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
    # Global Telegram error handler
    # --------------------------------------------------------

    @dp.errors()
    async def handle_errors(event: ErrorEvent) -> bool:
        exception = event.exception

        # Telegram sometimes returns this when editing a message
        # that already contains exactly the same content.
        if (
            isinstance(exception, TelegramBadRequest)
            and "message is not modified" in str(exception).lower()
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

        # Returning False allows aiogram to handle/log the error.
        return False

    return dp


# ============================================================
# HTTP SERVER
# ============================================================

async def run_http_server(bot: Bot) -> web.AppRunner:
    """
    HTTP server for Render health checks and SMS/payment webhook.

    Telegram updates are received separately through long polling.
    Both run on the same asyncio event loop.
    """

    app = web.Application()

    # --------------------------------------------------------
    # Root endpoint
    # --------------------------------------------------------

    async def root_handler(_request: web.Request) -> web.Response:
        return web.Response(
            text="ALMAZSHOP BOT OK",
            status=200,
            content_type="text/plain",
        )

    # --------------------------------------------------------
    # Health endpoint
    # --------------------------------------------------------

    async def health_handler(_request: web.Request) -> web.Response:
        return web.json_response(
            {
                "status": "ok",
                "service": "ALMAZSHOP",
                "telegram": "polling",
            },
            status=200,
        )

    app.router.add_get("/", root_handler)
    app.router.add_get("/health", health_handler)

    # --------------------------------------------------------
    # Existing SMS/payment webhook
    # --------------------------------------------------------

    register_sms_webhook(app, bot)

    # --------------------------------------------------------
    # Start aiohttp server
    # --------------------------------------------------------

    runner = web.AppRunner(app)

    await runner.setup()

    # IMPORTANT:
    # Render requires the service to listen on 0.0.0.0
    # and on the PORT configured by Render.
    site = web.TCPSite(
        runner,
        host="0.0.0.0",
        port=config.port,
    )

    await site.start()

    logger.info(
        "=================================================="
    )
    logger.info(
        "HTTP SERVER STARTED"
    )
    logger.info(
        "HOST: 0.0.0.0"
    )
    logger.info(
        "PORT: %s",
        config.port,
    )
    logger.info(
        "HEALTH: /health"
    )

    if config.public_url:
        logger.info(
            "PUBLIC URL: %s",
            config.public_url.rstrip("/"),
        )

    logger.info(
        "=================================================="
    )

    return runner


# ============================================================
# TELEGRAM POLLING
# ============================================================

async def run_polling(
    bot: Bot,
    dp: Dispatcher,
) -> None:
    """
    Start Telegram long polling.

    There must be only ONE polling process for this bot token.
    """

    logger.info(
        "Preparing Telegram polling..."
    )

    # --------------------------------------------------------
    # Remove webhook before polling.
    #
    # If an old webhook exists, Telegram polling can fail with
    # webhook/polling conflicts.
    # --------------------------------------------------------

    await bot.delete_webhook(
        drop_pending_updates=False,
    )

    # --------------------------------------------------------
    # Check Telegram connection
    # --------------------------------------------------------

    me = await bot.get_me()

    logger.info(
        "=================================================="
    )
    logger.info(
        "TELEGRAM BOT CONNECTED"
    )
    logger.info(
        "USERNAME: @%s",
        me.username,
    )
    logger.info(
        "BOT ID: %s",
        me.id,
    )
    logger.info(
        "MODE: LONG POLLING"
    )
    logger.info(
        "UPDATES: %s",
        dp.resolve_used_update_types(),
    )
    logger.info(
        "=================================================="
    )

    # --------------------------------------------------------
    # Start polling.
    #
    # aiogram handles SIGINT/SIGTERM and stops polling
    # gracefully when Render shuts down/restarts the service.
    # --------------------------------------------------------

    try:
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
            handle_signals=True,
        )

    except asyncio.CancelledError:
        logger.info(
            "Telegram polling task cancelled."
        )
        raise

    finally:
        logger.info(
            "Telegram polling stopped."
        )


# ============================================================
# MAIN
# ============================================================

async def main() -> None:
    logger.info(
        "=================================================="
    )
    logger.info(
        "ALMAZSHOP BOT STARTING..."
    )
    logger.info(
        "=================================================="
    )

    # --------------------------------------------------------
    # BOT TOKEN CHECK
    # --------------------------------------------------------

    if not config.bot_token:
        raise RuntimeError(
            "BOT_TOKEN is not set. "
            "Set BOT_TOKEN in Render Environment Variables."
        )

    # --------------------------------------------------------
    # DATABASE
    # --------------------------------------------------------

    logger.info(
        "Initializing database..."
    )

    await init_db()

    logger.info(
        "Database initialized successfully."
    )

    # --------------------------------------------------------
    # TELEGRAM BOT
    # --------------------------------------------------------

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
        ),
    )

    # --------------------------------------------------------
    # DISPATCHER
    # --------------------------------------------------------

    dp = build_dispatcher()

    http_runner: web.AppRunner | None = None

    try:
        # ----------------------------------------------------
        # START HTTP SERVER FIRST
        # ----------------------------------------------------

        http_runner = await run_http_server(bot)

        logger.info(
            "HTTP server is ready."
        )

        # ----------------------------------------------------
        # START TELEGRAM POLLING
        # ----------------------------------------------------

        logger.info(
            "Starting Telegram polling..."
        )

        await run_polling(
            bot,
            dp,
        )

    except asyncio.CancelledError:
        logger.info(
            "Main task cancelled."
        )
        raise

    except Exception:
        logger.exception(
            "FATAL ERROR IN MAIN APPLICATION"
        )
        raise

    finally:
        # ----------------------------------------------------
        # STOP HTTP SERVER
        # ----------------------------------------------------

        if http_runner is not None:
            logger.info(
                "Stopping HTTP server..."
            )

            try:
                await http_runner.cleanup()
            except Exception:
                logger.exception(
                    "Error while stopping HTTP server."
                )

        # ----------------------------------------------------
        # CLOSE TELEGRAM SESSION
        # ----------------------------------------------------

        logger.info(
            "Closing Telegram session..."
        )

        try:
            await bot.session.close()
        except Exception:
            logger.exception(
                "Error while closing Telegram session."
            )

        logger.info(
            "ALMAZSHOP BOT SHUTDOWN COMPLETE."
        )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        logger.info(
            "Keyboard interrupt received. Bot stopped."
        )

    except SystemExit:
        raise

    except Exception:
        logger.exception(
            "Application terminated because of an unexpected error."
        )
        raise
