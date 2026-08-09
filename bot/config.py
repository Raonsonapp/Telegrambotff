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
    # Your own card that customers pay into — shown as plain text and (if
    # set) used to build a pre-filled ExpressPay pay-by-link so the
    # customer doesn't have to type the card number/amount by hand.
    receiving_card_number: str = os.getenv("RECEIVING_CARD_NUMBER", "9762000199761387")
    # Plain http:// on purpose — pay.expresspay.tj's TLS cert doesn't match
    # this hostname (ERR_CERT_COMMON_NAME_INVALID over https); the reference
    # bot's real working link used http:// too.
    expresspay_base_url: str = os.getenv("EXPRESSPAY_BASE_URL", "http://pay.expresspay.tj/")
    # Required — the page errors with "one of the parameters is empty"
    # without it. "133" is the value copied from a real working link;
    # its actual meaning (service/tariff code?) is unconfirmed. If
    # ExpressPay ever tells you the correct value for your own account,
    # override it here.
    expresspay_f1: str = os.getenv("EXPRESSPAY_F1", "133")
    alif_shop_id: str = os.getenv("ALIF_SHOP_ID", "")
    alif_secret_key: str = os.getenv("ALIF_SECRET_KEY", "")
    alif_api_base_url: str = os.getenv("ALIF_API_BASE_URL", "")
    alif_callback_path: str = os.getenv("ALIF_CALLBACK_PATH", "/webhooks/alif")
    # Card/phone number that receives Alif Mobi transfers — shown to the
    # customer on the "💳 Алиф" manual payment screen (see
    # bot/services/payments.py:AlifManualProvider). This is a *manual*
    # transfer method (same admin-confirmed proof flow as the default card
    # method), not the real Alif Business gateway above — falls back to
    # receiving_card_number if left empty.
    # Alif, Эсхата, ва Амонатбонк — ҳар се ба ҳамин ЯК рақами мобилӣ
    # мераванд (тасдиқи соҳиби дӯкон) — ин манбаи ягонаи он рақам барои
    # ҳар се усул (бинед bot/services/payments.py:AlifManualProvider /
    # EskhataManualProvider / AmonatbonkManualProvider). Агар
    # MOBILE_TRANSFER_NUMBER дар Render гузошта нашуда бошад, аввал ба
    # арзишҳои кӯҳнаи DC_TRANSFER_NUMBER/ALIF_CARD_NUMBER (агар аз пеш
    # гузошта шуда бошанд) бармегардад — ҳеҷ тағйире дар Render лозим
    # нест — баъд ба рақами воқеӣ, ки шумо додед.
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
    # own PUBLIC_URL to look "active" and avoid that — set to "0" to disable
    # (e.g. on a paid plan that doesn't sleep).
    keepalive_ping_seconds: int = int(os.getenv("KEEPALIVE_PING_SECONDS", "600"))

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
    # anything else until the user is a confirmed member of this channel.
    # CHANNEL_USERNAME must be in "@handle" form — that's what
    # bot.get_chat_member() expects as chat_id. CHANNEL_URL is the public
    # t.me link shown on the "📢 Join Channel" button; if left blank it's
    # derived automatically from CHANNEL_USERNAME. Leave CHANNEL_USERNAME
    # empty to disable the gate entirely (e.g. while testing locally).
    channel_username: str = os.getenv("CHANNEL_USERNAME", "@otziv_chat_almaz_shop_bot")
    channel_url: str = field(
        default_factory=lambda: os.getenv("CHANNEL_URL", "")
        or (
            f"https://t.me/{os.getenv('CHANNEL_USERNAME', '@otziv_chat_almaz_shop_bot').lstrip('@')}"
        )
    )

    # wa.me link opens WhatsApp directly to a chat with this contact.
    contact_whatsapp_url: str = os.getenv("CONTACT_WHATSAPP_URL", "https://wa.me/qr/D3W6PIWVWZSYD1")
    contact_instagram_url: str = os.getenv(
        "CONTACT_INSTAGRAM_URL", "https://www.instagram.com/almazzshop?igsh=ZTF1eHhubDloNmxu"
    )


config = Config()
