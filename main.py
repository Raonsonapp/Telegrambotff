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


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger(__name__)


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=storage)

    force_join = ForceJoinMiddleware()

    dp.message.outer_middleware(force_join)
    dp.callback_query.outer_middleware(force_join)

    dp.include_router(admin.router)
    dp.include_router(customer.router)

    @dp.errors()
    async def handle_errors(event: ErrorEvent) -> bool:
        if (
            isinstance(event.exception, TelegramBadRequest)
            and "message is not modified" in str(event.exception)
        ):
            callback = event.update.callback_query

            if callback:
                try:
                    await callback.answer()
                except Exception:
                    pass

            return True

        logger.exception(
            "Unhandled Telegram update error",
            exc_info=event.exception,
        )

        return False

    return dp


async def _configure_bot_commands(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(
                command="start",
                description="Асосӣ меню",
            )
        ],
        scope=BotCommandScopeDefault(),
    )

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
                "Could not configure admin commands for %s: %s",
                admin_id,
                exc,
            )


async def _self_ping_loop(
    health_url: str,
    interval_seconds: int = 600,
) -> None:
    timeout = aiohttp.ClientTimeout(total=15)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        while True:
            try:
                await asyncio.sleep(interval_seconds)

                async with session.get(health_url) as response:
                    logger.info(
                        "KEEPALIVE: %s -> HTTP %s",
                        health_url,
                        response.status,
                    )

            except asyncio.CancelledError:
                logger.info("Keepalive task stopped")
                raise

            except Exception as exc:
                logger.warning(
                    "KEEPALIVE FAILED: %s",
                    exc,
                )


async def run_polling(
    bot: Bot,
    dp: Dispatcher,
) -> None:
    logger.info("Starting polling mode")

    await bot.delete_webhook(
        drop_pending_updates=True,
    )

    await dp.start_polling(bot)


async def run_webhook(
    bot: Bot,
    dp: Dispatcher,
) -> None:
    from aiogram.webhook.aiohttp_server import (
        SimpleRequestHandler,
        setup_application,
    )

    public_url = config.public_url.rstrip("/")

    webhook_path = "/" + config.telegram_webhook_path.lstrip("/")

    webhook_url = public_url + webhook_path

    health_url = public_url + "/health"

    logger.info("==========================================")
    logger.info("PUBLIC URL: %s", public_url)
    logger.info("WEBHOOK PATH: %s", webhook_path)
    logger.info("WEBHOOK URL: %s", webhook_url)
    logger.info("HEALTH URL: %s", health_url)
    logger.info("PORT: %s", config.port)
    logger.info("==========================================")

    await bot.set_webhook(
        url=webhook_url,
        secret_token=config.telegram_webhook_secret or None,
        drop_pending_updates=True,
    )

    webhook_info = await bot.get_webhook_info()

    logger.info(
        "TELEGRAM WEBHOOK INFO: url=%s pending=%s "
        "last_error_date=%s last_error=%s",
        webhook_info.url,
        webhook_info.pending_update_count,
        webhook_info.last_error_date,
        webhook_info.last_error_message,
    )

    app = web.Application()

    async def root(_request: web.Request) -> web.Response:
        return web.Response(
            text="ALMAZSHOP BOT ONLINE",
            status=200,
        )

    async def health(_request: web.Request) -> web.Response:
        return web.Response(
            text="OK",
            status=200,
        )

    app.router.add_get("/", root)
    app.router.add_get("/health", health)

    register_sms_webhook(app, bot)

    webhook_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=config.telegram_webhook_secret or None,
    )

    webhook_handler.register(
        app,
        path=webhook_path,
    )

    logger.info(
        "REGISTERED TELEGRAM WEBHOOK ROUTE: POST %s",
        webhook_path,
    )

    setup_application(
        app,
        dp,
        bot=bot,
    )

    runner = web.AppRunner(app)

    await runner.setup()

    site = web.TCPSite(
        runner,
        host="0.0.0.0",
        port=config.port,
    )

    await site.start()

    logger.info("==========================================")
    logger.info(
        "SERVER STARTED: 0.0.0.0:%s",
        config.port,
    )
    logger.info(
        "WEBHOOK LISTENING: POST %s",
        webhook_path,
    )
    logger.info(
        "TELEGRAM WEBHOOK: %s",
        webhook_url,
    )
    logger.info(
        "HEALTH CHECK: %s",
        health_url,
    )
    logger.info("==========================================")

    # 10-minute keepalive.
    # 600 seconds = 10 minutes.
    keepalive_seconds = 600

    asyncio.create_task(
        _self_ping_loop(
            health_url,
            keepalive_seconds,
        )
    )

    logger.info(
        "KEEPALIVE ENABLED: every 600 seconds (10 minutes)",
    )

    await asyncio.Event().wait()


async def _init_db_with_retry(
    max_attempts: int = 5,
) -> None:
    delay = 2

    for attempt in range(1, max_attempts + 1):
        try:
            await init_db()

            logger.info(
                "DATABASE INITIALIZED SUCCESSFULLY",
            )

            return

        except Exception as exc:
            if attempt == max_attempts:
                logger.exception(
                    "Database initialization failed "
                    "after %s attempts",
                    max_attempts,
                )

                raise

            logger.warning(
                "Database initialization failed "
                "(attempt %s/%s): %s. "
                "Retrying in %ss",
                attempt,
                max_attempts,
                exc,
                delay,
            )

            await asyncio.sleep(delay)

            delay = min(delay * 2, 30)


async def main() -> None:
    if not config.bot_token:
        raise RuntimeError(
            "BOT_TOKEN is not set. "
            "Copy .env.example to .env and fill it in."
        )

    await _init_db_with_retry()

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
        ),
    )

    dp = build_dispatcher()

    await _configure_bot_commands(bot)

    if config.public_url:
        logger.info(
            "PUBLIC_URL detected -> starting WEBHOOK mode",
        )

        await run_webhook(
            bot,
            dp,
        )

    else:
        logger.info(
            "PUBLIC_URL is empty -> starting POLLING mode",
        )

        await run_polling(
            bot,
            dp,
        )


if __name__ == "__main__":
    asyncio.run(main())
