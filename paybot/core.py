"""Pure payment rules: catalog, invoice checks, payment confirmation. No Telegram here.

Telegram Payments flow:
  1. bot sends an invoice (sendInvoice) for an order it created;
  2. before charging, Telegram asks the bot "is this still OK?" (pre_checkout_query) and
     waits up to 10 seconds for the answer;
  3. after the charge the bot receives `successful_payment`.
Every check below exists because one of these steps can arrive late, twice or forged.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

INVOICE_TTL = timedelta(minutes=30)
PAYLOAD_RE = re.compile(r"order:(\d+):([0-9a-f]{16})")


class PaymentError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Product:
    id: str
    title: str
    description: str
    price: int          # kopecks
    stock: int | None   # None = unlimited (services, digital goods)


@dataclass(frozen=True)
class Order:
    id: int | None
    user_id: int
    product_id: str
    amount: int
    currency: str
    status: str          # pending -> paid | expired
    created_at: datetime
    nonce: str
    charge_id: str = ""  # telegram_payment_charge_id
    provider_charge_id: str = ""
    paid_at: datetime | None = None

    @property
    def payload(self) -> str:
        return f"order:{self.id}:{self.nonce}"


def is_test_token(provider_token: str) -> bool:
    """BotFather test tokens look like '123:TEST:abc'. Live ones have ':LIVE:'."""
    return ":TEST:" in provider_token


def new_order(user_id: int, product: Product, currency: str, now: datetime) -> Order:
    if product.stock is not None and product.stock <= 0:
        raise PaymentError("out_of_stock", "Товар закончился")
    return Order(None, user_id, product.id, product.price, currency, "pending", now, secrets.token_hex(8))


def parse_payload(payload: str) -> tuple[int, str]:
    match = PAYLOAD_RE.fullmatch(payload or "")
    if not match:
        raise PaymentError("bad_payload", "Счёт не распознан")
    return int(match.group(1)), match.group(2)


def check_before_charge(order: Order | None, *, nonce: str, user_id: int, amount: int, currency: str,
                        product: Product | None, now: datetime) -> None:
    """Answer to pre_checkout_query. Raises PaymentError with a text the buyer will see."""
    if order is None or order.nonce != nonce:
        raise PaymentError("unknown_order", "Заказ не найден, оформите заново")
    if order.user_id != user_id:
        raise PaymentError("wrong_user", "Этот счёт выставлен другому покупателю")
    if order.status == "paid":
        raise PaymentError("already_paid", "Заказ уже оплачен")
    if order.status != "pending" or now - order.created_at > INVOICE_TTL:
        raise PaymentError("expired", "Счёт устарел, оформите заказ заново")
    if amount != order.amount or currency != order.currency:
        raise PaymentError("amount_mismatch", "Сумма не совпадает со счётом")
    if product is None:
        raise PaymentError("unknown_product", "Товар больше не продаётся")
    if product.stock is not None and product.stock <= 0:
        raise PaymentError("out_of_stock", "Товар закончился, деньги не списаны")


@dataclass(frozen=True)
class Confirmation:
    order: Order
    first_time: bool   # False when Telegram delivered the same payment twice


def confirm_payment(order: Order | None, *, nonce: str, amount: int, currency: str, charge_id: str,
                    provider_charge_id: str, now: datetime) -> Confirmation:
    """Apply successful_payment. Idempotent: the same charge twice changes nothing."""
    if order is None or order.nonce != nonce:
        raise PaymentError("unknown_order", "Платёж по неизвестному заказу")
    if order.status == "paid":
        if order.charge_id == charge_id:
            return Confirmation(order, False)
        # Paid twice with different charges: money must be returned by hand, so shout.
        raise PaymentError("double_payment", f"Заказ {order.id} оплачен повторно, нужен возврат")
    if amount != order.amount or currency != order.currency:
        raise PaymentError("amount_mismatch", f"Заказ {order.id}: сумма платежа не совпала со счётом")
    # A late payment for an expired invoice is still real money: accept it.
    paid = replace(order, status="paid", charge_id=charge_id, provider_charge_id=provider_charge_id, paid_at=now)
    return Confirmation(paid, True)


def format_price(kopecks: int, currency: str = "UAH") -> str:
    symbol = {"UAH": "₴", "USD": "$", "EUR": "€"}.get(currency, currency)
    whole, frac = divmod(kopecks, 100)
    return f"{whole:,}".replace(",", " ") + (f".{frac:02d}" if frac else "") + f" {symbol}"
