import html
import uuid

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.config import config
from bot.db.models import Product, ProductCategory
from bot.db.repo import (
    accept_terms,
    count_referrals,
    count_total_delivered_orders,
    count_total_giveaway_winners,
    count_total_users,
    create_order,
    deduct_referral_balance,
    get_active_giveaway,
    get_buyer_rank,
    get_last_giveaway_winner,
    get_last_recipient,
    get_product,
    get_user,
    get_user_purchase_stats,
    has_combo_purchase,
    list_active_products,
    list_balance_history,
    list_recent_orders_by_user,
    top_buyers,
    top_referrers,
    upsert_user,
)
from bot.db.session import get_session
from bot.keyboards import (
    admin_order_keyboard,
    back_to_menu_keyboard,
    cart_select_keyboard,
    confirm_order_keyboard,
    contact_keyboard,
    games_menu_keyboard,
    main_reply_keyboard,
    payment_link_keyboard,
    payment_method_keyboard,
    products_keyboard,
    profile_menu_keyboard,
    referral_menu_keyboard,
    review_channel_keyboard,
    reuse_recipient_keyboard,
    terms_keyboard,
)
from bot.middlewares import is_subscribed
from bot.services.payments import (
    AlifManualProvider,
    AmonatbonkManualProvider,
    EskhataManualProvider,
    ManualBankTransferProvider,
    get_payment_provider,
)
from bot.states import OrderFlow
from bot.texts import FAQ_TEXT, TERMS_TEXT, category_display_name, format_recipient, order_status_label, payment_method_label

router = Router(name="customer")

WELCOME_TEXT = "Хуш омадед ба ALMAZZSHOP! 💎\nМагазини фурӯши хидматҳои рақамӣ.\n\nЧиро интихоб мекунед?"

# Recipient-input prompt per category — see _recipient_prompt below.
_RECIPIENT_PROMPTS: dict[ProductCategory, str] = {
    ProductCategory.DIAMONDS: "ID-и бозингари Free Fire-и худро (рақаме, ки дар профили худ мебинед)",
    ProductCategory.FF_BRAZIL: "Player ID-и Free Fire (Бразилия)-и худро",
    ProductCategory.FF_INDONESIA: "Player ID-и Free Fire (Индонезия)-и худро",
    ProductCategory.PUBG: "Player ID-и PUBG Mobile-и худро",
    ProductCategory.STANDOFF2: "USER ID-и Standoff 2-и худро",
    ProductCategory.COMBO: "ID-и бозингари Free Fire-и худро (барои Комбо)",
    ProductCategory.TELEGRAM: "Username-и Telegram-и худро (бе @)",
}

# Catalog-screen titles per category — see _open_catalog / exit_cart_mode.
_CATALOG_TITLES: dict[ProductCategory, str] = {
    ProductCategory.DIAMONDS: "💎 Бастаи алмази Free Fire-ро интихоб кунед:",
    ProductCategory.FF_BRAZIL: "🔥 Бастаи Free Fire (Бразилия)-ро интихоб кунед:",
    ProductCategory.FF_INDONESIA: "🔥 Бастаи Free Fire (Индонезия)-ро интихоб кунед:",
    ProductCategory.PUBG: "🔫 Бастаи PUBG Mobile UC-ро интихоб кунед:",
    ProductCategory.STANDOFF2: "🎯 Бастаи Standoff 2 Gold-ро интихоб кунед:",
    ProductCategory.COMBO: "🎫 Комбои дастрасро интихоб кунед:",
    ProductCategory.TELEGRAM: "✈️ Бастаи Telegram Stars-ро интихоб кунед:",
}


async def _show_main_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Менюи зерин ҳамеша дар поён дастрас аст 👇", reply_markup=main_reply_keyboard())
    await message.answer(WELCOME_TEXT)


async def _format_orders_text(user_id: int) -> str:
    async with get_session() as session:
        orders = await list_recent_orders_by_user(session, user_id, limit=10)

    if not orders:
        return "📦 Шумо то ҳол фармоише надоред."

    lines = ["📦 Фармоишҳои охирини шумо:\n"]
    for o in orders:
        when = o.created_at.strftime("%d.%m.%Y %H:%M")
        lines.append(
            f"#{o.id} — {o.amount_somoni:.2f} сомонӣ — {order_status_label(o.status)} — {when}"
        )
    return "\n".join(lines)


async def _enter_bot(
    message: Message,
    user_id: int,
    username: str | None,
    full_name: str | None,
    state: FSMContext,
    referred_by: int | None = None,
) -> None:
    """Shared by /start and the post-subscription "✅ Check Subscription"
    callback — registers/refreshes the user, then routes them to the terms
    screen (first-time users) or straight to the main menu."""
    async with get_session() as session:
        user = await upsert_user(session, user_id, username, full_name, referred_by=referred_by)

    await state.clear()
    if user.accepted_terms_at is None:
        await message.answer(TERMS_TEXT, reply_markup=terms_keyboard())
        return

    await _show_main_menu(message, state)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    args = message.text.split(maxsplit=1)
    referred_by = None
    if len(args) > 1 and args[1].startswith("ref_"):
        try:
            referred_by = int(args[1].removeprefix("ref_"))
        except ValueError:
            referred_by = None

    await _enter_bot(
        message,
        message.from_user.id,
        message.from_user.username,
        message.from_user.full_name,
        state,
        referred_by=referred_by,
    )


@router.callback_query(F.data == "forcejoin:check")
async def check_subscription(callback: CallbackQuery, state: FSMContext) -> None:
    """Handles the "✅ Check Subscription" button from the force-join gate
    (bot/keyboards.py:force_join_keyboard, shown by bot/middlewares.py).
    Re-checks membership; only on success does the user actually get in."""
    if not await is_subscribed(callback.bot, callback.from_user.id):
        await callback.answer("❌ Шумо ҳанӯз ба канали мо обуна нашудед.", show_alert=True)
        return

    await callback.answer("✅ Ташаккур барои обуна!")
    try:
        await callback.message.edit_text("✅ Ташаккур! Шумо ба канал обуна шудед.")
    except TelegramBadRequest:
        pass
    await _enter_bot(
        callback.message,
        callback.from_user.id,
        callback.from_user.username,
        callback.from_user.full_name,
        state,
    )


