from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from bot.config import config
from bot.db.models import Order, Product, ProductCategory

# ═══════════════════════════════════════════════════════════════════════
# DESIGN SYSTEM — button color
# ═══════════════════════════════════════════════════════════════════════
# Telegram Bot API 9.4 (Feb 9, 2026) added a `style` field to
# KeyboardButton/InlineKeyboardButton — but it supports exactly THREE
# accent colors total, not an arbitrary palette: 'success' (green),
# 'danger' (red), 'primary' (blue). There is no yellow/gold, purple, or
# orange at the platform level, and Telegram has ZERO support for button
# press sounds, glow/particle effects, or animation of any kind — buttons
# are static text + one of these 3 colors, full stop, on every bot on the
# platform, not something specific to this project.
#
# Every button below gets one of the 3 real colors — NEVER the
# uncolored/default style — so nothing renders as a flat, colorless
# button. Where a button has a specific, strong meaning (confirm, reject,
# pay-now), that meaning always wins and always gets the same color
# everywhere in the bot:
#
#   STYLE_GO      🟢 green  — confirm / accept / pay / proceed / buy /
#                             "yes" / mark delivered
#   STYLE_STOP    🔴 red    — reject / cancel / decline / "no"
#   STYLE_NAV     🔵 blue   — navigation (menu/back/categories/profile)
#
# For a row of PEER buttons with no individually-distinct meaning (a list
# of game categories, a list of product packs — every row means exactly
# "select this"), _peer_style() below rotates through all 3 colors so the
# list has real visual variety instead of one flat color, while a button
# with a specific meaning elsewhere (✅/❌/💳 Пардохт/...) always keeps its
# fixed, consistent color regardless of position.
#
# Requires aiogram>=3.30.0 (see requirements.txt) and a reasonably recent
# Telegram client to actually render — older clients simply ignore the
# field and show the normal default button, so this never breaks
# anything, it just won't show a color there yet.
STYLE_GO = "success"
STYLE_STOP = "danger"
STYLE_NAV = "primary"
# "Back/exit" buttons (🔙 Ба меню, ...) were asked to be orange —
# Telegram doesn't offer that color, so these use blue (navigation) like
# every other menu-movement button; still always colored, never blank.
STYLE_BACK = STYLE_NAV

_ROTATION = (STYLE_NAV, STYLE_GO, STYLE_STOP)


def _peer_style(index: int) -> str:
    """3-way rotation through every real color for a list of same-meaning
    peer choices, so the list has visual rhythm instead of being one flat
    block of one color — see DESIGN SYSTEM above. Never returns None."""
    return _ROTATION[index % len(_ROTATION)]


def _ibtn(
    text: str,
    *,
    callback_data: str | None = None,
    url: str | None = None,
    style: str = STYLE_NAV,
) -> InlineKeyboardButton:
    """Single place every inline button is built — keeps `style=` from
    being hand-typed (and potentially getting inconsistent, or forgotten
    and left blank) at every call site; see the DESIGN SYSTEM block above
    for what each style means. Default is STYLE_NAV, not blank/None, so a
    button that forgets to pass style= still renders colored."""
    kwargs: dict = {"text": text, "style": style}
    if callback_data is not None:
        kwargs["callback_data"] = callback_data
    if url is not None:
        kwargs["url"] = url
    return InlineKeyboardButton(**kwargs)


def _kbtn(text: str, *, style: str = STYLE_NAV) -> KeyboardButton:
    """Same idea as _ibtn but for the persistent reply keyboard."""
    return KeyboardButton(text=text, style=style)


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
        inline_keyboard=[[_ibtn("✅ Қабул мекунам", callback_data="terms:accept", style=STYLE_GO)]]
    )


