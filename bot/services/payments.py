"""Payment provider abstraction.

Only the manual family is wired to a real, working flow: the customer
transfers by card (or Alif Mobi / Eskhata / Amonatbonk interbank transfer)
and sends proof; an admin confirms it with one tap. That is enough to
launch and take real orders immediately. `ManualBankTransferProvider`
(💳 ДС — Dushanbe City Bank card), `AlifManualProvider` (💳 Алиф),
`EskhataManualProvider` (💳 Эсхата) and `AmonatbonkManualProvider`
(💳 Амонатбонк) are all the exact same admin-confirmed flow, just pointed
at a different receiving number/label — the customer picks one at
checkout when PAYMENT_PROVIDER=manual (see
bot/handlers/customer.py:confirm_order). Alif, Eskhata and Amonatbonk all
transfer into the *same* underlying account, via the customer's own
Alif/Eskhata/Amonatbonk mobile-banking app — one shared number
(config.mobile_transfer_number) covers all three; only ДС uses a
different number (a card, config.receiving_card_number).

`AlifPayProvider` and `DCBankProvider` below are scaffolds, not working
integrations — they are the *real* payment-gateway APIs (Alif Business /
Dushanbe City Bank), not the manual "💳 Алиф" button. Neither Alif nor DC
Bank publishes a public generic REST spec — the real Shop ID, Secret Key,
endpoint URLs and signature scheme are handed to you directly after you
sign a merchant agreement. Fill in `create_invoice` and `verify_callback`
from that document before switching PAYMENT_PROVIDER=alif/dc in
production. Shipping either half-finished would silently take customers'
money without confirming it actually arrived.
"""

from __future__ import annotations

import hashlib
import hmac
from abc import ABC, abstractmethod
from dataclasses import dataclass

from bot.config import config


@dataclass
class InvoiceResult:
    provider_reference: str
    pay_url: str | None  # link to send the customer to, if any
    instructions: str  # what to show the customer in the bot
    card_photo_file_id: str | None = None  # photo of the receiving card, if admin set one


class PaymentProvider(ABC):
    # Stored on Order.payment_provider and shown back to the admin (see
    # bot/texts.py:payment_method_label) — every concrete provider must
    # set its own.
    method_key: str = "unknown"
    method_label: str = "Пардохт"

    @abstractmethod
    async def create_invoice(self, order_id: int, amount_somoni: float) -> InvoiceResult:
        """Start a payment for an order and return how the customer should pay."""

    @abstractmethod
    def verify_callback(self, payload: dict, headers: dict) -> tuple[bool, str | None]:
        """Validate an inbound payment-gateway webhook.

        Returns (is_valid, provider_reference_if_paid).
        """


def _build_expresspay_link(order_id: int, amount_somoni: float) -> str | None:
    """Pay-by-link with the recipient card and exact amount pre-filled, so
    the customer just taps "Пардохт" and confirms — reverse-engineered from
    a real link a similar shop's bot sends
    (?A=<card>&s=<amount>&c=<label>&f1=<code>). f1 turned out to be
    required (the page errors "one of the parameters is empty" without
    it) — see config.expresspay_f1. Tied to the shop's own ExpressPay
    merchant card, so this only ever applies to the default card method,
    never to the Alif Mobi manual transfer (see
    AlifManualProvider._pay_link)."""
    if not config.receiving_card_number:
        return None
    return (
        f"{config.expresspay_base_url}?A={config.receiving_card_number}"
        f"&s={amount_somoni:.2f}&c=order_{order_id}&f1={config.expresspay_f1}"
    )


