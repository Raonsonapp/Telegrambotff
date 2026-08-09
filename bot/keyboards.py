from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from bot.config import config
from bot.db.models import Order, Product, ProductCategory

# Categories that support "🛒 Якчанд бастаро якҷоя харидан" (buy several
# packs in one checkout). Left out on purpose:
# - TELEGRAM: always was single-item only.
# - COMBO: must stay one-at-a-time so the one-per-account duplicate check
#   (bot/db/repo.py:has_combo_purchase) is always checked against a single,
#   unambiguous recipient ID per purchase.
_CART_ENABLED_CATEGORIES = {
    ProductCategory.DIAMONDS,
    ProductCategory.FF_BRAZIL,
    ProductCategory.FF_INDONESIA,
    ProductCategory.PUBG,
    ProductCategory.STANDOFF2,
}


def terms_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="✅ Қабул мекунам", callback_data="terms:accept")]]
    )


# Force-Join gate (see bot/middlewares.py) — shown instead of the main menu
# until the user is a confirmed member of config.channel_username.
def force_join_keyboard() -> InlineKeyboardMarkup:
    rows = []
    if config.channel_url:
        rows.append([InlineKeyboardButton(text="📢 Join Channel", url=config.channel_url)])
    rows.append([InlineKeyboardButton(text="✅ Check Subscription", callback_data="forcejoin:check")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# The single always-visible menu — pinned below the message input for the
# whole chat once sent, independent of whatever inline keyboards later
# messages carry. WELCOME_TEXT itself carries no inline grid anymore so
# the two don't show up as a visually duplicated menu.
def main_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎮 Бозиҳо"), KeyboardButton(text="✈️ Telegram")],
            [KeyboardButton(text="👤 Профил"), KeyboardButton(text="🤝 Реферал")],
            [KeyboardButton(text="⭐ Отзив"), KeyboardButton(text="🆘 Дастгирӣ")],
            [KeyboardButton(text="❓ Саволҳои маъмул"), KeyboardButton(text="ℹ️ Маълумот")],
            [KeyboardButton(text="🎁 Туҳфа")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def review_channel_keyboard() -> InlineKeyboardMarkup:
    if not config.shop_channel_url:
        return back_to_menu_keyboard()
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="📢 Кушодани канал", url=config.shop_channel_url)]]
    )


def games_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔥 Free Fire", callback_data="menu:buy_diamonds")],
            [InlineKeyboardButton(text="🔥 Free Fire Бразилия", callback_data="menu:ff_brazil")],
            [InlineKeyboardButton(text="🔥 Free Fire Индонезия", callback_data="menu:ff_indonesia")],
            [InlineKeyboardButton(text="🔫 PUBG Mobile", callback_data="menu:pubg")],
            [InlineKeyboardButton(text="🎯 Standoff 2", callback_data="menu:standoff2")],
            [InlineKeyboardButton(text="🎫 Комбо (Пропуски прокачка)", callback_data="menu:combo")],
            [InlineKeyboardButton(text="🔙 Ба меню", callback_data="menu:main")],
        ]
    )


def profile_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📄 Фармоишҳоям", callback_data="menu:myorders")],
            [InlineKeyboardButton(text="🏆 Топ харидорон", callback_data="menu:top_buyers")],
            [InlineKeyboardButton(text="🏅 Топ рефералдорон", callback_data="menu:top_referrers")],
            [InlineKeyboardButton(text="📜 Таърихи баланс", callback_data="menu:balance_history")],
            [InlineKeyboardButton(text="🔙 Ба меню", callback_data="menu:main")],
        ]
    )


def referral_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🏅 Топ рефералдорон", callback_data="menu:top_referrers")],
            [InlineKeyboardButton(text="🔙 Ба меню", callback_data="menu:main")],
        ]
    )


def back_to_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔙 Ба меню", callback_data="menu:main")]]
    )