# Force-Join gate (see bot/middlewares.py) — shown instead of the main menu
# until the user is a confirmed member of config.channel_username.
def force_join_keyboard() -> InlineKeyboardMarkup:
    rows = []
    if config.channel_url:
        rows.append([_ibtn("📢 Join Channel", url=config.channel_url, style=STYLE_NAV)])
    rows.append([_ibtn("✅ Check Subscription", callback_data="forcejoin:check", style=STYLE_GO)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# The single always-visible menu — pinned below the message input for the
# whole chat once sent, independent of whatever inline keyboards later
# messages carry. WELCOME_TEXT itself carries no inline grid anymore so
# the two don't show up as a visually duplicated menu.
def main_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [_kbtn("🎮 Бозиҳо", style=STYLE_NAV), _kbtn("✈️ Telegram", style=STYLE_GO)],
            [_kbtn("👤 Профил", style=STYLE_NAV), _kbtn("🤝 Реферал", style=STYLE_GO)],
            [_kbtn("⭐ Отзив", style=STYLE_NAV), _kbtn("🆘 Дастгирӣ", style=STYLE_STOP)],
            [_kbtn("❓ Саволҳои маъмул", style=STYLE_GO), _kbtn("ℹ️ Маълумот", style=STYLE_NAV)],
            [_kbtn("🎁 Туҳфа", style=STYLE_STOP)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def review_channel_keyboard() -> InlineKeyboardMarkup:
    if not config.shop_channel_url:
        return back_to_menu_keyboard()
    return InlineKeyboardMarkup(
        inline_keyboard=[[_ibtn("📢 Кушодани канал", url=config.shop_channel_url, style=STYLE_NAV)]]
    )


def games_menu_keyboard() -> InlineKeyboardMarkup:
    games = [
        ("🔥 Free Fire", "menu:buy_diamonds"),
        ("🔥 Free Fire Индонезия", "menu:ff_indonesia"),
        ("🔫 PUBG Mobile", "menu:pubg"),
        ("🎯 Standoff 2", "menu:standoff2"),
    ]
    rows = [[_ibtn(text, callback_data=data, style=_peer_style(i))] for i, (text, data) in enumerate(games)]
    rows.append([_ibtn("🔙 Ба меню", callback_data="menu:main", style=STYLE_BACK)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def profile_menu_keyboard() -> InlineKeyboardMarkup:
    items = [
        ("📄 Фармоишҳоям", "menu:myorders"),
        ("🏆 Топ харидорон", "menu:top_buyers"),
        ("🏅 Топ рефералдорон", "menu:top_referrers"),
        ("📜 Таърихи баланс", "menu:balance_history"),
    ]
    rows = [[_ibtn(text, callback_data=data, style=_peer_style(i))] for i, (text, data) in enumerate(items)]
    rows.append([_ibtn("🔙 Ба меню", callback_data="menu:main", style=STYLE_BACK)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def referral_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_ibtn("🏅 Топ рефералдорон", callback_data="menu:top_referrers", style=STYLE_NAV)],
            [_ibtn("🔙 Ба меню", callback_data="menu:main", style=STYLE_BACK)],
        ]
    )


def back_to_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[_ibtn("🔙 Ба меню", callback_data="menu:main", style=STYLE_BACK)]]
    )


def contact_keyboard() -> InlineKeyboardMarkup:
    # A blank URL (e.g. WhatsApp/Instagram not filled in yet) makes Telegram
    # reject the whole send_message call with BUTTON_URL_INVALID — skip that
    # row entirely instead of shipping a broken button, so the rest of the
    # contact screen still works while those values are still pending.
    # Colors here are per explicit request rather than the meaning-based
    # scheme elsewhere: 🟢 WhatsApp, 🔴 Instagram, 🔵 channel.
    rows = []
    if config.contact_whatsapp_url:
        rows.append([_ibtn("💬 WhatsApp", url=config.contact_whatsapp_url, style=STYLE_GO)])
    if config.contact_instagram_url:
        rows.append([_ibtn("📷 Instagram", url=config.contact_instagram_url, style=STYLE_STOP)])
    if config.shop_channel_url:
        rows.append([_ibtn("📢 Канал", url=config.shop_channel_url, style=STYLE_NAV)])
    rows.append([_ibtn("🔙 Ба меню", callback_data="menu:main", style=STYLE_BACK)])
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
        [_ibtn(_product_label(p), callback_data=f"product:{p.id}", style=_peer_style(i))]
        for i, p in enumerate(products)
    ]
    if category in _CART_ENABLED_CATEGORIES:
        rows.append(
            [_ibtn("🛒 Якчанд бастаро якҷоя харидан", callback_data=f"cartmode:{category.value}", style=STYLE_GO)]
        )
    rows.append([_ibtn("🔙 Ба меню", callback_data="menu:main", style=STYLE_BACK)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cart_select_keyboard(
    products: list[Product], category: ProductCategory, selected_ids: set[int]
) -> InlineKeyboardMarkup:
    rows = []
    for i, p in enumerate(products):
        mark = "✅" if p.id in selected_ids else "⬜"
        style = STYLE_GO if p.id in selected_ids else _peer_style(i)
        rows.append([_ibtn(f"{mark} {_product_label(p)}", callback_data=f"cartitem:{p.id}", style=style)])
    if selected_ids:
        total = sum(p.price_somoni for p in products if p.id in selected_ids)
        rows.append(
            [
                _ibtn(
                    f"🛍 Идома ({len(selected_ids)} — {total:.2f} сомонӣ)",
                    callback_data="cart:checkout",
                    style=STYLE_GO,
                )
            ]
        )
    rows.append(
        [_ibtn("🔙 Якто-якто харидан", callback_data=f"cartmode:exit:{category.value}", style=STYLE_BACK)]
    )
    rows.append([_ibtn("🔙 Ба меню", callback_data="menu:main", style=STYLE_BACK)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def reuse_recipient_keyboard(recipient: str, label_suffix: str = "") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _ibtn(
                    f"✅ Истифодаи: {recipient}{label_suffix}",
                    callback_data=f"reuseid:{recipient}",
                    style=STYLE_GO,
                )
            ]
        ]
    )


def payment_link_keyboard(pay_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_ibtn("💳 Пардохт", url=pay_url, style=STYLE_GO)]])


