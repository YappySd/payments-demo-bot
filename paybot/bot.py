"""Telegram adapter (aiogram 3): catalog, invoice, pre-checkout, successful payment."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Message, PreCheckoutQuery

from paybot.core import PaymentError, format_price
from paybot.service import PaymentService

log = logging.getLogger(__name__)

TEST_BANNER = (
    "🧪 <b>Тестовый режим.</b> Деньги не списываются. "
    "Для тестового Stripe подойдёт карта <code>4242 4242 4242 4242</code>, любой срок в будущем, любой CVC."
)


def build_router(service: PaymentService, provider_token: str, admin_ids: frozenset[int], test_mode: bool,
                 notify_admin) -> Router:
    router = Router()

    def catalog_kb() -> InlineKeyboardMarkup:
        rows = [
            [InlineKeyboardButton(text=f"{p.title} · {format_price(p.price, service.currency)}", callback_data=f"buy:{p.id}")]
            for p in service.storage.products()
        ]
        return InlineKeyboardMarkup(inline_keyboard=rows)

    @router.message(CommandStart())
    async def start(message: Message) -> None:
        text = "Выберите товар, счёт придёт сюда же, оплата прямо в Telegram."
        if test_mode:
            text = f"{TEST_BANNER}\n\n{text}"
        await message.answer(text, reply_markup=catalog_kb())

    @router.callback_query(F.data.startswith("buy:"))
    async def buy(callback: CallbackQuery) -> None:
        product_id = callback.data.split(":", 1)[1]
        try:
            order = service.create_order(callback.from_user.id, product_id)
        except PaymentError as exc:
            await callback.answer(exc.message, show_alert=True)
            return
        product = service.storage.product(product_id)
        await callback.message.answer_invoice(
            title=product.title[:32],
            description=(product.description or product.title)[:255],
            payload=order.payload,
            provider_token=provider_token,
            currency=order.currency,
            prices=[LabeledPrice(label=product.title[:32], amount=order.amount)],
            need_phone_number=True,
        )
        await callback.answer()

    @router.pre_checkout_query()
    async def pre_checkout(query: PreCheckoutQuery) -> None:
        # Telegram waits ~10 s for this answer; only fast local checks here.
        try:
            service.pre_checkout(query.invoice_payload, query.from_user.id, query.total_amount, query.currency)
        except PaymentError as exc:
            await query.answer(ok=False, error_message=exc.message)
            return
        await query.answer(ok=True)

    @router.message(F.successful_payment)
    async def paid(message: Message) -> None:
        sp = message.successful_payment
        try:
            result = service.record_payment(sp.invoice_payload, sp.total_amount, sp.currency,
                                            sp.telegram_payment_charge_id, sp.provider_payment_charge_id)
        except PaymentError as exc:
            log.error("Платёж требует внимания: %s", exc.message)
            await notify_admin(f"⚠️ {exc.message}\ncharge_id: <code>{sp.telegram_payment_charge_id}</code>")
            await message.answer("Оплата получена, но заказ требует проверки. Мы свяжемся с вами.")
            return
        if not result.first_time:
            return
        order = result.order
        await message.answer(f"✅ Оплачено: заказ №{order.id}, {format_price(order.amount, order.currency)}. Спасибо!")
        phone = sp.order_info.phone_number if sp.order_info and sp.order_info.phone_number else "не указан"
        await notify_admin(
            f"💰 <b>Оплата</b> заказ №{order.id}\n{order.product_id} · {format_price(order.amount, order.currency)}\n"
            f"Покупатель: {message.from_user.full_name} ({phone})"
            + ("\n🧪 тестовый режим" if test_mode else "")
        )

    @router.message(Command("sales"))
    async def sales(message: Message) -> None:
        if message.from_user.id not in admin_ids:
            return
        count, total = service.storage.revenue()
        lines = [f"Оплачено заказов: {count}, сумма: {format_price(total, service.currency)}"]
        for o in service.storage.orders("paid", limit=10):
            lines.append(f"№{o.id} · {o.product_id} · {format_price(o.amount, o.currency)} · {o.paid_at:%d.%m %H:%M}")
        await message.answer("\n".join(lines))

    return router