class ManualBankTransferProvider(PaymentProvider):
    """The "💳 ДС" button — customer transfers by card into the shop's
    Dushanbe City Bank card (config.receiving_card_number) and sends
    proof; admin taps Confirm in the bot. Works today with zero external
    accounts."""

    method_key = "manual"
    method_label = "💳 ДС"

    def _card_number(self) -> str:
        return config.receiving_card_number

    def _pay_link(self, order_id: int, amount_somoni: float) -> str | None:
        return _build_expresspay_link(order_id, amount_somoni)

    async def _get_card_photo_file_id(self, session) -> str | None:
        from bot.db.repo import get_card_photo_file_id

        return await get_card_photo_file_id(session)

    def _extra_note(self) -> str:
        """Optional line shown right under the card/number — used by
        Eskhata/Amonatbonk to spell out that this is an interbank transfer
        (from a *different* bank's own app) into the same underlying
        account, not a special new account."""
        return ""

    async def create_invoice(self, order_id: int, amount_somoni: float) -> InvoiceResult:
        from bot.db.session import get_session

        card_number = self._card_number()
        pay_url = self._pay_link(order_id, amount_somoni)

        if not card_number:
            card_line = "⚠️ Рақами корти қабулкунанда танзим нашудааст — бо админ тамос гиред.\n"
        else:
            card_line = f"{self.method_label}: {card_number}\n{self._extra_note()}"

        if pay_url:
            instructions = (
                f"{card_line}"
                f"💰 Маблағи дақиқ: {amount_somoni:.2f} сомонӣ (на кам, на зиёд)\n\n"
                f"Тугмаи «💳 Пардохт»-ро пахш кунед, маблағро тасдиқ кунед, "
                f"баъд расиди пардохтро (скриншот) ба ин ҷо фиристед."
            )
        else:
            instructions = (
                f"{card_line}"
                f"Лутфан {amount_somoni:.2f} сомонӣ гузаронед ва расиди пардохтро "
                f"(скриншот) ба ин ҷо фиристед. Пас аз тасдиқи админ фармоишатон иҷро мешавад."
            )

        async with get_session() as session:
            card_photo_file_id = await self._get_card_photo_file_id(session)

        return InvoiceResult(
            provider_reference=f"{self.method_key}-{order_id}",
            pay_url=pay_url,
            instructions=instructions,
            card_photo_file_id=card_photo_file_id,
        )

    def verify_callback(self, payload: dict, headers: dict) -> tuple[bool, str | None]:
        # Manual provider has no webhook; confirmation happens via admin button.
        return False, None


class AlifManualProvider(ManualBankTransferProvider):
    """The "💳 Алиф" button — a manual Alif Mobi card-to-card transfer,
    with the exact same admin-confirmed proof flow as
    ManualBankTransferProvider above (identical create_invoice logic,
    inherited unchanged). Not a real Alif Business API integration — see
    AlifPayProvider below for that placeholder. Card photo is a separate
    setting (/setalifcardphoto) so it never shows the wrong card."""

    method_key = "manual_alif"
    method_label = "💳 Алиф"

    def _card_number(self) -> str:
        return config.mobile_transfer_number or config.receiving_card_number

    def _pay_link(self, order_id: int, amount_somoni: float) -> str | None:
        # ExpressPay pay-by-link is tied to the shop's main receiving card
        # specifically — an Alif Mobi transfer is entered by hand in the
        # Alif app, so no pre-filled link applies here.
        return None

    async def _get_card_photo_file_id(self, session) -> str | None:
        from bot.db.repo import get_alif_card_photo_file_id

        return await get_alif_card_photo_file_id(session)


class EskhataManualProvider(ManualBankTransferProvider):
    """The "💳 Эсхата" button — customer sends from their own Eskhata Bank
    mobile-banking app into the shop's account (config.mobile_transfer_number
    — the same number Alif/Amonatbonk use). Same admin-confirmed proof
    flow as every other manual method above; only the label, number
    source and card photo differ."""

    method_key = "manual_eskhata"
    method_label = "💳 Эсхата"

    def _card_number(self) -> str:
        return config.mobile_transfer_number or config.receiving_card_number

    def _pay_link(self, order_id: int, amount_somoni: float) -> str | None:
        return None

    def _extra_note(self) -> str:
        return "ℹ️ Аз барномаи мобилии Эсхата (Eskhata) ба ин рақам гузаронед.\n"

    async def _get_card_photo_file_id(self, session) -> str | None:
        from bot.db.repo import get_eskhata_card_photo_file_id

        return await get_eskhata_card_photo_file_id(session)


class AmonatbonkManualProvider(ManualBankTransferProvider):
    """The "💳 Амонатбонк" button — same idea as EskhataManualProvider,
    just from an Амонатбонк (Amonatbank) mobile-banking app instead."""

    method_key = "manual_amonatbonk"
    method_label = "💳 Амонатбонк"

    def _card_number(self) -> str:
        return config.mobile_transfer_number or config.receiving_card_number

    def _pay_link(self, order_id: int, amount_somoni: float) -> str | None:
        return None

    def _extra_note(self) -> str:
        return "ℹ️ Аз барномаи мобилии Амонатбонк ба ин рақам гузаронед.\n"

    async def _get_card_photo_file_id(self, session) -> str | None:
        from bot.db.repo import get_amonatbonk_card_photo_file_id

        return await get_amonatbonk_card_photo_file_id(session)


