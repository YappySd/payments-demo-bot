"""Full payment flow through the real aiogram router.

Telegram and the payment provider are replaced by a fake session: the test plays
their part by feeding pre_checkout_query and successful_payment updates.
"""

import asyncio
from datetime import datetime

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, AnswerPreCheckoutQuery, SendInvoice, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, OrderInfo, PreCheckoutQuery, SuccessfulPayment, Update, User

from paybot.bot import build_router
from paybot.core import Product
from paybot.service import PaymentService
from paybot.storage import Storage

TOKEN = "123456:TEST-not-a-real-token"
PROVIDER = "000000000:TEST:not-a-real-provider-token"
BUYER, ADMIN = 555, 999


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, (SendMessage, SendInvoice)):
            return Message(message_id=len(self.calls), date=datetime.now(), chat=Chat(id=BUYER, type="private"),
                           text=getattr(method, "text", None))
        return True

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError


class Env:
    def __init__(self, stock=None, test_mode=True):
        storage = Storage()
        storage.add_product(Product("tee", "Футболка", "Хлопок, M", 45000, stock))
        self.service = PaymentService(storage, "UAH", datetime.now)
        self.session = FakeSession()
        self.bot = Bot(TOKEN, session=self.session)
        self.admin_msgs = []

        async def notify_admin(text):
            self.admin_msgs.append(text)

        self.dp = Dispatcher()
        self.dp.include_router(build_router(self.service, PROVIDER, frozenset({ADMIN}), test_mode, notify_admin))
        self.n = 0

    def user(self, uid=BUYER, name="Anna"):
        return User(id=uid, is_bot=False, first_name=name)

    def message(self, uid=BUYER, name="Anna", **kw):
        return Message(message_id=self.n, date=datetime.now(), chat=Chat(id=uid, type="private"),
                       from_user=self.user(uid, name), **kw)

    async def feed(self, **update):
        self.n += 1
        self.session.calls.clear()
        await self.dp.feed_update(self.bot, Update(update_id=self.n, **update))
        return list(self.session.calls)

    async def send(self, text, uid=BUYER):
        return await self.feed(message=self.message(uid, text=text).as_(self.bot))

    async def press(self, data, uid=BUYER):
        cb = CallbackQuery(id=str(self.n), from_user=self.user(uid), chat_instance="x",
                           message=self.message(uid, text="menu"), data=data).as_(self.bot)
        return await self.feed(callback_query=cb)

    async def pre_checkout(self, invoice, uid=BUYER, amount=None):
        q = PreCheckoutQuery(id="pcq", from_user=self.user(uid), currency=invoice.currency,
                             total_amount=amount if amount is not None else invoice.prices[0].amount,
                             invoice_payload=invoice.payload).as_(self.bot)
        calls = await self.feed(pre_checkout_query=q)
        return next(c for c in calls if isinstance(c, AnswerPreCheckoutQuery))

    async def successful_payment(self, invoice, charge="tg-charge-1", uid=BUYER, name="Anna", phone="380501234567"):
        sp = SuccessfulPayment(currency=invoice.currency, total_amount=invoice.prices[0].amount,
                               invoice_payload=invoice.payload, telegram_payment_charge_id=charge,
                               provider_payment_charge_id="prov-" + charge, order_info=OrderInfo(phone_number=phone))
        return await self.feed(message=self.message(uid, name, successful_payment=sp).as_(self.bot))

    async def buy(self, uid=BUYER):
        calls = await self.press("buy:tee", uid)
        return next(c for c in calls if isinstance(c, SendInvoice))


def run(coro):
    return asyncio.run(coro)


def test_start_shows_test_banner_and_catalog():
    async def scenario():
        env = Env()
        [msg] = await env.send("/start")
        assert "Тестовый режим" in msg.text
        assert msg.reply_markup.inline_keyboard[0][0].callback_data == "buy:tee"
        [msg] = await Env(test_mode=False).send("/start")
        assert "Тестовый режим" not in msg.text

    run(scenario())


def test_full_payment_flow():
    async def scenario():
        env = Env(stock=3)
        invoice = await env.buy()
        assert invoice.provider_token == PROVIDER and invoice.currency == "UAH"
        assert invoice.prices[0].amount == 45000 and invoice.payload.startswith("order:")

        answer = await env.pre_checkout(invoice)
        assert answer.ok is True

        calls = await env.successful_payment(invoice)
        assert any("Оплачено" in c.text for c in calls if isinstance(c, SendMessage))
        assert len(env.admin_msgs) == 1 and "380501234567" in env.admin_msgs[0]
        assert env.service.storage.product("tee").stock == 2

    run(scenario())


def test_pre_checkout_refuses_tampered_amount_and_other_user():
    async def scenario():
        env = Env()
        invoice = await env.buy()
        answer = await env.pre_checkout(invoice, amount=100)
        assert answer.ok is False and "Сумма" in answer.error_message
        answer = await env.pre_checkout(invoice, uid=777)
        assert answer.ok is False

    run(scenario())


def test_repeated_successful_payment_notifies_once():
    async def scenario():
        env = Env(stock=3)
        invoice = await env.buy()
        await env.successful_payment(invoice)
        calls = await env.successful_payment(invoice)
        assert calls == [] and len(env.admin_msgs) == 1
        assert env.service.storage.product("tee").stock == 2

    run(scenario())


def test_double_payment_alerts_admin():
    async def scenario():
        env = Env()
        invoice = await env.buy()
        await env.successful_payment(invoice, charge="a")
        calls = await env.successful_payment(invoice, charge="b")
        assert "оплачен повторно" in env.admin_msgs[-1]
        assert any("требует проверки" in c.text for c in calls if isinstance(c, SendMessage))

    run(scenario())


def test_sold_out_button():
    async def scenario():
        env = Env(stock=0)
        calls = await env.press("buy:tee")
        alert = next(c for c in calls if isinstance(c, AnswerCallbackQuery))
        assert alert.show_alert and "закончился" in alert.text
        assert not any(isinstance(c, SendInvoice) for c in calls)

    run(scenario())


def test_sales_report_only_for_admin():
    async def scenario():
        env = Env()
        invoice = await env.buy()
        await env.successful_payment(invoice)
        assert await env.send("/sales") == []
        [report] = await env.send("/sales", uid=ADMIN)
        assert "Оплачено заказов: 1" in report.text

    run(scenario())


def test_main_module_imports():
    import paybot.main  # noqa: F401


def test_buyer_name_with_html_is_escaped_for_admin():
    async def scenario():
        env = Env()
        invoice = await env.buy()
        await env.successful_payment(invoice, name="<b>Hacker</b> & Co", phone="<i>1</i>")
        text = env.admin_msgs[-1]
        assert "&lt;b&gt;Hacker&lt;/b&gt; &amp; Co" in text and "&lt;i&gt;1&lt;/i&gt;" in text
        assert "<b>Hacker" not in text

    run(scenario())
