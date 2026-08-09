import random
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from bot.db.models import (
    BalanceTransaction,
    BotSettings,
    Giveaway,
    GiveawayEntry,
    GiveawayWinner,
    Order,
    OrderStatus,
    Product,
    ProductCategory,
    User,
)


async def upsert_user(
    session: AsyncSession,
    user_id: int,
    username: str | None,
    full_name: str | None,
    referred_by: int | None = None,
) -> User:
    user = await session.get(User, user_id)
    if user is None:
        if referred_by == user_id:
            referred_by = None
        user = User(id=user_id, username=username, full_name=full_name, referred_by=referred_by)
        session.add(user)
    else:
        user.username = username
        user.full_name = full_name
    await session.commit()
    return user


async def get_user(session: AsyncSession, user_id: int) -> User | None:
    return await session.get(User, user_id)


async def accept_terms(session: AsyncSession, user: User) -> User:
    user.accepted_terms_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(user)
    return user


async def credit_referral_balance(
    session: AsyncSession, user_id: int, amount: float, reason: str = "Бонуси реферал"
) -> None:
    user = await session.get(User, user_id)
    if user is not None and amount:
        user.referral_balance = round(user.referral_balance + amount, 2)
        session.add(BalanceTransaction(user_id=user_id, amount=round(amount, 2), reason=reason))
        await session.commit()


async def deduct_referral_balance(
    session: AsyncSession, user: User, amount: float, reason: str = "Пардохт аз баланси реферал"
) -> User:
    user.referral_balance = round(user.referral_balance - amount, 2)
    session.add(BalanceTransaction(user_id=user.id, amount=round(-amount, 2), reason=reason))
    await session.commit()
    await session.refresh(user)
    return user


