from datetime import datetime, timedelta

import pytest

from paybot.core import (
    Order,
    PaymentError,
    Product,
    check_before_charge,
    confirm_payment,
    format_price,
    is_test_token,
    new_order,
    parse_payload,
)

NOW = datetime(2030, 1, 7, 12, 0)
PRODUCT = Product("course", "Курс", "Видео-курс", 49900, None)
LIMITED = Product("tee", "Футболка", "", 45000, 1)


def order(**kw):
    o = new_order(7, PRODUCT, "UAH", NOW)
    data = {**o.__dict__, "id": 1}
    data.update(kw)
    return Order(**data)


def check(o, **kw):
    args = dict(nonce=o.nonce if o else "x" * 16, user_id=7, amount=49900, currency="UAH", product=PRODUCT, now=NOW)
    args.update(kw)
    check_before_charge(o, **args)


def test_test_token_detection():
    assert is_test_token("284685063:TEST:abcdef")
    assert not is_test_token("284685063:LIVE:abcdef")


def test_payload_round_trip():
    o = order()
    assert parse_payload(o.payload) == (1, o.nonce)
    for bad in ("", "order:1", "order:x:0123456789abcdef", "order:1:short", "evil:1:0123456789abcdef"):
        with pytest.raises(PaymentError):
            parse_payload(bad)


def test_nonces_differ():
    assert new_order(1, PRODUCT, "UAH", NOW).nonce != new_order(1, PRODUCT, "UAH", NOW).nonce


def test_sold_out_product_cannot_be_ordered():
    with pytest.raises(PaymentError) as exc:
        new_order(1, Product("x", "x", "", 1, 0), "UAH", NOW)
    assert exc.value.code == "out_of_stock"


def test_valid_pre_checkout_passes():
    check(order())


@pytest.mark.parametrize(
    "kw, code",
    [
        ({"nonce": "0" * 16}, "unknown_order"),
        ({"user_id": 8}, "wrong_user"),
        ({"amount": 100}, "amount_mismatch"),
        ({"currency": "USD"}, "amount_mismatch"),
        ({"now": NOW + timedelta(minutes=31)}, "expired"),
        ({"product": None}, "unknown_product"),
        ({"product": Product("course", "Курс", "", 49900, 0)}, "out_of_stock"),
    ],
)
def test_pre_checkout_refusals(kw, code):
    with pytest.raises(PaymentError) as exc:
        check(order(), **kw)
    assert exc.value.code == code


def test_pre_checkout_on_paid_or_missing_order():
    with pytest.raises(PaymentError) as exc:
        check(order(status="paid"))
    assert exc.value.code == "already_paid"
    with pytest.raises(PaymentError):
        check(None)


def confirm(o, **kw):
    args = dict(nonce=o.nonce if o else "x" * 16, amount=49900, currency="UAH", charge_id="tg-1",
                provider_charge_id="pr-1", now=NOW)
    args.update(kw)
    return confirm_payment(o, **args)


def test_confirm_marks_paid():
    result = confirm(order())
    assert result.first_time and result.order.status == "paid"
    assert result.order.charge_id == "tg-1" and result.order.paid_at == NOW


def test_same_payment_twice_is_ignored():
    paid = confirm(order()).order
    again = confirm(paid)
    assert again.first_time is False and again.order == paid


def test_second_different_payment_is_an_alarm():
    paid = confirm(order()).order
    with pytest.raises(PaymentError) as exc:
        confirm(paid, charge_id="tg-2")
    assert exc.value.code == "double_payment"


def test_late_payment_on_expired_invoice_is_still_recorded():
    assert confirm(order(), now=NOW + timedelta(hours=2)).order.status == "paid"


def test_confirm_rejects_wrong_amount():
    with pytest.raises(PaymentError) as exc:
        confirm(order(), amount=1)
    assert exc.value.code == "amount_mismatch"


def test_format_price():
    assert format_price(49900) == "499 ₴"
    assert format_price(123450, "USD") == "1 234.50 $"
