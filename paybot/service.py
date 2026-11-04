"""Use cases: create order, answer pre-checkout, record payment. Storage + core, no Telegram."""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from paybot.core import Confirmation, Order, PaymentError, check_before_charge, confirm_payment, new_order, parse_payload
from paybot.storage import Storage


class PaymentService:
    def __init__(self, storage: Storage, currency: str, clock: Callable[[], datetime]):
        self.storage = storage
        self.currency = currency
        self.clock = clock

    def create_order(self, user_id: int, product_id: str) -> Order:
        product = self.storage.product(product_id)
        if product is None:
            raise PaymentError("unknown_product", "Такого товара нет")
        return self.storage.add_order(new_order(user_id, product, self.currency, self.clock()))

    def pre_checkout(self, payload: str, user_id: int, amount: int, currency: str) -> Order:
        order_id, nonce = parse_payload(payload)
        order = self.storage.order(order_id)
        product = self.storage.product(order.product_id) if order else None
        check_before_charge(order, nonce=nonce, user_id=user_id, amount=amount, currency=currency,
                            product=product, now=self.clock())
        return order

    def record_payment(self, payload: str, amount: int, currency: str, charge_id: str,
                       provider_charge_id: str) -> Confirmation:
        order_id, nonce = parse_payload(payload)
        result = confirm_payment(self.storage.order(order_id), nonce=nonce, amount=amount, currency=currency,
                                 charge_id=charge_id, provider_charge_id=provider_charge_id, now=self.clock())
        if result.first_time and not self.storage.mark_paid(result.order):
            # Another handler marked it a moment earlier: report it as a repeat, do not notify twice.
            return Confirmation(self.storage.order(order_id), False)
        return result
