import os
import re
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _int_list(raw: str) -> list[int]:
    # Accept comma- AND/OR whitespace-separated IDs, not just commas — a
    # value pasted twice by mistake in Render's dashboard (e.g. "123 123",
    # no comma) used to crash the whole bot on startup with an opaque
    # ValueError instead of just being parsed.
    tokens = re.split(r"[,\s]+", raw.strip())
    return [int(t) for t in tokens if t]


def _first_int(raw: str, default: int = 0) -> int:
    values = _int_list(raw)
    return values[0] if values else default


def _normalize_database_url(raw: str) -> str:
    """Supabase (and most providers) hand out a plain "postgresql://" or
    "postgres://" URI meant for psycopg2/libpq — SQLAlchemy needs the
    "+asyncpg" driver suffix to use it from async code, and psycopg2 isn't
    even installed here. Rewrite the scheme automatically instead of
    relying on a manual find-replace step that's easy to skip and fails
    with a confusing "No module named 'psycopg2'" traceback instead of a
    clear error."""
    if not raw:
        return raw
    if raw.startswith("postgres://"):
        return "postgresql+asyncpg://" + raw[len("postgres://"):]
    if raw.startswith("postgresql://"):
        return "postgresql+asyncpg://" + raw[len("postgresql://"):]
    return raw


