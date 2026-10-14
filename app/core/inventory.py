"""Единый остаток и резерв под заказ. Все каналы списывают из одной строки products,
поэтому один товар нельзя продать дважды."""
import sqlite3

from . import CoreError, NotFound, OutOfStock


def reserve(c: sqlite3.Connection, product_id: int, qty: int) -> None:
    cur = c.execute(
        "UPDATE products SET reserved = reserved + ? WHERE id = ? AND stock - reserved >= ?",
        (qty, product_id, qty),
    )
    if cur.rowcount == 0:
        p = c.execute("SELECT sku, stock, reserved FROM products WHERE id = ?", (product_id,)).fetchone()
        if not p:
            raise NotFound("Товар")
        raise OutOfStock(p["sku"], qty, p["stock"] - p["reserved"])


def release(c: sqlite3.Connection, product_id: int, qty: int) -> None:
    c.execute("UPDATE products SET reserved = MAX(0, reserved - ?) WHERE id = ?", (qty, product_id))


def ship(c: sqlite3.Connection, product_id: int, qty: int) -> None:
    """Товар ушёл со склада: списываем и остаток, и резерв."""
    cur = c.execute(
        "UPDATE products SET stock = stock - ?, reserved = reserved - ? WHERE id = ? AND reserved >= ?",
        (qty, qty, product_id, qty),
    )
    if cur.rowcount == 0:
        raise CoreError("Списание без резерва невозможно", status=409)


def restock(c: sqlite3.Connection, product_id: int, qty: int) -> None:
    c.execute("UPDATE products SET stock = stock + ? WHERE id = ?", (qty, product_id))


def set_stock(c: sqlite3.Connection, product_id: int, stock: int) -> None:
    p = c.execute("SELECT reserved FROM products WHERE id = ?", (product_id,)).fetchone()
    if not p:
        raise NotFound("Товар")
    if stock < p["reserved"]:
        raise CoreError(f"Остаток не может быть меньше резерва ({p['reserved']})", status=409)
    c.execute("UPDATE products SET stock = ? WHERE id = ?", (stock, product_id))