def payment_method_keyboard() -> InlineKeyboardMarkup:
    """Shown right after "✅ Тасдиқ (бо чек)" when PAYMENT_PROVIDER=manual —
    lets the customer choose which manual card to pay into. All three
    options lead to the exact same admin-confirmed receipt flow (see
    bot/services/payments.py), just with a different card/label — same
    STYLE_GO for all three since they're parallel "proceed to pay"
    choices, not different outcomes."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_ibtn("💳 ДС (Душанбе Сити)", callback_data="paymethod:card", style=STYLE_GO)],
            [_ibtn("💳 Алиф", callback_data="paymethod:alif", style=STYLE_GO)],
            [_ibtn("💳 Амонатбонк", callback_data="paymethod:amonatbonk", style=STYLE_GO)],
            [_ibtn("❌ Бекор", callback_data="order:cancel", style=STYLE_STOP)],
        ]
    )


def review_prompt_keyboard(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[_ibtn("⏭ Гузарондан", callback_data=f"review:skip:{order_id}", style=STYLE_NAV)]]
    )


def confirm_order_keyboard(offer_balance_payment: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if offer_balance_payment:
        rows.append(
            [_ibtn("💰 Пардохт аз баланси реферал", callback_data="order:pay_balance", style=STYLE_GO)]
        )
    rows.append(
        [
            _ibtn("✅ Тасдиқ (бо чек)", callback_data="order:confirm", style=STYLE_GO),
            _ibtn("❌ Бекор", callback_data="order:cancel", style=STYLE_STOP),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_order_keyboard(order: Order) -> InlineKeyboardMarkup:
    rows = []
    if order.status.value == "awaiting_payment":
        rows.append(
            [
                _ibtn("✅ Пардохт тасдиқ шуд", callback_data=f"admin:paid:{order.id}", style=STYLE_GO),
                _ibtn("❌ Рад", callback_data=f"admin:reject:{order.id}", style=STYLE_STOP),
            ]
        )
    elif order.status.value in ("paid", "delivering"):
        rows.append(
            [_ibtn("📦 Дода шуд (Delivered)", callback_data=f"admin:delivered:{order.id}", style=STYLE_GO)]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_panel_keyboard() -> InlineKeyboardMarkup:
    """The /admin panel — read-only views the admin can tap instead of
    typing a command; anything that needs typed arguments (adding a
    product, setting a price, ...) still has its own /command, listed as
    text below the buttons (see bot/handlers/admin.py:admin_panel)."""
    items = [
        ("📦 Маҳсулот", "adminpanel:products"),
        ("⏳ Фармоишҳои дар интизор", "adminpanel:pending"),
        ("🧾 Чекҳои фиристодашуда", "adminpanel:proofs"),
        ("🎁 Ҳолати туҳфа", "adminpanel:giveaway"),
    ]
    return InlineKeyboardMarkup(
        inline_keyboard=[[_ibtn(text, callback_data=data, style=_peer_style(i))] for i, (text, data) in enumerate(items)]
    )


def admin_panel_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[_ibtn("🔙 Панели админ", callback_data="adminpanel:home", style=STYLE_BACK)]]
    )
