import enum
from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, Enum, Float, ForeignKey, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class OrderStatus(str, enum.Enum):
    AWAITING_PAYMENT = "awaiting_payment"
    PAID = "paid"
    DELIVERING = "delivering"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ProductCategory(str, enum.Enum):
    DIAMONDS = "diamonds"  # Free Fire (CIS) diamonds — recipient is a player ID
    TELEGRAM = "telegram"  # Telegram Stars/Premium — recipient is a @username
    PUBG = "pubg"  # PUBG Mobile UC — recipient is Player ID + Server ID
    STANDOFF2 = "standoff2"  # Standoff 2 Gold — recipient is a USER ID
    FF_BRAZIL = "ff_brazil"  # Free Fire (Brazil) diamonds — recipient is a player ID
    FF_INDONESIA = "ff_indonesia"  # Free Fire (Indonesia) diamonds — recipient is a player ID
    COMBO = "combo"  # One-time-per-account combo packs (e.g. level-up pass)


# Member NAMEs (not .value) are what SQLAlchemy's Enum type stores in the
# database column by default — every member name here is kept to 16 chars
# or fewer on purpose, to stay inside whatever width bot_products.category
# already has in a live database (see the defensive column-widening in
# bot/db/session.py, which additionally guards against this on Postgres).
assert all(len(member.name) <= 16 for member in ProductCategory)

# Display unit shown next to a plain (digit-named) pack's amount, e.g.
# "310💎" or "660 UC" — see Product.unit_label and
# bot/keyboards.py:_product_label.
_UNIT_LABELS: dict[ProductCategory, str] = {
    ProductCategory.DIAMONDS: "💎",
    ProductCategory.TELEGRAM: "⭐",
    ProductCategory.PUBG: "UC",
    ProductCategory.STANDOFF2: "G",
    ProductCategory.FF_BRAZIL: "💎",
    ProductCategory.FF_INDONESIA: "💎",
    ProductCategory.COMBO: "💎",
}


class User(Base):
    # Prefixed (not just "users") to never collide with a table Supabase's
    # own project templates create — a fresh Supabase project created from
    # the "User Management Starter" quickstart ships a public.users table
    # with a UUID primary key linked to auth.users; create_all() sees a
    # table already named "users" and skips creating ours, leaving every
    # query here trying to compare our bigint Telegram IDs against that
    # table's UUID id ("operator does not exist: uuid = bigint").
    __tablename__ = "bot_users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # Telegram user id
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    accepted_terms_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    referred_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("bot_users.id"), nullable=True
    )
    referral_balance: Mapped[float] = mapped_column(Float, default=0.0)

    orders: Mapped[list["Order"]] = relationship(back_populates="user")


class Product(Base):
    """A diamond/UC/Gold package, voucher, or combo the bot sells."""

    __tablename__ = "bot_products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64))
    category: Mapped[ProductCategory] = mapped_column(
        Enum(ProductCategory), default=ProductCategory.DIAMONDS
    )
    diamonds: Mapped[int] = mapped_column(Integer)  # unit count: diamonds, UC, Gold, or Stars
    # Extra units the supplier throws in on top of `diamonds` for this pack
    # (e.g. FazerCards' "110_diamonds" offer for a 100-pack = 10 bonus) —
    # set automatically by /mapproduct from the live offer, so the bot can
    # advertise the same bonus the supplier's own site shows.
    bonus_diamonds: Mapped[int] = mapped_column(Integer, default=0)
    price_somoni: Mapped[float] = mapped_column(Float)
    cost_somoni: Mapped[float] = mapped_column(Float, default=0.0)
    is_active: Mapped[bool] = mapped_column(default=True)
    # FazerCards (api.fzr.cards) mapping — set via /mapproduct once known
    # from /fzr_categories + /fzr_offers. Empty means: no auto-delivery for
    # this product, falls back to manual fulfillment.
    fzr_category_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fzr_offer_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    @property
    def margin_somoni(self) -> float:
        return round(self.price_somoni - self.cost_somoni, 2)

    @property
    def unit_label(self) -> str:
        return _UNIT_LABELS.get(self.category, "💎")

    @property
    def total_diamonds(self) -> int:
        return self.diamonds + self.bonus_diamonds