def contact_keyboard() -> InlineKeyboardMarkup:
    # A blank URL (e.g. WhatsApp/Instagram not filled in yet) makes Telegram
    # reject the whole send_message call with BUTTON_URL_INVALID — skip that
    # row entirely instead of shipping a broken button, so the rest of the
    # contact screen still works while those values are still pending.
    rows = []
    if config.contact_whatsapp_url:
        rows.append([InlineKeyboardButton(text="💬 WhatsApp", url=config.contact_whatsapp_url)])
    if config.contact_instagram_url:
        rows.append([InlineKeyboardButton(text="📷 Instagram", url=config.contact_instagram_url)])
    if config.shop_channel_url:
        rows.append([InlineKeyboardButton(text="📢 Канал", url=config.shop_channel_url)])
    rows.append([InlineKeyboardButton(text="🔙 Ба меню", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _product_label(p: Product) -> str:
    # A plain pack's admin-given name is just its size ("100 диамонд"), so a
    # numeric diamond count says more than the name; a voucher/subscription
    # ("Ваучери ҳафтагӣ") or combo ("Пропуски прокачка 800") has a name that
    # carries real information the raw amount would hide — show whichever
    # is meaningful.
    if p.name[:1].isdigit():
        bonus = f" (+{p.bonus_diamonds} бонус)" if p.bonus_diamonds else ""
        return f"{p.diamonds}{bonus} {p.unit_label} — {p.price_somoni:.2f} сомонӣ"
    return f"🎟 {p.name} — {p.price_somoni:.2f} сомонӣ"


def products_keyboard(products: list[Product], category: ProductCategory) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=_product_label(p), callback_data=f"product:{p.id}")]
        for p in products
    ]
    if category in _CART_ENABLED_CATEGORIES:
        rows.append(
            [InlineKeyboardButton(text="🛒 Якчанд бастаро якҷоя харидан", callback_data=f"cartmode:{category.value}")]
        )
    rows.append(
        [InlineKeyboardButton(text="🔙 Ба меню", callback_data="menu:main")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cart_select_keyboard(
    products: list[Product], category: ProductCategory, selected_ids: set[int]
) -> InlineKeyboardMarkup:
    rows = []
    for p in products:
        mark = "✅" if p.id in selected_ids else "⬜"
        rows.append(
            [InlineKeyboardButton(text=f"{mark} {_product_label(p)}", callback_data=f"cartitem:{p.id}")]
        )
    if selected_ids:
        total = sum(p.price_somoni for p in products if p.id in selected_ids)
        rows.append(
            [InlineKeyboardButton(text=f"🛍 Идома ({len(selected_ids)} — {total:.2f} сомонӣ)", callback_data="cart:checkout")]
        )
    rows.append(
        [InlineKeyboardButton(text="🔙 Якто-якто харидан", callback_data=f"cartmode:exit:{category.value}")]
    )
    rows.append([InlineKeyboardButton(text="🔙 Ба меню", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def reuse_recipient_keyboard(recipient: str, label_suffix: str = "") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"✅ Истифодаи: {recipient}{label_suffix}",
                    callback_data=f"reuseid:{recipient}",
                )
            ]
        ]
    )


def payment_link_keyboard(pay_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="💳 Пардохт", url=pay_url)]]
    )


def payment_method_keyboard() -> InlineKeyboardMarkup:
    """Shown right after "✅ Тасдиқ (бо чек)" when PAYMENT_PROVIDER=manual —
    lets the customer choose which manual card to pay into. Both options
    lead to the exact same admin-confirmed receipt flow (see
    bot/services/payments.py), just with a different card/label."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 ДС (Душанбе Сити)", callback_data="paymethod:card")],
            [InlineKeyboardButton(text="💳 Алиф", callback_data="paymethod:alif")],
            [InlineKeyboardButton(text="💳 Эсхата", callback_data="paymethod:eskhata")],
            [InlineKeyboardButton(text="💳 Амонатбонк", callback_data="paymethod:amonatbonk")],
            [InlineKeyboardButton(text="❌ Бекор", callback_data="order:cancel")],
        ]
    )


def review_prompt_keyboard(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⏭ Гузарондан", callback_data=f"review:skip:{order_id}")]
        ]
    )


def confirm_order_keyboard(offer_balance_payment: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if offer_balance_payment:
        rows.append(
            [InlineKeyboardButton(text="💰 Пардохт аз баланси реферал", callback_data="order:pay_balance")]
        )
    rows.append(
        [
            InlineKeyboardButton(text="✅ Тасдиқ (бо чек)", callback_data="order:confirm"),
            InlineKeyboardButton(text="❌ Бекор", callback_data="order:cancel"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_order_keyboard(order: Order) -> InlineKeyboardMarkup:
    rows = []
    if order.status.value == "awaiting_payment":
        rows.append(
            [
                InlineKeyboardButton(
                    text="✅ Пардохт тасдиқ шуд", callback_data=f"admin:paid:{order.id}"
                ),
                InlineKeyboardButton(
                    text="❌ Рад", callback_data=f"admin:reject:{order.id}"
                ),
            ]
        )
    elif order.status.value in ("paid", "delivering"):
        rows.append(
            [
                InlineKeyboardButton(
                    text="📦 Дода шуд (Delivered)", callback_data=f"admin:delivered:{order.id}"
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)
