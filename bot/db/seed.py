"""Default product catalog, seeded automatically on first run.

Values taken directly from the price list image the admin provided. Cost
(харид/purchase price) is unknown, so it's set equal to the sale price as
a neutral placeholder — that's only used for the admin's own profit view
(/products) and is never shown to customers. Tell the bot the real
wholesale cost and it'll be corrected.
"""

from sqlalchemy import select

from bot.db.models import Product, ProductCategory

# Telegram Stars packages are intentionally not seeded here — unlike the
# Free Fire prices above (given directly by the admin), no real Stars
# pricing was provided, and seeding made-up numbers would show customers
# a price nobody decided on. Add real ones with /addstars.
DEFAULT_PRODUCTS = [
    # (name, diamonds, bonus_diamonds, price_somoni)
    ("100 DIAMOND", 100, 10, 9),
    ("310 DIAMOND", 310, 31, 27.50),
    ("520 DIAMOND", 520, 52, 48.50),
    ("1060 DIAMOND", 1060, 106, 90.50),
    ("2180 DIAMOND", 2180, 218, 180.50),
    ("5600 DIAMOND", 5600, 560, 485.00),
    ("Ваучери Lite", 90, 0, 6.50),
    ("Ваучери weeky", 450, 0, 17.50),
    ("Ваучери Monther", 2600, 0, 90.00),
    ("Evo Access 3D",0,0,6.90),
    ("Evo Access 7D",0,0,9.90),
    ("Evo Access 30D",0,0,24.90),
]


async def seed_default_products(session) -> None:
    # Per-item, not all-or-nothing: if the catalog was already seeded
    # before an item (e.g. the vouchers) was added to DEFAULT_PRODUCTS
    # above, skipping seeding entirely just because *something* exists
    # left that item permanently missing from every deploy since. Matching
    # by name still never touches — let alone overwrites — anything the
    # admin has already added or edited (including price/bonus changes
    # made via /setprice or /setbonus on a name that's already there).
    result = await session.execute(select(Product.name))
    existing_names = {name for (name,) in result.all()}

    added = False
    for name, diamonds, bonus_diamonds, price in DEFAULT_PRODUCTS:
        if name in existing_names:
            continue
        session.add(
            Product(
                name=name,
                category=ProductCategory.DIAMONDS,
                diamonds=diamonds,
                bonus_diamonds=bonus_diamonds,
                price_somoni=price,
                cost_somoni=price,
            )
        )
        added = True

    if added:
        await session.commit()
