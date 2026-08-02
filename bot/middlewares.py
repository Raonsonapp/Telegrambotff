"""Force-Join gate.

Registered in main.py as an *outer* middleware on both the message and
callback_query pipelines, so it runs before FSM state is even loaded and
before any handler in admin.router or customer.router — nothing (not even
/start) reaches a handler until the user is a confirmed member of
config.channel_username.

Admins (config.admin_user_ids) always bypass the gate, so a misconfigured
or forgotten CHANNEL_USERNAME can never lock the bot owner out of the
admin panel.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message, TelegramObject

from bot.config import config
from bot.keyboards import force_join_keyboard

FORCE_JOIN_TEXT = "❌ Аввал ба канали мо обуна шавед."


async def is_subscribed(bot: Any, user_id: int) -> bool:
    """True if `user_id` is currently a member of config.channel_username.

    Any lookup failure (bot isn't an admin in the channel, the channel
    doesn't exist / was mistyped, or Telegram simply can't resolve it) is
    treated as "not subscribed" rather than raised — a single API hiccup
    must not crash the update, it should just re-show the join gate.
    """
    if not config.channel_username:
        # Feature not configured — never lock everyone out by mistake.
        return True
    try:
        member = await bot.get_chat_member(chat_id=config.channel_username, user_id=user_id)
    except TelegramBadRequest:
        return False
    return member.status in ("creator", "administrator", "member")


class ForceJoinMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not config.channel_username:
            return await handler(event, data)

        if isinstance(event, Message):
            user = event.from_user
        elif isinstance(event, CallbackQuery):
            user = event.from_user
        else:
            return await handler(event, data)

        if user is None or user.is_bot:
            return await handler(event, data)

        if user.id in config.admin_user_ids:
            return await handler(event, data)

        # The "✅ Check Subscription" button's own handler
        # (bot/handlers/customer.py:check_subscription) performs the real
        # re-check and reports the result — let it through instead of
        # intercepting it here too, or the user never gets that feedback.
        if isinstance(event, CallbackQuery) and event.data == "forcejoin:check":
            return await handler(event, data)

        if await is_subscribed(event.bot, user.id):
            return await handler(event, data)

        if isinstance(event, CallbackQuery):
            await event.answer(FORCE_JOIN_TEXT, show_alert=True)
            return None

        await event.answer(FORCE_JOIN_TEXT, reply_markup=force_join_keyboard())
        return None