class AlifPayProvider(PaymentProvider):
    """Scaffold only — see module docstring. Do not enable without real
    credentials and the real callback signature scheme from Alif Business.
    This is the *real* payment-gateway integration (PAYMENT_PROVIDER=alif)
    — not the manual "💳 Алиф" button above (AlifManualProvider)."""

    method_key = "alif"
    method_label = "Alif Pay"

    def __init__(self) -> None:
        if not (config.alif_shop_id and config.alif_secret_key and config.alif_api_base_url):
            raise RuntimeError(
                "PAYMENT_PROVIDER=alif is set but ALIF_SHOP_ID / ALIF_SECRET_KEY / "
                "ALIF_API_BASE_URL are empty. Get these from your Alif Business merchant "
                "agreement first — see bot/services/payments.py docstring."
            )
        self.shop_id = config.alif_shop_id
        self.secret_key = config.alif_secret_key
        self.api_base_url = config.alif_api_base_url

    async def create_invoice(self, order_id: int, amount_somoni: float) -> InvoiceResult:
        raise NotImplementedError(
            "Wire this up to Alif's real 'create invoice' endpoint once you have "
            "their merchant API doc. Placeholder to prevent silently taking payments "
            "without a real gateway behind it."
        )

    def verify_callback(self, payload: dict, headers: dict) -> tuple[bool, str | None]:
        # Placeholder HMAC check — replace with Alif's actual signature scheme.
        signature = headers.get("X-Signature", "")
        expected = hmac.new(
            self.secret_key.encode(), str(payload).encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return False, None
        if payload.get("status") == "paid":
            return True, str(payload.get("transaction_id"))
        return False, None


class DCBankProvider(PaymentProvider):
    """Scaffold only, same reasoning as AlifPayProvider. Dushanbe City Bank
    (dc.tj) does run merchant/internet-acquiring services ("Express Pay"),
    but — like Alif — doesn't publish a public generic API spec. Contact
    them directly as a registered business to get a Shop ID, Secret Key,
    and their real endpoint/callback signature documentation, then fill in
    `create_invoice` and `verify_callback` below before switching
    PAYMENT_PROVIDER=dc in production."""

    method_key = "dc"
    method_label = "Dushanbe City Bank"

    def __init__(self) -> None:
        if not (config.dc_shop_id and config.dc_secret_key and config.dc_api_base_url):
            raise RuntimeError(
                "PAYMENT_PROVIDER=dc is set but DC_SHOP_ID / DC_SECRET_KEY / "
                "DC_API_BASE_URL are empty. Get these from Dushanbe City Bank as a "
                "registered merchant first — see bot/services/payments.py docstring."
            )
        self.shop_id = config.dc_shop_id
        self.secret_key = config.dc_secret_key
        self.api_base_url = config.dc_api_base_url

    async def create_invoice(self, order_id: int, amount_somoni: float) -> InvoiceResult:
        raise NotImplementedError(
            "Wire this up to DC Bank's real 'create invoice' endpoint once you have "
            "their merchant API doc. Placeholder to prevent silently taking payments "
            "without a real gateway behind it."
        )

    def verify_callback(self, payload: dict, headers: dict) -> tuple[bool, str | None]:
        # Placeholder HMAC check — replace with DC Bank's actual signature scheme.
        signature = headers.get("X-Signature", "")
        expected = hmac.new(
            self.secret_key.encode(), str(payload).encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return False, None
        if payload.get("status") == "paid":
            return True, str(payload.get("transaction_id"))
        return False, None


def get_payment_provider() -> PaymentProvider:
    """Used only for the real-gateway path (PAYMENT_PROVIDER=alif/dc).
    When PAYMENT_PROVIDER=manual (the default), the customer instead picks
    between ДС/Алиф/Эсхата/Амонатбонк directly at checkout — see
    bot/handlers/customer.py:confirm_order."""
    if config.payment_provider == "alif":
        return AlifPayProvider()
    if config.payment_provider == "dc":
        return DCBankProvider()
    return ManualBankTransferProvider()
