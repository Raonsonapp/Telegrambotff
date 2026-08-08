"""Public review/purchase announcement for the shop channel."""

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from sqlalchemy import func, select

from bot.config import config
from bot.db.models import Order, OrderStatus, User


async def post_review_announcement(
    bot: Bot,
    session,
    order: Order,
    product,
    review_text: str | None,
) -> None:
    """Post one purchase/review announcement to the public review channel.

    The post is sent only after the order has reached DELIVERED.
    Failures in the public channel must never break the customer's order flow.
    """

    if not config.review_channel_id:
        return

    # Get customer.
    user = await session.get(User, order.user_id)

    if user is None:
        display_name = "Мизоҷ"
    elif user.username:
        display_name = f"@{user.username}"
    elif user.full_name:
        display_name = user.full_name
    else:
        display_name = "Мизоҷ"

    # Number of successful purchases made by this customer.
    purchase_count_result = await session.execute(
        select(func.count(Order.id)).where(
            Order.user_id == order.user_id,
            Order.status == OrderStatus.DELIVERED,
        )
    )
    purchase_count = purchase_count_result.scalar_one()

    # Global sequential customer/review number.
    # Count delivered orders up to and including this order.
    review_number_result = await session.execute(
        select(func.count(Order.id)).where(
            Order.status == OrderStatus.DELIVERED,
            Order.id <= order.id,
        )
    )
    review_number = review_number_result.scalar_one()

    # Product display.
    product_name = f"💎 {product.diamonds}"

    lines = [
        f"🏅 ОТЗИВИ МУШТАРӢ #{review_number}",
        "",
        f"👤 Муштарӣ: {display_name}",
        "",
        f"🎁 Маҳсулот: {product_name}",
        f"🆔 ID фармоиш: #{order.id}",
    ]

    # Customer's real review.
    if review_text and review_text.strip():
        clean_review = review_text.strip()
        lines += [
            "",
            "💬 Отзыв:",
            f"«{clean_review}»",
        ]

    # Purchase counter.
    suffix = "умин"
    if purchase_count == 1:
        purchase_text = "1-умин"
    else:
        purchase_text = f"{purchase_count}-умин"

    lines += [
        "",
        f"🔥 Ин муштарӣ аллакай {purchase_text} хариди худро анҷом дод.",
        "Ташаккур барои эътимод ва ҳамкории доимӣ! ❤️",
    ]

    try:
        await bot.send_message(
            chat_id=config.review_channel_id,
            text="\n".join(lines),
        )
    except TelegramAPIError:
        # A channel/permission problem must never break the order flow.
        pass
