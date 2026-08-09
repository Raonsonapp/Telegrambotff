from aiogram.fsm.state import State, StatesGroup


class OrderFlow(StatesGroup):
    choosing_product = State()
    choosing_cart = State()
    entering_player_id = State()
    # Only entered for categories that need a second recipient field
    # (currently just PUBG Mobile's Server ID) — see
    # bot/handlers/customer.py:_after_recipient_id.
    entering_recipient_extra = State()
    confirming = State()
    # Only entered when PAYMENT_PROVIDER=manual — lets the customer pick
    # between the default card and "💳 Алиф" before an invoice is created.
    # See bot/handlers/customer.py:confirm_order.
    choosing_payment_method = State()
    awaiting_payment_proof = State()
    awaiting_review = State()
