import json
import threading
from datetime import datetime, timedelta

import pytest

from paybot.core import PaymentError, Product
from paybot.service import PaymentService
from paybot.storage import Storage

NOW = datetime(2030, 1, 7, 12, 0)


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


def make(stock=None, path=":memory:"):
    storage = Storage(path)
    storage.add_product(Product("tee", "Футболка", "Хлопок", 45000, stock))
    clock = Clock()
    return PaymentService(storage, "UAH", clock), clock


def pay(service, order, charge="tg-1"):
    return service.record_payment(order.payload, order.amount, "UAH", charge, "pr-" + charge)


def test_happy_path_takes_stock():
    service, _ = make(stock=2)
    order = service.create_order(7, "tee")
    service.pre_checkout(order.payload, 7, 45000, "UAH")
    assert pay(service, order).first_time
    assert service.storage.product("tee").stock == 1
    assert service.storage.revenue() == (1, 45000)


def test_duplicate_delivery_takes_stock_once():
    service, _ = make(stock=5)
    order = service.create_order(7, "tee")
    assert pay(service, order).first_time
    assert not pay(service, order).first_time
    assert service.storage.product("tee").stock == 4


def test_concurrent_duplicate_deliveries(tmp_path):
    service, _ = make(stock=5, path=str(tmp_path / "p.db"))
    order = service.create_order(7, "tee")
    results, barrier = [], threading.Barrier(8)

    def worker():
        barrier.wait()
        results.append(pay(service, order).first_time)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 1
    assert service.storage.product("tee").stock == 4


def test_last_item_two_buyers():
    service, _ = make(stock=1)
    first = service.create_order(1, "tee")
    second = service.create_order(2, "tee")
    pay(service, first)
    with pytest.raises(PaymentError) as exc:
        service.pre_checkout(second.payload, 2, 45000, "UAH")
    assert exc.value.code == "out_of_stock"
    with pytest.raises(PaymentError):
        service.create_order(3, "tee")


def test_expired_invoice_is_refused_before_charge():
    service, clock = make()
    order = service.create_order(7, "tee")
    clock.now = NOW + timedelta(minutes=45)
    with pytest.raises(PaymentError) as exc:
        service.pre_checkout(order.payload, 7, 45000, "UAH")
    assert exc.value.code == "expired"


def test_forged_payload_is_refused():
    service, _ = make()
    order = service.create_order(7, "tee")
    forged = f"order:{order.id}:{'0' * 16}"
    with pytest.raises(PaymentError):
        service.pre_checkout(forged, 7, 45000, "UAH")
    with pytest.raises(PaymentError):
        service.pre_checkout("order:999:0123456789abcdef", 7, 45000, "UAH")


def test_unknown_product():
    service, _ = make()
    with pytest.raises(PaymentError):
        service.create_order(7, "nope")


def test_catalog_reload_keeps_sold_stock(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps({"products": [{"id": "tee", "title": "Футболка", "price": 45000, "stock": 3}]}), encoding="utf-8")
    storage = Storage()
    storage.load_catalog(path)
    service = PaymentService(storage, "UAH", Clock())
    pay(service, service.create_order(1, "tee"))
    path.write_text(json.dumps({"products": [{"id": "tee", "title": "Футболка", "price": 47000, "stock": 3}]}), encoding="utf-8")
    storage.load_catalog(path)
    product = storage.product("tee")
    assert product.stock == 2 and product.price == 47000


def test_example_catalog_is_valid():
    from pathlib import Path

    storage = Storage()
    storage.load_catalog(Path(__file__).resolve().parent.parent / "catalog.example.json")
    assert len(storage.products()) >= 2
