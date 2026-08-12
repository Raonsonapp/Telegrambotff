"""Default product catalog, seeded automatically on first run.

Values taken directly from the price list image the admin provided. Cost
(харид/purchase price) is unknown, so it's set equal to the sale price as
a neutral placeholder — that's only used for the admin's own profit view
(/products) and is never shown to customers. Tell the bot the real
wholesale cost and it'll be corrected.

Only categories with real, admin-confirmed сомонӣ prices are seeded here:
Free Fire (CIS), PUBG Mobile, Free Fire (Indonesia), Standoff 2. Free Fire
(Brazil) and Combo are intentionally left unseeded — no real prices were
ever given for them, and seeding made-up numbers would show customers a
price nobody actually decided on. Add real ones with /addffbr, /addcombo
— see README.md for exact usage and examples.
"""

from sqlalchemy import select

from bot.db.models import Product, ProductCategory

# (name, diamonds/UC/Gold, bonus, price_somoni)
DIAMONDS_PRODUCTS = [
    ("100 DIAMOND", 100, 10, 9.0),
    ("310 DIAMOND", 310, 31, 27.50),
    ("520 DIAMOND", 520, 52, 48.50),
    ("1060 DIAMOND", 1060, 106, 90.50),
    ("2180 DIAMOND", 2180, 218, 180.50),
    ("5600 DIAMOND", 5600, 560, 485.00),
    ("Ваучери Lite", 90, 0, 6.50),
    ("Ваучери weeky", 450, 0, 17.50),
    ("Ваучери Monther", 2600, 0, 90.00),
    ("Evo Access 3D", 0, 0, 6.90),
    ("Evo Access 7D", 0, 0, 9.90),
    ("Evo Access 30D", 0, 0, 24.90),
]

PUBG_PRODUCTS = [
    ("60 UC", 60, 0, 11.0),
    ("325 UC", 325, 0, 53.0),
    ("660 UC", 660, 0, 106.0),
    ("1800 UC", 1800, 0, 266.0),
    ("3850 UC", 3850, 0, 530.0),
    ("8100 UC", 8100, 0, 1100.0),
]

FF_INDONESIA_PRODUCTS = [
    ("50 DIAMOND", 50, 0, 7.0),
    ("100 DIAMOND", 100, 0, 11.0),
    ("150 DIAMOND", 150, 0, 15.0),
    ("210 DIAMOND", 210, 0, 20.0),
    ("420 DIAMOND", 420, 0, 40.0),
    ("500 DIAMOND", 500, 0, 50.0),
    ("800 DIAMOND", 800, 0, 75.0),
    ("1000 DIAMOND", 1000, 0, 95.0),
    ("Ваучери ҳафтагӣ", 0, 0, 22.0),
    ("Ваучери моҳона", 0, 0, 90.0),
]

STANDOFF2_PRODUCTS = [
    ("100G", 100, 0, 15.0),
    ("200G", 200, 0, 28.0),
    ("300G", 300, 0, 45.0),
    ("500G", 500, 0, 70.0),
]

_SEED_TABLE: list[tuple[ProductCategory, list[tuple[str, int, int, float]]]] = [
    (ProductCategory.DIAMONDS, DIAMONDS_PRODUCTS),
    (ProductCategory.PUBG, PUBG_PRODUCTS),
    (ProductCategory.FF_INDONESIA, FF_INDONESIA_PRODUCTS),
    (ProductCategory.STANDOFF2, STANDOFF2_PRODUCTS),
]


async def seed_default_products(session) -> None:
    # Per-(category, name) item, not all-or-nothing: if the catalog was
    # already seeded before a category/item was added to this table above,
    # skipping seeding entirely just because *something* exists left that
    # item permanently missing from every deploy since. Scoped by
    # (category, name) rather than name alone — several categories reuse
    # the same plain pack name ("100 DIAMOND" exists in both DIAMONDS and
    # FF_INDONESIA, as two different real products), so name-only
    # deduplication would incorrectly skip seeding one of them. Matching
    # never touches — let alone overwrites — anything the admin has
    # already added or edited (including price/bonus changes made via
    # /setprice or /setbonus on a name that's already there).
    result = await session.execute(select(Product.category, Product.name))
    existing = {(category, name) for category, name in result.all()}

    added = False
    for category, products in _SEED_TABLE:
        for name, diamonds, bonus_diamonds, price in products:
            if (category, name) in existing:
                continue
            session.add(
                Product(
                    name=name,
                    category=category,
                    diamonds=diamonds,
                    bonus_diamonds=bonus_diamonds,
                    price_somoni=price,
                    cost_somoni=price,
                )
            )
            added = True

    if added:
        await session.commit()