@dataclass(frozen=True)
class Config:
    bot_token: str = os.getenv("BOT_TOKEN", "")
    admin_chat_id: int = _first_int(os.getenv("ADMIN_CHAT_ID", "0"))
    admin_user_ids: list[int] = field(
        default_factory=lambda: _int_list(os.getenv("ADMIN_USER_IDS", ""))
    )

    database_path: str = os.getenv("DATABASE_PATH", "./diamond_bot.db")
    # A real Postgres URL (e.g. from Supabase) takes over from the local
    # SQLite file when set — SQLite alone is fine for local development,
    # but on Render's free tier the disk is wiped on every deploy, taking
    # every user/order/product edit with it. See README for the Supabase
    # setup steps.
    database_url: str = field(
        default_factory=lambda: _normalize_database_url(os.getenv("DATABASE_URL", ""))
    )

    # Public URL Render (or any other host) gives your service — REQUIRED
    # in Render's Environment tab for webhook mode + the self-ping
    # keepalive (see main.py) to work. Left empty on purpose as the
    # fallback (rather than a guessed/hardcoded Render URL): a wrong
    # hardcoded URL here would silently register the Telegram webhook, and
    # self-ping, against a domain that isn't even this service — the bot
    # would then never receive updates at all, which is far worse and
    # harder to notice than just running in polling mode. Empty means
    # "PUBLIC_URL isn't set" -> main.py safely falls back to polling.
    public_url: str = os.getenv("PUBLIC_URL", "")
    telegram_webhook_path: str = os.getenv("TELEGRAM_WEBHOOK_PATH", "/tg-webhook")
    telegram_webhook_secret: str = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
    # Render sets $PORT itself at runtime; 8080 is just a local fallback.
    port: int = int(os.getenv("PORT", "8080"))

    payment_provider: str = os.getenv("PAYMENT_PROVIDER", "manual")
    # Your own card that customers pay into — shown as plain text on the
    # "💳 ДС" screen and used to build the pre-filled DC pay-by-link below.
    # `os.getenv(KEY) or default` on purpose, NOT `os.getenv(KEY, default)`
    # — the latter only falls back when the env var is completely absent;
    # if Render has RECEIVING_CARD_NUMBER set but BLANK (e.g. left over
    # from an earlier edit), `os.getenv(KEY, default)` returns that empty
    # string as-is, and every payment screen below then shows "рақами
    # корти қабулкунанда танзим нашудааст" instead of ever using the real
    # card. `or` treats blank the same as absent, so the real default
    # always wins unless a real value is actually configured.
    receiving_card_number: str = os.getenv("RECEIVING_CARD_NUMBER") or "9762000199761387"
    # DC Bank's own card-to-card "tap to pay" portal (pay.dc.tj) — opens
    # with the receiving card + exact order amount pre-filled, so the
    # customer just taps "💳 Пардохт" and confirms instead of typing it by
    # hand. All parts (domain, "c" code, "f1") come from a real working
    # link for this shop's DC card. Falls back to the old EXPRESSPAY_*
    # env var names first, in case those were already set on Render — no
    # change needed there if so.
    dc_pay_base_url: str = os.getenv("DC_PAY_BASE_URL") or os.getenv("EXPRESSPAY_BASE_URL") or "https://pay.dc.tj/"
    # "c" identifies this shop's card registration with DC's payment
    # portal — fixed per shop (confirmed from a real working link), not
    # per order; DC's page itself doesn't hand it back to us afterwards,
    # so there's nothing to match against later either way.
    dc_pay_card_code: str = os.getenv("DC_PAY_CARD_CODE") or "almazshop_01"
    # Required — the page errors with "one of the parameters is empty"
    # without it. "133" is the value copied from a real working link;
    # its actual meaning (service/tariff code?) is unconfirmed.
    dc_pay_f1: str = os.getenv("DC_PAY_F1") or os.getenv("EXPRESSPAY_F1") or "133"
    # Alif Mobi's in-app "provider" bill-payment deep link — opens the
    # Alif Mobi app directly to this shop's registered provider entry with
    # the exact order amount pre-filled. id = the provider entry, account
    # = this shop's registered account within that entry (both real
    # values from a working link, not guessed).
    alif_mobi_base_url: str = os.getenv("ALIF_MOBI_BASE_URL") or "https://alifmobi.page.link/providers"
    alif_mobi_provider_id: str = os.getenv("ALIF_MOBI_PROVIDER_ID") or "124"
    alif_mobi_account: str = os.getenv("ALIF_MOBI_ACCOUNT") or "976820008"
    alif_shop_id: str = os.getenv("ALIF_SHOP_ID", "")
    alif_secret_key: str = os.getenv("ALIF_SECRET_KEY", "")
    alif_api_base_url: str = os.getenv("ALIF_API_BASE_URL", "")
    alif_callback_path: str = os.getenv("ALIF_CALLBACK_PATH", "/webhooks/alif")
    # The phone number shown as plain text alongside the tap-to-pay Alif
    # Mobi link above (in case the customer's Alif Mobi app isn't
    # installed and they need to send manually instead) — and the same
    # number Амонатбонк uses, since both land in the same underlying
    # account. Falls back to the old DC_TRANSFER_NUMBER/ALIF_CARD_NUMBER
    # env var names first — no change needed on Render if those were
    # already set.
    mobile_transfer_number: str = (
        os.getenv("MOBILE_TRANSFER_NUMBER")
        or os.getenv("DC_TRANSFER_NUMBER")
        or os.getenv("ALIF_CARD_NUMBER")
        or "976820008"
    )
    # Карти "💳 ДС (Душанбе Сити)" бошад — ин ҳамон receiving_card_number-и
    # болост (9762000199761387), рақами корти алоҳида лозим нест.
    dc_shop_id: str = os.getenv("DC_SHOP_ID", "")
    dc_secret_key: str = os.getenv("DC_SECRET_KEY", "")
    dc_api_base_url: str = os.getenv("DC_API_BASE_URL", "")
    dc_callback_path: str = os.getenv("DC_CALLBACK_PATH", "/webhooks/dc")
    webhook_server_host: str = os.getenv("WEBHOOK_SERVER_HOST", "0.0.0.0")
    webhook_server_port: int = int(os.getenv("WEBHOOK_SERVER_PORT", "8080"))

    # Render's free tier spins the service down after ~15 minutes with no
    # incoming HTTP traffic. Every KEEPALIVE_PING_SECONDS, the bot pings its
    # own PUBLIC_URL/health to look "active" and avoid that. 300s (5 min)
    # gives a comfortable safety margin under the 15-minute cutoff even if
    # a ping is briefly delayed or dropped — this DOES NOT WORK AT ALL if
    # PUBLIC_URL is left empty (see main.py:keepalive_loop), so that must
    # be set on Render for this to have any effect. Set to "0" to disable
    # entirely (e.g. on a paid plan that doesn't sleep).
    keepalive_ping_seconds: int = int(os.getenv("KEEPALIVE_PING_SECONDS", "300"))

    delivery_provider: str = os.getenv("DELIVERY_PROVIDER", "manual")
    supplier_api_base_url: str = os.getenv("SUPPLIER_API_BASE_URL", "")
    supplier_api_key: str = os.getenv("SUPPLIER_API_KEY", "")

    # FazerCards reseller API (https://api.fzr.cards) — set DELIVERY_PROVIDER=fazercards
    # once products are mapped to real category/offer IDs (see /fzr_categories,
    # /fzr_offers, /mapproduct admin commands).
    fazercards_api_key: str = os.getenv("FAZERCARDS_API_KEY", "")
    fazercards_api_base_url: str = os.getenv("FAZERCARDS_API_BASE_URL", "https://api.fzr.cards")

    # SMS-based auto payment confirmation: an app on the admin's phone
    # forwards incoming "Zachislenie" (deposit) SMS from the bank to this
    # webhook. Empty secret disables the endpoint entirely.
    sms_webhook_path: str = os.getenv("SMS_WEBHOOK_PATH", "/sms-webhook")
    sms_webhook_secret: str = os.getenv("SMS_WEBHOOK_SECRET", "")
    sms_match_window_minutes: int = int(os.getenv("SMS_MATCH_WINDOW_MINUTES", "60"))

    # Public review chat: bot must be added there (as admin with "Post
    # Messages" permission if it's a channel) otherwise announcements
    # silently fail.
    shop_channel_url: str = os.getenv("SHOP_CHANNEL_URL", "https://t.me/otziv_chat_almaz_shop_bot")
    review_channel_id: str = os.getenv("REVIEW_CHANNEL_ID", "@otziv_chat_almaz_shop_bot")

    # Force-Join gate (see bot/middlewares.py): the bot refuses to do
    # anything else — including /start — until the user is a confirmed
    # member of this channel. Left EMPTY (disabled) by default on purpose:
    # a placeholder channel here that the bot isn't actually an admin of
    # makes bot.get_chat_member() fail for every non-admin user, which the
    # gate then treats as "not subscribed" — every regular customer's
    # /start silently gets the join-gate screen instead of the main menu
    # (admins always bypass the gate, so this is easy to miss while
    # testing as the owner). Set CHANNEL_USERNAME in Render's Environment
    # tab to a real channel the bot is an admin of to turn the gate back
    # on. CHANNEL_USERNAME must be in "@handle" form — that's what
    # bot.get_chat_member() expects as chat_id. CHANNEL_URL is the public
    # t.me link shown on the "📢 Join Channel" button; if left blank it's
    # derived automatically from CHANNEL_USERNAME.
    channel_username: str = os.getenv("CHANNEL_USERNAME", "")
    channel_url: str = field(
        default_factory=lambda: os.getenv("CHANNEL_URL", "")
        or (
            f"https://t.me/{os.getenv('CHANNEL_USERNAME', '').lstrip('@')}"
            if os.getenv("CHANNEL_USERNAME")
            else ""
        )
    )

    # wa.me link opens WhatsApp directly to a chat with this contact.
    contact_whatsapp_url: str = os.getenv("CONTACT_WHATSAPP_URL", "https://wa.me/qr/D3W6PIWVWZSYD1")
    contact_instagram_url: str = os.getenv(
        "CONTACT_INSTAGRAM_URL", "https://www.instagram.com/almazzshop?igsh=ZTF1eHhubDloNmxu"
    )


config = Config()