class Order(Base):
    __tablename__ = "bot_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("bot_users.id"))
    product_id: Mapped[int] = mapped_column(Integer, ForeignKey("bot_products.id"))
    # Free Fire/Standoff 2/PUBG player ID or @username (Telegram Stars/
    # Premium), depending on the product's category.
    ff_player_id: Mapped[str] = mapped_column(String(32))
    # Second recipient field, only used by categories that need more than
    # one (currently just PUBG Mobile's Server ID). Null for everything
    # else — see bot/states.py:OrderFlow.entering_recipient_extra.
    recipient_extra: Mapped[str | None] = mapped_column(String(64), nullable=True)
    amount_somoni: Mapped[float] = mapped_column(Float)
    paid_with_referral_balance: Mapped[bool] = mapped_column(default=False)
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus), default=OrderStatus.AWAITING_PAYMENT
    )
    payment_provider: Mapped[str] = mapped_column(String(32), default="manual")
    payment_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Shared token for orders created together in one "buy several packs at
    # once" checkout — one payment/invoice covers the whole group, and
    # admin Paid/Delivered taps cascade to every order sharing this value.
    # Null for ordinary single-product orders.
    cart_group_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    admin_note: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # SHA-256 of the payment-proof photo bytes, so the same screenshot can't
    # be reused across orders without the admin being warned. Not a
    # substitute for actually checking the bank app — just catches replay.
    payment_proof_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # When the customer actually submitted a receipt (photo/document) for
    # this order — separate from payment_proof_hash (fraud-replay check,
    # photos only) so the admin can see who has sent *something* even for
    # document-type receipts, before deciding to confirm.
    proof_submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    user: Mapped["User"] = relationship(back_populates="orders")
    product: Mapped["Product"] = relationship()


class BalanceTransaction(Base):
    """One ledger line for a referral-balance change (credit or debit) —
    powers the "📜 Таърихи баланс" screen in the profile menu. `amount` is
    signed: positive for a credit (referral bonus), negative for a debit
    (paying for an order with balance)."""

    __tablename__ = "bot_balance_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("bot_users.id"))
    amount: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Giveaway(Base):
    """A community purchase-goal giveaway round, configured by the admin
    via /giveaway_start. `current_purchases` counts every DELIVERED order
    while this round is active (bot/services/fulfillment.py) — once it
    reaches `required_purchases`, `winners_count` winners are drawn at
    random from everyone who purchased during the round and the round
    closes (is_active=False, is_completed=True)."""

    __tablename__ = "bot_giveaways"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    required_purchases: Mapped[int] = mapped_column(Integer)
    prize_description: Mapped[str] = mapped_column(String(256))
    winners_count: Mapped[int] = mapped_column(Integer, default=1)
    current_purchases: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(default=True)
    is_completed: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GiveawayEntry(Base):
    """One participation record: this order counted toward this giveaway
    round for this user. Used to draw winners only from users who actually
    purchased during the round (distinct user_id)."""

    __tablename__ = "bot_giveaway_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    giveaway_id: Mapped[int] = mapped_column(Integer, ForeignKey("bot_giveaways.id"))
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("bot_users.id"))
    order_id: Mapped[int] = mapped_column(Integer, ForeignKey("bot_orders.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GiveawayWinner(Base):
    """A drawn winner of a completed giveaway round — kept permanently
    (never deleted) so "🎉 То ҳол ... нафар туҳфа бурдааст" and "🏆
    Барандаи охирин" can always be shown, even long after the round that
    produced them has closed."""

    __tablename__ = "bot_giveaway_winners"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    giveaway_id: Mapped[int] = mapped_column(Integer, ForeignKey("bot_giveaways.id"))
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("bot_users.id"))
    prize_description: Mapped[str] = mapped_column(String(256))
    won_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BotSettings(Base):
    """Single-row table (id=1) for admin-configurable settings that aren't
    tied to any one product/order — e.g. the personal-card photos shown to
    customers on the payment screen, set live via /setcardphoto and
    /setalifcardphoto instead of a redeploy-only env var."""

    __tablename__ = "bot_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    card_photo_file_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    alif_card_photo_file_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    eskhata_card_photo_file_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    amonatbonk_card_photo_file_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