async def list_balance_history(
    session: AsyncSession, user_id: int, limit: int = 20
) -> list[BalanceTransaction]:
    result = await session.execute(
        select(BalanceTransaction)
        .where(BalanceTransaction.user_id == user_id)
        .order_by(BalanceTransaction.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def count_referrals(session: AsyncSession, user_id: int) -> int:
    result = await session.execute(
        select(func.count()).select_from(User).where(User.referred_by == user_id)
    )
    return result.scalar_one()


async def top_referrers(session: AsyncSession, limit: int = 10) -> list[tuple[User, int]]:
    referred = aliased(User)
    stmt = (
        select(User, func.count(referred.id).label("referral_count"))
        .join(referred, referred.referred_by == User.id)
        .group_by(User.id)
        .order_by(func.count(referred.id).desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return [(row[0], row[1]) for row in result.all()]


async def list_active_products(
    session: AsyncSession, category: ProductCategory | None = None
) -> list[Product]:
    stmt = select(Product).where(Product.is_active.is_(True))
    if category is not None:
        stmt = stmt.where(Product.category == category)
    result = await session.execute(stmt)
    products = list(result.scalars().all())
    # Plain packs sort by size; vouchers (name doesn't start with a digit —
    # same test bot/keyboards.py uses to tell them apart) always sort after
    # every pack, instead of landing wherever their diamond-equivalent
    # number happens to fall among the packs.
    products.sort(key=lambda p: (0 if p.name[:1].isdigit() else 1, p.diamonds))
    return products


async def get_product(session: AsyncSession, product_id: int) -> Product | None:
    return await session.get(Product, product_id)


async def set_product_fzr_mapping(
    session: AsyncSession, product: Product, category_id: str, offer_id: str
) -> Product:
    product.fzr_category_id = category_id
    product.fzr_offer_id = offer_id
    await session.commit()
    await session.refresh(product)
    return product


async def set_product_bonus(session: AsyncSession, product: Product, bonus_diamonds: int) -> Product:
    product.bonus_diamonds = max(0, bonus_diamonds)
    await session.commit()
    await session.refresh(product)
    return product


async def set_product_price(
    session: AsyncSession, product: Product, price_somoni: float, cost_somoni: float | None = None
) -> Product:
    product.price_somoni = price_somoni
    if cost_somoni is not None:
        product.cost_somoni = cost_somoni
    # A product created with no real price yet (see bot/handlers/admin.py:
    # _add_product) is left inactive on purpose so it can't accidentally
    # sell for 0 сомонӣ — giving it a real positive price here is exactly
    # the admin's "okay, this one's ready" signal, so reactivate it in the
    # same step instead of requiring a separate command. Symmetrically,
    # setting it back to 0/negative takes it back offline.
    product.is_active = price_somoni > 0
    await session.commit()
    await session.refresh(product)
    return product


async def create_order(
    session: AsyncSession,
    user_id: int,
    product: Product,
    ff_player_id: str,
    payment_provider: str,
    paid_with_referral_balance: bool = False,
    cart_group_id: str | None = None,
    recipient_extra: str | None = None,
) -> Order:
    order = Order(
        user_id=user_id,
        product_id=product.id,
        ff_player_id=ff_player_id,
        recipient_extra=recipient_extra,
        amount_somoni=product.price_somoni,
        payment_provider=payment_provider,
        status=OrderStatus.PAID if paid_with_referral_balance else OrderStatus.AWAITING_PAYMENT,
        paid_with_referral_balance=paid_with_referral_balance,
        cart_group_id=cart_group_id,
    )
    session.add(order)
    await session.commit()
    await session.refresh(order)
    return order


async def get_orders_by_group(session: AsyncSession, cart_group_id: str) -> list[Order]:
    result = await session.execute(
        select(Order).where(Order.cart_group_id == cart_group_id).order_by(Order.id)
    )
    return list(result.scalars().all())


async def get_order(session: AsyncSession, order_id: int) -> Order | None:
    result = await session.execute(
        select(Order).where(Order.id == order_id)
    )
    return result.scalar_one_or_none()


async def get_last_recipient(
    session: AsyncSession, user_id: int, category: ProductCategory
) -> str | None:
    """Most recent recipient (player/user ID or Telegram @username) this
    user ordered something for, in this category — lets the bot offer a
    "use the same one again" shortcut instead of retyping every time."""
    result = await session.execute(
        select(Order.ff_player_id)
        .join(Product, Product.id == Order.product_id)
        .where(Order.user_id == user_id, Product.category == category)
        .order_by(Order.created_at.desc())
        .limit(1)
    )
    return result.scalars().first()


async def list_orders_by_status(session: AsyncSession, status: OrderStatus) -> list[Order]:
    result = await session.execute(
        select(Order).where(Order.status == status).order_by(Order.created_at)
    )
    return list(result.scalars().all())


async def list_recent_orders_by_user(
    session: AsyncSession, user_id: int, limit: int = 10
) -> list[Order]:
    result = await session.execute(
        select(Order)
        .where(Order.user_id == user_id)
        .order_by(Order.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def find_orders_awaiting_amount(
    session: AsyncSession, amount_somoni: float, since: datetime
) -> list[Order]:
    """Candidate orders an incoming bank SMS of this amount could be for."""
    result = await session.execute(
        select(Order).where(
            Order.status == OrderStatus.AWAITING_PAYMENT,
            Order.created_at >= since,
            func.abs(Order.amount_somoni - amount_somoni) < 0.01,
        )
    )
    return list(result.scalars().all())


async def find_order_by_payment_reference(session: AsyncSession, reference: str) -> Order | None:
    result = await session.execute(select(Order).where(Order.payment_reference == reference))
    return result.scalars().first()


async def set_payment_proof_hash(session: AsyncSession, order: Order, proof_hash: str) -> Order:
    order.payment_proof_hash = proof_hash
    await session.commit()
    await session.refresh(order)
    return order


async def find_duplicate_proof(
    session: AsyncSession, proof_hash: str, exclude_order_id: int
) -> Order | None:
    result = await session.execute(
        select(Order).where(
            Order.payment_proof_hash == proof_hash,
            Order.id != exclude_order_id,
        )
    )
    return result.scalars().first()


async def set_proof_submitted(session: AsyncSession, order: Order) -> Order:
    order.proof_submitted_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(order)
    return order


def _proof_submitted_filter():
    # proof_submitted_at only exists going forward from when this tracking
    # was added — but payment_proof_hash has been recorded (for photo
    # receipts specifically) since well before that, so OR-ing it in
    # recovers every historical photo receipt too, not just new ones.
    # Receipts sent as plain text/document before this tracking existed
    # have no surviving marker and can't be recovered.
    return or_(Order.proof_submitted_at.is_not(None), Order.payment_proof_hash.is_not(None))


async def list_proofs_submitted(session: AsyncSession, limit: int = 30) -> list[tuple[Order, User]]:
    order_time = func.coalesce(Order.proof_submitted_at, Order.created_at)
    result = await session.execute(
        select(Order, User)
        .join(User, User.id == Order.user_id)
        .where(_proof_submitted_filter())
        .order_by(order_time.desc())
        .limit(limit)
    )
    return [(o, u) for o, u in result.all()]


async def count_proofs_submitted(session: AsyncSession) -> int:
    result = await session.execute(
        select(func.count()).select_from(Order).where(_proof_submitted_filter())
    )
    return result.scalar_one()


async def set_order_status(
    session: AsyncSession,
    order: Order,
    status: OrderStatus,
    admin_note: str | None = None,
    payment_reference: str | None = None,
) -> Order:
    order.status = status
    if admin_note is not None:
        order.admin_note = admin_note
    if payment_reference is not None:
        order.payment_reference = payment_reference
    await session.commit()
    await session.refresh(order)
    return order


async def get_user_purchase_stats(session: AsyncSession, user_id: int) -> tuple[int, float]:
    result = await session.execute(
        select(func.count(Order.id), func.coalesce(func.sum(Order.amount_somoni), 0.0)).where(
            Order.user_id == user_id, Order.status == OrderStatus.DELIVERED
        )
    )
    count, total = result.one()
    return count, float(total)


async def top_buyers(session: AsyncSession, limit: int = 10) -> list[tuple[User, int, float]]:
    stmt = (
        select(
            User,
            func.count(Order.id).label("purchase_count"),
            func.coalesce(func.sum(Order.amount_somoni), 0.0).label("total_spent"),
        )
        .join(Order, Order.user_id == User.id)
        .where(Order.status == OrderStatus.DELIVERED)
        .group_by(User.id)
        .order_by(func.count(Order.id).desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return [(row[0], row[1], float(row[2])) for row in result.all()]


async def get_buyer_rank(session: AsyncSession, user_id: int) -> int | None:
    stmt = (
        select(Order.user_id, func.count(Order.id).label("cnt"))
        .where(Order.status == OrderStatus.DELIVERED)
        .group_by(Order.user_id)
        .order_by(func.count(Order.id).desc())
    )
    result = await session.execute(stmt)
    for idx, (uid, _cnt) in enumerate(result.all(), start=1):
        if uid == user_id:
            return idx
    return None


async def count_total_users(session: AsyncSession) -> int:
    result = await session.execute(select(func.count()).select_from(User))
    return result.scalar_one()


async def count_total_delivered_orders(session: AsyncSession) -> int:
    result = await session.execute(
        select(func.count()).select_from(Order).where(Order.status == OrderStatus.DELIVERED)
    )
    return result.scalar_one()


async def has_combo_purchase(session: AsyncSession, ff_player_id: str) -> bool:
    """True if this player ID already has any non-cancelled/failed Combo
    order — enforces "як аккаунт танҳо як бор" (one account, one Combo,
    ever) across every Combo tier, not just the specific tier being
    bought. Checked both when the customer types their ID and again right
    before the order is actually created, to close the (tiny) race window
    between the two."""
    result = await session.execute(
        select(func.count())
        .select_from(Order)
        .join(Product, Product.id == Order.product_id)
        .where(
            Product.category == ProductCategory.COMBO,
            Order.ff_player_id == ff_player_id,
            Order.status.not_in([OrderStatus.CANCELLED, OrderStatus.FAILED]),
        )
    )
    return result.scalar_one() > 0


async def _get_settings(session: AsyncSession) -> BotSettings:
    settings = await session.get(BotSettings, 1)
    if settings is None:
        settings = BotSettings(id=1)
        session.add(settings)
        await session.commit()
        await session.refresh(settings)
    return settings


async def get_card_photo_file_id(session: AsyncSession) -> str | None:
    settings = await _get_settings(session)
    return settings.card_photo_file_id


async def set_card_photo_file_id(session: AsyncSession, file_id: str) -> None:
    settings = await _get_settings(session)
    settings.card_photo_file_id = file_id
    await session.commit()


async def get_alif_card_photo_file_id(session: AsyncSession) -> str | None:
    settings = await _get_settings(session)
    return settings.alif_card_photo_file_id


async def set_alif_card_photo_file_id(session: AsyncSession, file_id: str) -> None:
    settings = await _get_settings(session)
    settings.alif_card_photo_file_id = file_id
    await session.commit()


async def get_eskhata_card_photo_file_id(session: AsyncSession) -> str | None:
    settings = await _get_settings(session)
    return settings.eskhata_card_photo_file_id


async def set_eskhata_card_photo_file_id(session: AsyncSession, file_id: str) -> None:
    settings = await _get_settings(session)
    settings.eskhata_card_photo_file_id = file_id
    await session.commit()


async def get_amonatbonk_card_photo_file_id(session: AsyncSession) -> str | None:
    settings = await _get_settings(session)
    return settings.amonatbonk_card_photo_file_id


async def set_amonatbonk_card_photo_file_id(session: AsyncSession, file_id: str) -> None:
    settings = await _get_settings(session)
    settings.amonatbonk_card_photo_file_id = file_id
    await session.commit()


# ---------------------------------------------------------------------------
# Giveaway
# ---------------------------------------------------------------------------


async def get_active_giveaway(session: AsyncSession) -> Giveaway | None:
    result = await session.execute(select(Giveaway).where(Giveaway.is_active.is_(True)))
    return result.scalars().first()


async def create_giveaway(
    session: AsyncSession, required_purchases: int, prize_description: str, winners_count: int
) -> Giveaway:
    giveaway = Giveaway(
        required_purchases=required_purchases,
        prize_description=prize_description,
        winners_count=max(1, winners_count),
        current_purchases=0,
        is_active=True,
        is_completed=False,
    )
    session.add(giveaway)
    await session.commit()
    await session.refresh(giveaway)
    return giveaway


async def stop_giveaway(session: AsyncSession, giveaway: Giveaway) -> None:
    giveaway.is_active = False
    await session.commit()


async def record_giveaway_entry(
    session: AsyncSession, order: Order
) -> tuple[Giveaway, list[int]] | None:
    """Call once per DELIVERED order. Increments the active giveaway's
    progress and logs the participation; if this purchase brings the count
    up to the required threshold, randomly draws winners from every
    distinct user who participated in this round, records them, closes the
    round, and returns (giveaway, winner_user_ids) so the caller can notify
    everyone. Returns None on every call that doesn't trigger a draw
    (including when there's no active giveaway at all)."""
    giveaway = await get_active_giveaway(session)
    if giveaway is None:
        return None

    giveaway.current_purchases += 1
    session.add(GiveawayEntry(giveaway_id=giveaway.id, user_id=order.user_id, order_id=order.id))

    if giveaway.current_purchases < giveaway.required_purchases:
        await session.commit()
        return None

    result = await session.execute(
        select(GiveawayEntry.user_id).where(GiveawayEntry.giveaway_id == giveaway.id).distinct()
    )
    participant_ids = [row[0] for row in result.all()]
    winners_count = min(giveaway.winners_count, len(participant_ids))
    winner_ids = random.sample(participant_ids, winners_count) if winners_count else []

    for uid in winner_ids:
        session.add(
            GiveawayWinner(
                giveaway_id=giveaway.id, user_id=uid, prize_description=giveaway.prize_description
            )
        )

    giveaway.is_active = False
    giveaway.is_completed = True
    giveaway.completed_at = datetime.now(timezone.utc)
    await session.commit()

    return (giveaway, winner_ids) if winner_ids else None


async def get_last_giveaway_winner(session: AsyncSession) -> GiveawayWinner | None:
    result = await session.execute(
        select(GiveawayWinner).order_by(GiveawayWinner.won_at.desc()).limit(1)
    )
    return result.scalars().first()


async def count_total_giveaway_winners(session: AsyncSession) -> int:
    result = await session.execute(select(func.count()).select_from(GiveawayWinner))
    return result.scalar_one()
