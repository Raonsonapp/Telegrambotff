from bot.db.models import OrderStatus

TERMS_TEXT = (
    "📜 Шартномаи корбар\n\n"
    "Пеш аз истифода лутфан шартҳоро хонед:\n\n"
    "1️⃣ Синну сол: Хидматҳо танҳо барои 18+. Агар хурдтар ҳастед, лутфан бо иҷозати падару модар истифода баред.\n\n"
    "2️⃣ Донат: Пас аз тасдиқи пардохт, маҳсулот одатан дар 1-5 дақиқа фиристода мешавад.\n\n"
    "3️⃣ Бозгашти пул: Пас аз донат пул бозгардонида намешавад (ба ғайр аз хатои техникии мо).\n\n"
    "4️⃣ ID/Username дуруст: Масъулияти дурустии ID-и бозингар ё username бар уҳдаи корбар аст.\n\n"
    "✅ Бо пахши «Қабул мекунам» шумо ба ҳама шартҳо розӣ мешавед."
)

FAQ_TEXT = (
    "❓ Саволҳои маъмул\n\n"
    "⏱ Чанд вақт мегирад?\n"
    "Одатан маҳсулот дар 1-5 дақиқа баъд аз тасдиқи пардохт фиристода мешавад.\n\n"
    "📝 Агар ID/Username-и хато навишта бошам чӣ?\n"
    "Пеш аз тасдиқ бо админ тамос гиред. Баъд аз иҷрои фармоиш, маблағ бозгардонида намешавад — барои ҳамин лутфан бодиққат санҷед.\n\n"
    "💰 Пул баргардонида мешавад?\n"
    "Не, баъд аз иҷрои муваффақ пул баргардонида намешавад (ба ғайр аз хатои техникии мо). Агар пардохт кардед вале маҳсулот нарасид, ба дастгирӣ муроҷиат кунед.\n\n"
    "💳 Кадом усулҳои пардохт ҳастанд?\n"
    "Гузаронидани корт ба корт (дастӣ) ё «💳 Алиф». Ҳангоми тасдиқи фармоиш усули дилхоҳро интихоб мекунед.\n\n"
    "📸 Чек чӣ гуна фиристам?\n"
    "Баъд аз пардохт, расми чекро (скриншот аз барномаи бонк) ба ҳамин чат фиристед. Бот худкор ба админ мефиристад.\n\n"
    "⚠️ Пардохт кардам, вале бот тасдиқ накард — чӣ кунам?\n"
    "Каме сабр кунед (то 15-30 дақиқа). Агар боз ҳам тасдиқ нашуд, ба дастгирӣ тамос гиред ва скриншоти пардохтро нишон диҳед."
)

# Customer-facing status labels for "📄 Фармоишҳоям" / /myorders — see
# bot/handlers/customer.py:_format_orders_text.
ORDER_STATUS_LABELS: dict[OrderStatus, str] = {
    OrderStatus.AWAITING_PAYMENT: "⏳ Интизорӣ",
    OrderStatus.PAID: "✅ Пардохт шуд",
    OrderStatus.DELIVERING: "🚚 Фиристода шуд",
    OrderStatus.DELIVERED: "✔️ Иҷро шуд",
    OrderStatus.CANCELLED: "❌ Рад шуд",
    OrderStatus.FAILED: "❌ Хато",
}


def order_status_label(status: OrderStatus) -> str:
    return ORDER_STATUS_LABELS.get(status, status.value)


# Order.payment_provider values -> human label, shown to the admin when a
# receipt comes in (bot/handlers/customer.py:receive_payment_proof) and
# anywhere else an order's payment method is displayed.
PAYMENT_METHOD_LABELS: dict[str, str] = {
    "manual": "💳 ДС",
    "manual_alif": "💳 Алиф",
    "manual_amonatbonk": "💳 Амонатбонк",
    "alif": "Alif Pay (шлюзи расмӣ)",
    "dc": "Dushanbe City Bank (шлюзи расмӣ)",
    "referral_balance": "💰 Баланси реферал",
}


def payment_method_label(payment_provider: str) -> str:
    return PAYMENT_METHOD_LABELS.get(payment_provider, payment_provider)


def format_recipient(ff_player_id: str, recipient_extra: str | None = None) -> str:
    """PUBG Mobile orders carry a Server ID alongside the Player ID —
    every other category only ever has recipient_extra=None. Used
    anywhere an order's recipient is shown (confirmation screens, admin
    notifications, /pending, /myorders)."""
    if recipient_extra:
        return f"{ff_player_id} (Server: {recipient_extra})"
    return ff_player_id


# Human-readable category name for admin notification captions (🆕
# Фармоиши #.../Товар/Категория) — see bot/handlers/customer.py:
# receive_payment_proof and pay_with_balance.
CATEGORY_DISPLAY_NAMES: dict[str, str] = {
    "diamonds": "Free Fire (СНГ)",
    "telegram": "Telegram Stars",
    "pubg": "PUBG Mobile",
    "standoff2": "Standoff 2",
    "ff_brazil": "Free Fire (Бразилия)",
    "ff_indonesia": "Free Fire (Индонезия)",
    "combo": "Комбо",
}


def category_display_name(category) -> str:
    key = category.value if hasattr(category, "value") else str(category)
    return CATEGORY_DISPLAY_NAMES.get(key, key)


# ═══════════════════════════════════════════════════════════════════════
# NEON / GAMING poster framing
# ═══════════════════════════════════════════════════════════════════════
# Plain Telegram text (even with HTML parse_mode) can't render literal
# glow/CSS effects — this is the closest a text message gets to a "neon
# gaming poster": a bold branded header + themed accent emoji + a divider,
# matched to the message's MEANING the same way bot/keyboards.py matches
# button color to meaning. Existing message CONTENT is never rewritten by
# this — neon_header() only builds a banner to put in front of it.
NEON_DIVIDER = "━━━━━━━━━━━━━━━"

_NEON_THEMES: dict[str, str] = {
    "brand": "💎⚡💎",  # welcome / general branding
    "diamonds": "💎🔷✨",  # buying diamonds/UC/Gold — product screens
    "payment": "💚💰💚",  # payment instructions / paid confirmation
    "warning": "🟠⚠️🟠",  # caution / please double-check
    "error": "🔴❌🔴",  # rejected / failed
    "success": "🟢✅🟢",  # delivered / confirmed
    "admin": "👑💜👑",  # admin-facing alerts
    "news": "🔵📢🔵",  # announcements / broadcasts
}


def neon_header(title: str, theme: str = "brand") -> str:
    accent = _NEON_THEMES.get(theme, _NEON_THEMES["brand"])
    return f"{accent}\n<b>{title}</b>\n{NEON_DIVIDER}"