@router.callback_query(F.data == "terms:accept")
async def accept_terms_cb(callback: CallbackQuery, state: FSMContext) -> None:
    async with get_session() as session:
        user = await get_user(session, callback.from_user.id)
        await accept_terms(session, user)

    await callback.message.edit_text(
        f"✅ Ташаккур! Шартнома қабул шуд.\n\n"
        f"👋 Хуш омадед, {callback.from_user.full_name}!\n"
        f"🆔 ID-и шумо: {callback.from_user.id}"
    )
    await _show_main_menu(callback.message, state)
    await callback.answer()


@router.callback_query(F.data == "menu:main")
async def menu_main(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(WELCOME_TEXT)
    await callback.answer()


# --- Persistent reply-keyboard buttons (bot/keyboards.py:main_reply_keyboard) ---
# Registered ahead of every OrderFlow-state text handler further down, so
# tapping one of these always wins over whatever mid-flow input state the
# user was in (typing a player ID, ...) — the same always-available "jump
# to a section" behavior the inline grid's buttons give, just pinned below
# the input instead of inside a specific message.


@router.message(F.text == "🎮 Бозиҳо")
async def reply_games(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("🎮 Бозиро интихоб кунед:", reply_markup=games_menu_keyboard())


@router.message(F.text == "✈️ Telegram")
async def reply_telegram(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _open_catalog_message(
        message, state, ProductCategory.TELEGRAM, _CATALOG_TITLES[ProductCategory.TELEGRAM]
    )


@router.message(F.text == "👤 Профил")
async def reply_profile(message: Message, state: FSMContext) -> None:
    await state.clear()
    async with get_session() as session:
        user = await get_user(session, message.from_user.id)
        count, total = await get_user_purchase_stats(session, message.from_user.id)

    text = (
        "👤 Профили шумо\n\n"
        f"👋 Ном: {message.from_user.full_name}\n"
        f"🆔 ID: {message.from_user.id}\n"
        f"📱 Username: @{message.from_user.username or '—'}\n\n"
        "📊 Омори харид:\n"
        f"✅ Харидҳои муваффақ: {count}\n"
        f"💰 Маблағи умумии харид: {total:.2f} сомонӣ\n"
        f"🤝 Баланси реферал: {user.referral_balance:.2f} сомонӣ"
    )
    await message.answer(text, reply_markup=profile_menu_keyboard())


@router.message(F.text == "🤝 Реферал")
async def reply_referral(message: Message, state: FSMContext) -> None:
    await state.clear()
    bot_user = await message.bot.get_me()
    link = f"https://t.me/{bot_user.username}?start=ref_{message.from_user.id}"

    async with get_session() as session:
        user = await get_user(session, message.from_user.id)
        invited = await count_referrals(session, message.from_user.id)

    text = (
        "🤝 Барномаи рефералӣ\n\n"
        f"🔗 Линки даъвати шумо:\n{link}\n\n"
        f"👥 Даъватшудагон: {invited} нафар\n"
        f"💰 Балансӣ рефералӣ: {user.referral_balance:.2f} сомонӣ\n\n"
        "🎁 Барои ҳар дӯсте, ки тавассути линки шумо ба бот ворид шуда, харидро анҷом медиҳад "
        "(ва он аз ҷониби админ тасдиқ мешавад), шумо 5% аз маблағи хариди ӯро ҳамчун бонус мегиред.\n\n"
        "💳 Бонуси ҷамъшуда ба балансии шумо илова мешавад ва метавонед онро барои пардохти "
        "харидҳо дар бот истифода баред."
    )
    await message.answer(text, reply_markup=referral_menu_keyboard())


@router.message(F.text == "⭐ Отзив")
async def reply_review_channel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Шарҳҳои мизоҷони моро дар канал бинед:", reply_markup=review_channel_keyboard())


@router.message(F.text == "🆘 Дастгирӣ")
async def reply_contact(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "📞 Тамос бо мо — тугмаро зер кунед, мустақим кушода мешавад:\n\n"
        "🛡 Бехатар · 🎧 Дастгирии 24/7 · ⏱ Дар 1-5 дақиқа",
        reply_markup=contact_keyboard(),
    )


@router.message(F.text == "❓ Саволҳои маъмул")
async def reply_faq(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(FAQ_TEXT, reply_markup=back_to_menu_keyboard())


@router.message(F.text == "ℹ️ Маълумот")
async def reply_about(message: Message, state: FSMContext) -> None:
    await state.clear()
    async with get_session() as session:
        users_count = await count_total_users(session)
        orders_count = await count_total_delivered_orders(session)

    text = (
        "ℹ️ Дар бораи ALMAZ TJ\n\n"
        "🤖 Боти расмии фурӯши хидматҳои рақамӣ дар Тоҷикистон\n\n"
        "🎮 Хизматҳо: Free Fire (CIS/Бразилия/Индонезия), PUBG Mobile, Standoff 2, Telegram Stars\n"
        "🚀 Афзалиятҳо: суръати баланд (1-5 дақ.), бехатар\n\n"
        f"📊 Корбарон: {users_count} | Фармоишҳои иҷрошуда: {orders_count}\n\n"
        f"📢 Канал: {config.shop_channel_url}"
    )
    await message.answer(text, reply_markup=back_to_menu_keyboard())


async def _format_giveaway_text() -> str:
    async with get_session() as session:
        giveaway = await get_active_giveaway(session)
        last_winner = await get_last_giveaway_winner(session)
        total_winners = await count_total_giveaway_winners(session)
        last_winner_user = await get_user(session, last_winner.user_id) if last_winner else None

    lines = ["🎁 Туҳфаи бот\n"]
    if giveaway is None:
        lines.append("Ҳозир туҳфаи фаъол нест. Мунтазир бошед!")
    else:
        pct = int(giveaway.current_purchases / giveaway.required_purchases * 100) if giveaway.required_purchases else 0
        remaining = max(giveaway.required_purchases - giveaway.current_purchases, 0)
        lines.append(f"🎁 Ҷоиза: {giveaway.prize_description}")
        lines.append(f"👥 Ғолибон: {giveaway.winners_count} нафар")
        lines.append(f"\n📊 {giveaway.current_purchases} аз {giveaway.required_purchases}")
        lines.append(f"📈 {pct}%")
        lines.append(f"⏳ {remaining} харид мондааст")

    if last_winner_user is not None:
        name = (
            f"@{last_winner_user.username}"
            if last_winner_user.username
            else (last_winner_user.full_name or f"ID{last_winner_user.id}")
        )
        lines.append(f"\n🏆 Барандаи охирин: {name}")

    lines.append(f"🎉 То ҳол {total_winners} нафар туҳфа бурдааст.")
    return "\n".join(lines)


@router.message(F.text == "🎁 Туҳфа")
async def reply_giveaway(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(await _format_giveaway_text(), reply_markup=back_to_menu_keyboard())


async def _open_catalog_message(
    message: Message, state: FSMContext, category: ProductCategory, title: str
) -> None:
    async with get_session() as session:
        products = await list_active_products(session, category=category)

    if not products:
        await message.answer(
            "Ҳозир маҳсулот дастрас нест. Лутфан баъдтар кӯшиш кунед ё бо админ тамос гиред.",
            reply_markup=back_to_menu_keyboard(),
        )
        return

    await message.answer(title, reply_markup=products_keyboard(products, category))
    await state.set_state(OrderFlow.choosing_product)


@router.callback_query(F.data == "menu:games")
async def menu_games(callback: CallbackQuery) -> None:
    await callback.message.edit_text("🎮 Бозиро интихоб кунед:", reply_markup=games_menu_keyboard())
    await callback.answer()


async def _open_catalog(callback: CallbackQuery, state: FSMContext, category: ProductCategory, title: str) -> None:
    async with get_session() as session:
        products = await list_active_products(session, category=category)

    if not products:
        await callback.message.edit_text(
            "Ҳозир маҳсулот дастрас нест. Лутфан баъдтар кӯшиш кунед ё бо админ тамос гиред.",
            reply_markup=back_to_menu_keyboard(),
        )
        await callback.answer()
        return

    await callback.message.edit_text(title, reply_markup=products_keyboard(products, category))
    await state.set_state(OrderFlow.choosing_product)
    await callback.answer()


@router.callback_query(F.data == "menu:buy_diamonds")
async def menu_buy_diamonds(callback: CallbackQuery, state: FSMContext) -> None:
    await _open_catalog(callback, state, ProductCategory.DIAMONDS, _CATALOG_TITLES[ProductCategory.DIAMONDS])


@router.callback_query(F.data == "menu:telegram")
async def menu_telegram(callback: CallbackQuery, state: FSMContext) -> None:
    await _open_catalog(callback, state, ProductCategory.TELEGRAM, _CATALOG_TITLES[ProductCategory.TELEGRAM])


@router.callback_query(F.data == "menu:ff_brazil")
async def menu_ff_brazil(callback: CallbackQuery, state: FSMContext) -> None:
    await _open_catalog(callback, state, ProductCategory.FF_BRAZIL, _CATALOG_TITLES[ProductCategory.FF_BRAZIL])


@router.callback_query(F.data == "menu:ff_indonesia")
async def menu_ff_indonesia(callback: CallbackQuery, state: FSMContext) -> None:
    await _open_catalog(callback, state, ProductCategory.FF_INDONESIA, _CATALOG_TITLES[ProductCategory.FF_INDONESIA])


@router.callback_query(F.data == "menu:pubg")
async def menu_pubg(callback: CallbackQuery, state: FSMContext) -> None:
    await _open_catalog(callback, state, ProductCategory.PUBG, _CATALOG_TITLES[ProductCategory.PUBG])


@router.callback_query(F.data == "menu:standoff2")
async def menu_standoff2(callback: CallbackQuery, state: FSMContext) -> None:
    await _open_catalog(callback, state, ProductCategory.STANDOFF2, _CATALOG_TITLES[ProductCategory.STANDOFF2])


@router.callback_query(F.data == "menu:combo")
async def menu_combo(callback: CallbackQuery, state: FSMContext) -> None:
    await _open_catalog(callback, state, ProductCategory.COMBO, _CATALOG_TITLES[ProductCategory.COMBO])


@router.callback_query(F.data.regexp(r"^cartmode:(?!exit:).+$"))
async def enter_cart_mode(callback: CallbackQuery, state: FSMContext) -> None:
    category = ProductCategory(callback.data.split(":", 1)[1])
    async with get_session() as session:
        products = await list_active_products(session, category=category)

    await state.update_data(cart_category=category.value, cart_ids=[])
    await state.set_state(OrderFlow.choosing_cart)
    await callback.message.edit_text(
        "🛒 Бастаҳоеро, ки мехоҳед якҷоя харед, интихоб кунед (якчанд адад мумкин аст):",
        reply_markup=cart_select_keyboard(products, category, set()),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cartmode:exit:"))
async def exit_cart_mode(callback: CallbackQuery, state: FSMContext) -> None:
    category = ProductCategory(callback.data.split(":", 2)[2])
    title = _CATALOG_TITLES.get(category, "Бастаро интихоб кунед:")
    await _open_catalog(callback, state, category, title)


@router.callback_query(OrderFlow.choosing_cart, F.data.startswith("cartitem:"))
async def toggle_cart_item(callback: CallbackQuery, state: FSMContext) -> None:
    product_id = int(callback.data.split(":", 1)[1])
    data = await state.get_data()
    category = ProductCategory(data["cart_category"])
    selected = set(data.get("cart_ids", []))

    if product_id in selected:
        selected.discard(product_id)
    else:
        selected.add(product_id)
    await state.update_data(cart_ids=list(selected))

    async with get_session() as session:
        products = await list_active_products(session, category=category)

    await callback.message.edit_reply_markup(reply_markup=cart_select_keyboard(products, category, selected))
    await callback.answer()


@router.callback_query(OrderFlow.choosing_cart, F.data == "cart:checkout")
async def cart_checkout(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    cart_ids = data.get("cart_ids", [])
    if not cart_ids:
        await callback.answer("Аввал ҳадди ақал як маҳсулот интихоб кунед.", show_alert=True)
        return

    category = ProductCategory(data["cart_category"])
    async with get_session() as session:
        products = [await get_product(session, pid) for pid in cart_ids]
        last_recipient = await get_last_recipient(session, callback.from_user.id, category)

    await state.update_data(cart_product_ids=cart_ids)
    await state.set_state(OrderFlow.entering_player_id)

    total = sum(p.price_somoni for p in products)
    summary = "\n".join(f"• {p.diamonds} {p.unit_label}" for p in products)
    prompt = await _recipient_prompt(category)
    text = f"Шумо интихоб кардед:\n{summary}\n\n💰 Ҳамагӣ: {total:.2f} сомонӣ.\n\nЛутфан {prompt} ирсол кунед:"

    if last_recipient:
        text += f"\n\nШумо пештар бо ин истифода карда будед: {last_recipient}"
        await callback.message.edit_text(text, reply_markup=reuse_recipient_keyboard(last_recipient))
    else:
        await callback.message.edit_text(text)
    await callback.answer()


@router.callback_query(F.data == "menu:contact")
async def menu_contact(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "📞 Тамос бо мо — тугмаро зер кунед, мустақим кушода мешавад:\n\n"
        "🛡 Бехатар · 🎧 Дастгирии 24/7 · ⏱ Дар 1-5 дақиқа",
        reply_markup=contact_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "menu:faq")
async def menu_faq(callback: CallbackQuery) -> None:
    await callback.message.edit_text(FAQ_TEXT, reply_markup=back_to_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data == "menu:about")
async def menu_about(callback: CallbackQuery) -> None:
    async with get_session() as session:
        users_count = await count_total_users(session)
        orders_count = await count_total_delivered_orders(session)

    text = (
        "ℹ️ Дар бораи ALMAZ TJ\n\n"
        "🤖 Боти расмии фурӯши хидматҳои рақамӣ дар Тоҷикистон\n\n"
        "🎮 Хизматҳо: Free Fire (CIS/Бразилия/Индонезия), PUBG Mobile, Standoff 2, Telegram Stars\n"
        "🚀 Афзалиятҳо: суръати баланд (1-5 дақ.), бехатар\n\n"
        f"📊 Корбарон: {users_count} | Фармоишҳои иҷрошуда: {orders_count}\n\n"
        f"📢 Канал: {config.shop_channel_url}"
    )
    await callback.message.edit_text(text, reply_markup=back_to_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data == "menu:profile")
async def menu_profile(callback: CallbackQuery) -> None:
    async with get_session() as session:
        user = await get_user(session, callback.from_user.id)
        count, total = await get_user_purchase_stats(session, callback.from_user.id)

    text = (
        "👤 Профили шумо\n\n"
        f"👋 Ном: {callback.from_user.full_name}\n"
        f"🆔 ID: {callback.from_user.id}\n"
        f"📱 Username: @{callback.from_user.username or '—'}\n\n"
        "📊 Омори харид:\n"
        f"✅ Харидҳои муваффақ: {count}\n"
        f"💰 Маблағи умумии харид: {total:.2f} сомонӣ\n"
        f"🤝 Баланси реферал: {user.referral_balance:.2f} сомонӣ"
    )
    await callback.message.edit_text(text, reply_markup=profile_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data == "menu:referral")
async def menu_referral(callback: CallbackQuery) -> None:
    bot_user = await callback.bot.get_me()
    link = f"https://t.me/{bot_user.username}?start=ref_{callback.from_user.id}"

    async with get_session() as session:
        user = await get_user(session, callback.from_user.id)
        invited = await count_referrals(session, callback.from_user.id)

    text = (
        "🤝 Барномаи рефералӣ\n\n"
        f"🔗 Линки даъвати шумо:\n{link}\n\n"
        f"👥 Даъватшудагон: {invited} нафар\n"
        f"💰 Балансӣ рефералӣ: {user.referral_balance:.2f} сомонӣ\n\n"
        "🎁 Барои ҳар дӯсте, ки тавассути линки шумо ба бот ворид шуда, харидро анҷом медиҳад "
        "(ва он аз ҷониби админ тасдиқ мешавад), шумо 5% аз маблағи хариди ӯро ҳамчун бонус мегиред.\n\n"
        "💳 Бонуси ҷамъшуда ба балансии шумо илова мешавад ва метавонед онро барои пардохти "
        "харидҳо дар бот истифода баред."
    )
    await callback.message.edit_text(text, reply_markup=referral_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data == "menu:top_buyers")
async def menu_top_buyers(callback: CallbackQuery) -> None:
    medals = ["🥇", "🥈", "🥉"]
    async with get_session() as session:
        rows = await top_buyers(session, limit=10)
        rank = await get_buyer_rank(session, callback.from_user.id)

    lines = ["🏆 Топ харидорон\n"]
    for i, (user, count, total) in enumerate(rows):
        icon = medals[i] if i < 3 else f"{i + 1}."
        name = f"@{user.username}" if user.username else (user.full_name or f"ID{user.id}")
        lines.append(f"{icon} {name} — {count} харид • {total:.2f} сомонӣ")

    if rank:
        lines.append(f"\n👤 Шумо: {rank}-ҷой")

    await callback.message.edit_text("\n".join(lines), reply_markup=back_to_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data == "menu:top_referrers")
async def menu_top_referrers(callback: CallbackQuery) -> None:
    medals = ["🥇", "🥈", "🥉"]
    async with get_session() as session:
        rows = await top_referrers(session, limit=10)

    lines = ["🏅 Топ рефералдорон\n"]
    if not rows:
        lines.append("Ҳанӯз ҳеҷ кас дӯст даъват накардааст.")
    for i, (user, count) in enumerate(rows):
        icon = medals[i] if i < 3 else f"{i + 1}."
        name = f"@{user.username}" if user.username else (user.full_name or f"ID{user.id}")
        lines.append(f"{icon} {name} — {count} даъват")

    await callback.message.edit_text("\n".join(lines), reply_markup=back_to_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data == "menu:myorders")
async def menu_myorders(callback: CallbackQuery) -> None:
    text = await _format_orders_text(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=back_to_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data == "menu:balance_history")
async def menu_balance_history(callback: CallbackQuery) -> None:
    async with get_session() as session:
        rows = await list_balance_history(session, callback.from_user.id, limit=20)

    if not rows:
        text = "📜 Таърихи баланс\n\nШумо то ҳол ягон ҳаракати баланс надоред."
    else:
        lines = ["📜 Таърихи баланси реферал (20-и охирин)\n"]
        for tx in rows:
            sign = "➕" if tx.amount >= 0 else "➖"
            when = tx.created_at.strftime("%d.%m.%Y %H:%M")
            lines.append(f"{sign} {abs(tx.amount):.2f} сомонӣ — {tx.reason} ({when})")
        text = "\n".join(lines)

    await callback.message.edit_text(text, reply_markup=back_to_menu_keyboard())
    await callback.answer()


async def _recipient_prompt(category: ProductCategory) -> str:
    return _RECIPIENT_PROMPTS.get(category, "ID-и худро")


@router.callback_query(OrderFlow.choosing_product, F.data.regexp(r"^product:\d+$"))
async def choose_product(callback: CallbackQuery, state: FSMContext) -> None:
    product_id = int(callback.data.split(":", 1)[1])
    async with get_session() as session:
        product = await get_product(session, product_id)
        if product is not None:
            last_recipient = await get_last_recipient(session, callback.from_user.id, product.category)

    if product is None or not product.is_active:
        await callback.answer("Ин маҳсулот дастрас нест.", show_alert=True)
        return

    await state.update_data(product_id=product.id)
    await state.set_state(OrderFlow.entering_player_id)
    prompt = await _recipient_prompt(product.category)
    text = (
        f"Шумо интихоб кардед: {product.diamonds} {product.unit_label} — {product.price_somoni:.2f} сомонӣ.\n\n"
        f"Лутфан {prompt} ирсол кунед:"
    )
    if last_recipient:
        text += f"\n\nШумо пештар бо ин истифода карда будед: {last_recipient}"
        await callback.message.edit_text(text, reply_markup=reuse_recipient_keyboard(last_recipient))
    else:
        await callback.message.edit_text(text)
    await callback.answer()


async def _finalize_recipient(state: FSMContext, user_id: int, recipient: str, answer_target) -> None:
    """Shared by both the free-text ID/username entry and the "use my
    previous one" quick button — builds the confirmation screen. Handles
    both a single product (data["product_id"]) and a multi-pack cart
    checkout (data["cart_product_ids"]), and PUBG Mobile's extra Server ID
    field (data["recipient_extra"], collected by enter_recipient_extra)."""
    data = await state.get_data()
    cart_ids = data.get("cart_product_ids")
    recipient_extra = data.get("recipient_extra")

    async with get_session() as session:
        user = await get_user(session, user_id)
        if cart_ids:
            products = [await get_product(session, pid) for pid in cart_ids]
        else:
            products = [await get_product(session, data["product_id"])]

    await state.update_data(ff_player_id=recipient)
    await state.set_state(OrderFlow.confirming)

    total_price = sum(p.price_somoni for p in products)
    offer_balance = user is not None and user.referral_balance >= total_price > 0
    category = products[0].category

    player_name = None
    if category in (ProductCategory.DIAMONDS, ProductCategory.FF_BRAZIL, ProductCategory.FF_INDONESIA):
        mapped = next((p for p in products if p.fzr_category_id), None)
        if mapped:
            player_name = await _try_validate_player_id(mapped.fzr_category_id, recipient)

    recipient_label = "📱 Username" if category == ProductCategory.TELEGRAM else "🆔 ID"
    confirm_lines = ["🛒 <b>Тасдиқи фармоиш</b>\n", f"{recipient_label}: {recipient}"]
    if recipient_extra:
        confirm_lines.append(f"🖥 Server ID: {recipient_extra}")
    if player_name:
        confirm_lines.append(f"👤 Ном: <b>{html.escape(player_name)}</b>")
    confirm_lines.append("")

    for p in products:
        bonus = f" (+{p.bonus_diamonds})" if p.bonus_diamonds else ""
        confirm_lines.append(f"🎁 Маҳсулот: {p.diamonds}{bonus} {p.unit_label}")
    if len(products) > 1:
        confirm_lines.append(f"💰 Ҳамагӣ: <b>{total_price:.2f} сомонӣ</b>")
    else:
        confirm_lines.append(f"💰 Нарх: <b>{products[0].price_somoni:.2f} сомонӣ</b>")

    confirm_lines.append("\nҲама дуруст аст?")
    await answer_target.answer(
        "\n".join(confirm_lines),
        reply_markup=confirm_order_keyboard(offer_balance_payment=offer_balance),
    )


async def _after_recipient_id(
    state: FSMContext, user_id: int, category: ProductCategory, recipient: str, answer_target
) -> None:
    """PUBG Mobile needs a Server ID on top of the Player ID — route there
    first; every other category goes straight to the confirmation screen."""
    if category == ProductCategory.PUBG:
        await state.update_data(ff_player_id=recipient)
        await state.set_state(OrderFlow.entering_recipient_extra)
        await answer_target.answer(
            f"✅ Player ID: {recipient}\n\n"
            "Акнун Server ID-и худро ирсол кунед (рақами сервери PUBG Mobile):"
        )
        return
    await _finalize_recipient(state, user_id, recipient, answer_target)


@router.message(OrderFlow.entering_player_id, F.text)
async def enter_player_id(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    cart_ids = data.get("cart_product_ids")
    async with get_session() as session:
        product = await get_product(session, cart_ids[0] if cart_ids else data["product_id"])

    recipient = message.text.strip()

    if product.category == ProductCategory.TELEGRAM:
        recipient = recipient.removeprefix("@")
        if not (5 <= len(recipient) <= 32) or not recipient.replace("_", "").isalnum():
            await message.answer("Username-и нодуруст. Лутфан username-и дурусти Telegram-ро (бе @) нависед.")
            return
    else:
        if not recipient.isdigit() or not (4 <= len(recipient) <= 20):
            await message.answer("ID-и нодуруст. Лутфан танҳо рақами дурусти ID-ро ворид кунед.")
            return
        if product.category == ProductCategory.COMBO:
            async with get_session() as session:
                already = await has_combo_purchase(session, recipient)
            if already:
                await message.answer(
                    "❌ Ин ID (аккаунт) аллакай як бор Комборо гирифтааст. "
                    "Ҳар аккаунт танҳо як бор метавонад Комбо харад. "
                    "Лутфан ID-и дигар ворид кунед ё «❌ Бекор»-ро зер кунед."
                )
                return

    await _after_recipient_id(state, message.from_user.id, product.category, recipient, message)


@router.message(OrderFlow.entering_recipient_extra, F.text)
async def enter_recipient_extra(message: Message, state: FSMContext) -> None:
    server_id = message.text.strip()
    if not server_id.isdigit() or not (1 <= len(server_id) <= 10):
        await message.answer("Server ID-и нодуруст. Лутфан танҳо рақами Server ID-и PUBG Mobile-ро ворид кунед.")
        return

    await state.update_data(recipient_extra=server_id)
    data = await state.get_data()
    await _finalize_recipient(state, message.from_user.id, data["ff_player_id"], message)


@router.callback_query(OrderFlow.entering_player_id, F.data.startswith("reuseid:"))
async def reuse_saved_id(callback: CallbackQuery, state: FSMContext) -> None:
    recipient = callback.data.split(":", 1)[1]
    data = await state.get_data()
    cart_ids = data.get("cart_product_ids")
    async with get_session() as session:
        product = await get_product(session, cart_ids[0] if cart_ids else data["product_id"])

    if product.category == ProductCategory.COMBO:
        async with get_session() as session:
            already = await has_combo_purchase(session, recipient)
        if already:
            await callback.answer("Ин ID аллакай Комборо гирифтааст.", show_alert=True)
            return

    await _after_recipient_id(state, callback.from_user.id, product.category, recipient, callback.message)
    await callback.answer()


async def _try_validate_player_id(fzr_category_id: str, player_id: str) -> str | None:
    """Best-effort player-ID check via FazerCards. Returns the confirmed
    in-game name, or None if unsupported/unavailable/couldn't confirm —
    callers must treat None as "couldn't verify", never as "definitely
    wrong", since this must not block a purchase on its own.

    /api/v2/topups/validate-id uses its own category namespace, separate
    from the /api/v2/topups (offers/order) one — it lists one entry per
    *game* ("free_fire"), not per regional top-up SKU ("free_fire_cis",
    "free_fire_bd", ...). Player-ID validation is the same Garena lookup
    regardless of which regional SKU is used to actually deliver the
    diamonds, so match by game family (prefix) and call validate-id with
    *its own* category_id — never the product's fzr_category_id, which
    that endpoint doesn't recognise (confirmed: it 404s as "unknown
    category_id" there)."""
    from bot.services.delivery import guess_id_field_key
    from bot.services.fazercards import FazerCardsError, list_validate_id_categories, validate_player_id

    try:
        supported = await list_validate_id_categories()
    except FazerCardsError:
        return None

    item = next(
        (
            i
            for i in supported.get("items", [])
            if i.get("category_id")
            and (
                fzr_category_id == i["category_id"]
                or fzr_category_id.startswith(i["category_id"] + "_")
            )
        ),
        None,
    )
    if item is None:
        return None

    field_key = guess_id_field_key(item.get("fields", []))
    if field_key is None:
        return None

    try:
        result = await validate_player_id(item["category_id"], {field_key: player_id})
    except FazerCardsError:
        return None

    return result.get("player_name") if result.get("valid") else None


@router.callback_query(StateFilter(OrderFlow.confirming, OrderFlow.choosing_payment_method), F.data == "order:cancel")
async def cancel_order(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Фармоиш бекор карда шуд.")
    await callback.answer()


async def _cart_products(session, data: dict) -> list[Product]:
    cart_ids = data.get("cart_product_ids")
    if cart_ids:
        return [await get_product(session, pid) for pid in cart_ids]
    return [await get_product(session, data["product_id"])]


@router.callback_query(OrderFlow.confirming, F.data == "order:pay_balance")
async def pay_with_balance(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    recipient = data["ff_player_id"]
    recipient_extra = data.get("recipient_extra")

    async with get_session() as session:
        products = await _cart_products(session, data)

        if any(p is None or not p.is_active or p.price_somoni <= 0 for p in products):
            await callback.answer("Ин маҳсулот дигар дастрас нест.", show_alert=True)
            await state.clear()
            try:
                await callback.message.edit_text(
                    "❌ Маҳсулот дигар дастрас нест (нарх тағйир ёфт ё хомӯш шуд). "
                    "Лутфан аз менюи асосӣ аз нав интихоб кунед.",
                    reply_markup=back_to_menu_keyboard(),
                )
            except TelegramBadRequest:
                pass
            return

        total = sum(p.price_somoni for p in products)
        user = await get_user(session, callback.from_user.id)

        if user is None or user.referral_balance < total:
            await callback.answer("Баланси реферал кофӣ нест.", show_alert=True)
            return

        if any(p.category == ProductCategory.COMBO for p in products):
            if await has_combo_purchase(session, recipient):
                await callback.answer("Ин ID аллакай Комборо гирифтааст.", show_alert=True)
                return

        group_id = uuid.uuid4().hex if len(products) > 1 else None
        orders = [
            await create_order(
                session,
                user_id=callback.from_user.id,
                product=p,
                ff_player_id=recipient,
                payment_provider="referral_balance",
                paid_with_referral_balance=True,
                cart_group_id=group_id,
                recipient_extra=recipient_extra,
            )
            for p in products
        ]
        primary = orders[0]
        await deduct_referral_balance(
            session, user, total, reason=f"Пардохти фармоиши #{primary.id} бо баланси реферал"
        )

    summary = "\n".join(
        f"📦 {p.diamonds} {p.unit_label} ({category_display_name(p.category)}) — {p.price_somoni:.2f} сомонӣ"
        for p in products
    )
    if config.admin_chat_id:
        await callback.bot.send_message(
            config.admin_chat_id,
            f"🆕 Фармоиши #{primary.id} (пардохт аз баланси реферал — тасдиқшуда)\n"
            f"👤 Мизоҷ: {callback.from_user.full_name} (@{callback.from_user.username or '—'}, id={callback.from_user.id})\n"
            f"{summary}\n"
            f"🎮 {format_recipient(recipient, recipient_extra)}\n\n"
            f"Лутфан иҷро карда, 'Delivered'-ро зер кунед.",
            reply_markup=admin_order_keyboard(primary),
        )

    await state.clear()
    await callback.message.edit_text(
        f"✅ Фармоиши #{primary.id} бо баланси реферал пардохт шуд!\n"
        f"{summary}\n\nДар 1-5 дақиқа ба шумо мерасад."
    )
    await callback.answer()


async def _create_orders_and_invoice(callback: CallbackQuery, state: FSMContext, provider) -> None:
    """Shared by every manual payment method (💳 ДС / 💳 Алиф / 💳 Эсхата /
    💳 Амонатбонк) — see
    confirm_order, choose_payment_card, choose_payment_alif."""
    data = await state.get_data()
    recipient = data["ff_player_id"]
    recipient_extra = data.get("recipient_extra")

    async with get_session() as session:
        products = await _cart_products(session, data)

        if any(p is None or not p.is_active or p.price_somoni <= 0 for p in products):
            # Selected between confirm and payment-method choice, but the
            # admin deactivated/repriced it in the meantime (e.g. a
            # placeholder-price product from /addpubg still awaiting a
            # real /setprice) — never build a payment link with a
            # missing/zero FINAL PRICE.
            await callback.answer("Ин маҳсулот дигар дастрас нест.", show_alert=True)
            await state.clear()
            try:
                await callback.message.edit_text(
                    "❌ Маҳсулот дигар дастрас нест (нарх тағйир ёфт ё хомӯш шуд). "
                    "Лутфан аз менюи асосӣ аз нав интихоб кунед.",
                    reply_markup=back_to_menu_keyboard(),
                )
            except TelegramBadRequest:
                pass
            return

        if any(p.category == ProductCategory.COMBO for p in products):
            if await has_combo_purchase(session, recipient):
                await callback.answer("Ин ID аллакай Комборо гирифтааст.", show_alert=True)
                return

        group_id = uuid.uuid4().hex if len(products) > 1 else None
        orders = [
            await create_order(
                session,
                user_id=callback.from_user.id,
                product=p,
                ff_player_id=recipient,
                payment_provider=provider.method_key,
                cart_group_id=group_id,
                recipient_extra=recipient_extra,
            )
            for p in products
        ]
        primary = orders[0]
        total = sum(o.amount_somoni for o in orders)
        if len(orders) > 1:
            # One payment covers the whole cart — the total lives on the
            # primary order so SMS/manual confirmation matches on it;
            # siblings carry 0 so stats aren't double-counted.
            primary.amount_somoni = total
            for o in orders[1:]:
                o.amount_somoni = 0.0
            await session.commit()
            await session.refresh(primary)

    invoice = await provider.create_invoice(primary.id, total)

    await state.update_data(order_id=primary.id)
    await state.set_state(OrderFlow.awaiting_payment_proof)
    text = f"Фармоиши #{primary.id} сабт шуд.\n\n{invoice.instructions}"
    keyboard = payment_link_keyboard(invoice.pay_url) if invoice.pay_url else None

    if invoice.card_photo_file_id:
        # A photo can't be attached by editing an existing text-only
        # message — clear its old confirm/cancel keyboard (so it doesn't
        # linger as a dead button) and send the card photo as a new
        # message instead.
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        await callback.message.answer_photo(invoice.card_photo_file_id, caption=text, reply_markup=keyboard)
    else:
        await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(OrderFlow.confirming, F.data == "order:confirm")
async def confirm_order(callback: CallbackQuery, state: FSMContext) -> None:
    if config.payment_provider == "manual":
        # Let the customer pick between the default card and "💳 Алиф" —
        # both lead to the exact same admin-confirmed receipt flow, see
        # bot/services/payments.py. See choose_payment_card /
        # choose_payment_alif below.
        await state.set_state(OrderFlow.choosing_payment_method)
        await callback.message.edit_text(
            "💳 Усули пардохтро интихоб кунед:", reply_markup=payment_method_keyboard()
        )
        await callback.answer()
        return

    # Real-gateway mode (PAYMENT_PROVIDER=alif/dc — requires a signed
    # merchant agreement + real credentials, see bot/services/payments.py).
    provider = get_payment_provider()
    await _create_orders_and_invoice(callback, state, provider)


@router.callback_query(OrderFlow.choosing_payment_method, F.data == "paymethod:card")
async def choose_payment_card(callback: CallbackQuery, state: FSMContext) -> None:
    await _create_orders_and_invoice(callback, state, ManualBankTransferProvider())


@router.callback_query(OrderFlow.choosing_payment_method, F.data == "paymethod:alif")
async def choose_payment_alif(callback: CallbackQuery, state: FSMContext) -> None:
    await _create_orders_and_invoice(callback, state, AlifManualProvider())


@router.callback_query(OrderFlow.choosing_payment_method, F.data == "paymethod:eskhata")
async def choose_payment_eskhata(callback: CallbackQuery, state: FSMContext) -> None:
    await _create_orders_and_invoice(callback, state, EskhataManualProvider())


@router.callback_query(OrderFlow.choosing_payment_method, F.data == "paymethod:amonatbonk")
async def choose_payment_amonatbonk(callback: CallbackQuery, state: FSMContext) -> None:
    await _create_orders_and_invoice(callback, state, AmonatbonkManualProvider())


@router.message(OrderFlow.awaiting_payment_proof)
async def receive_payment_proof(message: Message, state: FSMContext) -> None:
    import hashlib

    from bot.db.repo import (
        find_duplicate_proof,
        get_order,
        get_orders_by_group,
        set_payment_proof_hash,
        set_proof_submitted,
    )

    data = await state.get_data()
    order_id = data.get("order_id")
    if not order_id:
        return

    if not message.photo and not message.document:
        # A stray text message here (a question, "Номер?", etc.) is not a
        # receipt. Forwarding it to the admin as if it were one used to
        # silently consume this order's awaiting-proof state and clear it —
        # so the customer's *real* screenshot, sent right after, no longer
        # matched this handler and never reached the admin at all. Keep the
        # state open and ask again instead of eating it on a non-photo reply.
        await message.answer("Лутфан расми (скриншот)-и расиди пардохтро равон кунед — на матн.")
        return

    async with get_session() as session:
        order = await get_order(session, order_id)
        group = await get_orders_by_group(session, order.cart_group_id) if order.cart_group_id else [order]
        products = [await get_product(session, o.product_id) for o in group]
        # Always shown now (not just for multi-item carts) — the admin
        # needs to see which product/category/amount this receipt is for
        # regardless of whether it's a single order or a group.
        items_summary = "\n" + "\n".join(
            f"📦 {p.diamonds} {p.unit_label} ({category_display_name(p.category)})"
            + (f" — {o.amount_somoni:.2f} сомонӣ" if o.amount_somoni else "")
            for o, p in zip(group, products)
        )
        recipient_line = f"🎮 {format_recipient(order.ff_player_id, order.recipient_extra)}\n"
        total = sum(o.amount_somoni for o in group)
        method_line = f"💳 Усул: {payment_method_label(order.payment_provider)}\n"

    caption = (
        f"🆕 Фармоиши #{order_id}{items_summary}\n"
        f"💰 Ҳамагӣ: {total:.2f} сомонӣ\n"
        f"👤 Мизоҷ: {message.from_user.full_name} (@{message.from_user.username or '—'}, id={message.from_user.id})\n"
        f"{recipient_line}"
        f"{method_line}"
        f"Расиди пардохт замима шуд.\n\n"
        f"❗️ Пеш аз тасдиқ, ҳатман дар аппи бонки худ маблағи воқеиро санҷед — расм танҳо кофӣ нест."
    )

    async with get_session() as session:
        order = await get_order(session, order_id)

        if message.photo:
            file_bytes = await message.bot.download(message.photo[-1].file_id)
            proof_hash = hashlib.sha256(file_bytes.read()).hexdigest()
            duplicate = await find_duplicate_proof(session, proof_hash, order_id)
            order = await set_payment_proof_hash(session, order, proof_hash)
            if duplicate is not None:
                caption = (
                    f"⚠️⚠️ ДИҚҚАТ: ҳамин расм қаблан барои фармоиши #{duplicate.id} "
                    f"истифода шуда буд! Эҳтимоли фиреб — бодиққат санҷед.\n\n{caption}"
                )
        order = await set_proof_submitted(session, order)

    if config.admin_chat_id:
        if message.photo:
            await message.bot.send_photo(
                config.admin_chat_id,
                photo=message.photo[-1].file_id,
                caption=caption,
                reply_markup=admin_order_keyboard(order),
            )
        else:
            await message.bot.send_document(
                config.admin_chat_id,
                document=message.document.file_id,
                caption=caption,
                reply_markup=admin_order_keyboard(order),
            )

    await message.answer(
        "Ташаккур! Расиди шумо ба админ фиристода шуд. Пас аз тасдиқ маҳсулоти шумо ирсол мешавад."
    )
    await state.clear()


@router.message(OrderFlow.awaiting_review, F.text)
async def receive_review(message: Message, state: FSMContext) -> None:
    from bot.db.repo import get_order
    from bot.services.announcements import post_review_announcement

    data = await state.get_data()
    order_id = data.get("order_id")
    await state.clear()
    if not order_id:
        return

    async with get_session() as session:
        order = await get_order(session, order_id)
        product = await get_product(session, order.product_id)
        await post_review_announcement(message.bot, session, order, product, message.text.strip())

    await message.answer("Ташаккур барои шарҳи шумо! 🙏")


@router.callback_query(F.data.startswith("review:skip:"))
async def skip_review(callback: CallbackQuery, state: FSMContext) -> None:
    from bot.db.repo import get_order
    from bot.services.announcements import post_review_announcement

    order_id = int(callback.data.split(":")[2])
    await state.clear()

    async with get_session() as session:
        order = await get_order(session, order_id)
        product = await get_product(session, order.product_id)
        await post_review_announcement(callback.bot, session, order, product, None)

    await callback.message.edit_text("Хуб, ташаккур барои харид! 🙏")
    await callback.answer()


@router.message(Command("myorders"))
async def my_orders(message: Message) -> None:
    text = await _format_orders_text(message.from_user.id)
    await message.answer(text)


@router.message(F.photo | F.document)
async def stray_receipt_fallback(message: Message) -> None:
    """Reached only when no state-specific handler above claimed a
    photo/document — i.e. the customer sent something receipt-shaped with
    no active order actually waiting for proof (receive_payment_proof
    above already handles the *matching* case: registered on
    OrderFlow.awaiting_payment_proof, so it always wins first when that
    state is active). Silently doing nothing here used to leave the
    customer thinking their receipt was sent when it never reached anyone."""
    await message.answer(
        "Ин расм/файл ба ягон фармоиши фаъол алоқаманд нест — расид қабул нашуд.\n\n"
        "Агар мехоҳед харид кунед, аз меню сар кунед 👇",
        reply_markup=main_reply_keyboard(),
    )


@router.callback_query()
async def stale_callback_fallback(callback: CallbackQuery, state: FSMContext) -> None:
    """Registered last on purpose: only reached when no handler above
    matched. That happens when a button belongs to a screen the user has
    since navigated away from (its FSM-state filter no longer matches) —
    without this, Telegram leaves that tap's loading spinner stuck forever
    since nothing ever calls callback.answer() on it. Answer it and send a
    fresh main menu instead of leaving the user stuck."""
    await callback.answer("Ин тугма кӯҳна шудааст, лутфан аз нав кушоед 👇", show_alert=True)
    await state.clear()
    await callback.message.answer(WELCOME_TEXT)
