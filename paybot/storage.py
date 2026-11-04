"""SQLite: products, orders, payments. Stock changes and payment marks are one transaction."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from paybot.core import Order, Product

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL,
    price INTEGER NOT NULL, stock INTEGER
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL, product_id TEXT NOT NULL, amount INTEGER NOT NULL, currency TEXT NOT NULL,
    status TEXT NOT NULL, created_at TEXT NOT NULL, nonce TEXT NOT NULL,
    charge_id TEXT NOT NULL DEFAULT '', provider_charge_id TEXT NOT NULL DEFAULT '', paid_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_charge ON orders(charge_id) WHERE charge_id != '';
"""

_COLS = "id, user_id, product_id, amount, currency, status, created_at, nonce, charge_id, provider_charge_id, paid_at"


class Storage:
    def __init__(self, path: str = ":memory:"):
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # ---------------------------------------------------------- products

    def load_catalog(self, path: str | Path) -> None:
        """Insert or update products from JSON. Stock in the DB is kept: it changes with sales."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        with self._lock:
            for p in data["products"]:
                self._conn.execute(
                    "INSERT INTO products (id, title, description, price, stock) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET title = excluded.title, description = excluded.description, "
                    "price = excluded.price",
                    (p["id"], p["title"], p.get("description", ""), int(p["price"]), p.get("stock")),
                )

    def add_product(self, product: Product) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO products (id, title, description, price, stock) VALUES (?, ?, ?, ?, ?)",
                (product.id, product.title, product.description, product.price, product.stock),
            )

    def products(self) -> list[Product]:
        with self._lock:
            rows = self._conn.execute("SELECT id, title, description, price, stock FROM products ORDER BY rowid").fetchall()
        return [Product(*r) for r in rows]

    def product(self, product_id: str) -> Product | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, title, description, price, stock FROM products WHERE id = ?", (product_id,)
            ).fetchone()
        return Product(*row) if row else None

    # ------------------------------------------------------------ orders

    @staticmethod
    def _order(row) -> Order:
        (oid, uid, pid, amount, currency, status, created, nonce, charge, pcharge, paid) = row
        return Order(oid, uid, pid, amount, currency, status, datetime.fromisoformat(created), nonce, charge, pcharge,
                     datetime.fromisoformat(paid) if paid else None)

    def add_order(self, order: Order) -> Order:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO orders (user_id, product_id, amount, currency, status, created_at, nonce) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (order.user_id, order.product_id, order.amount, order.currency, order.status,
                 order.created_at.isoformat(), order.nonce),
            )
        return self.order(cur.lastrowid)

    def order(self, order_id: int) -> Order | None:
        with self._lock:
            row = self._conn.execute(f"SELECT {_COLS} FROM orders WHERE id = ?", (order_id,)).fetchone()
        return self._order(row) if row else None

    def mark_paid(self, order: Order) -> bool:
        """Mark paid and take one item from stock, atomically. False if it was already paid."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                cur = self._conn.execute(
                    "UPDATE orders SET status = 'paid', charge_id = ?, provider_charge_id = ?, paid_at = ? "
                    "WHERE id = ? AND status != 'paid'",
                    (order.charge_id, order.provider_charge_id, order.paid_at.isoformat(), order.id),
                )
                if cur.rowcount == 1:
                    # MAX(...,0): the money is already taken, stock must not go negative in reports.
                    self._conn.execute(
                        "UPDATE products SET stock = MAX(stock - 1, 0) WHERE id = ? AND stock IS NOT NULL",
                        (order.product_id,),
                    )
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
        return cur.rowcount == 1

    def orders(self, status: str | None = None, limit: int = 20) -> list[Order]:
        sql = f"SELECT {_COLS} FROM orders" + (" WHERE status = ?" if status else "") + " ORDER BY id DESC LIMIT ?"
        with self._lock:
            rows = self._conn.execute(sql, (status, limit) if status else (limit,)).fetchall()
        return [self._order(r) for r in rows]

    def revenue(self) -> tuple[int, int]:
        """(paid orders, sum in kopecks)."""
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM orders WHERE status = 'paid'"
            ).fetchone()
