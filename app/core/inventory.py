"""Stock operations: product totals mirror its variants once configured."""
import sqlite3

from .. import db
from . import CoreError, NotFound, OutOfStock


def variants(c: sqlite3.Connection, product_id: int) -> list[dict]:
    return db.rows(c.execute(
        "SELECT *, stock-reserved AS free FROM product_variants WHERE product_id=? ORDER BY id", (product_id,)))


def declared_variants(data: dict) -> list[str]:
    return list(dict.fromkeys(v.strip() for v in (data.get("variants") or "").split(",") if v.strip()))


def set_variants(c: sqlite3.Connection, product_id: int, entries: list[dict]) -> None:
    p = c.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
    if not p:
        raise NotFound("Товар")
    current = variants(c, product_id)
    if not current and p["reserved"]:
        raise CoreError("Сначала завершите или отмените заказы с резервом этого товара. Затем распределите остаток по вариантам.", status=409)
    if not entries or len(entries) > 200:
        raise CoreError("Добавьте от 1 до 200 вариантов")
    cleaned = {}
    for entry in entries:
        label, stock = str(entry.get("label") or "").strip(), entry.get("stock")
        if not label or len(label) > 100 or any(ch in label for ch in ",\n\r"):
            raise CoreError("Название варианта: от 1 до 100 символов, без запятых и переносов строк")
        if type(stock) is not int or stock < 0:
            raise CoreError("Остаток варианта должен быть целым неотрицательным числом")
        if label.casefold() in cleaned:
            raise CoreError("Варианты не должны повторяться")
        cleaned[label.casefold()] = {"label": label, "stock": stock}
    for v in current:
        entry = cleaned.get(v["label"].casefold())
        if not entry or entry["label"] != v["label"]:
            raise CoreError("Сохраните названия существующих вариантов. Для снятия с продажи установите остаток 0.")
        if entry["stock"] < v["reserved"]:
            raise CoreError(f"{v['label']}: остаток меньше резерва ({v['reserved']})", status=409)
    for entry in cleaned.values():
        c.execute("INSERT INTO product_variants(product_id,label,stock) VALUES (?,?,?) "
                  "ON CONFLICT(product_id,label) DO UPDATE SET stock=excluded.stock",
                  (product_id, entry["label"], entry["stock"]))
    c.execute("UPDATE products SET stock=? WHERE id=?", (sum(v["stock"] for v in cleaned.values()), product_id))


def resolve_variant(c: sqlite3.Connection, product_id: int, label: str | None, data: dict) -> tuple[str | None, int | None]:
    tracked = variants(c, product_id)
    choices = [v["label"] for v in tracked] if tracked else declared_variants(data)
    if label is not None and not isinstance(label, str):
        raise CoreError("Название варианта должно быть текстом", code="unknown_variant")
    label = (label or "").strip()
    if not label and len(choices) == 1:
        label = choices[0]
    if not label and choices:
        raise CoreError("Выберите размер / цвет товара", code="variant_required")
    match = next((v for v in choices if v.casefold() == label.casefold()), None) if label else None
    if label and match is None:
        raise CoreError(f"Вариант «{label}» не найден у товара", code="unknown_variant")
    variant_id = next((v["id"] for v in tracked if v["label"] == match), None)
    return match, variant_id


def _variant(c, product_id, variant_id):
    if variant_id is None:
        if variants(c, product_id):
            raise CoreError("Для этого товара нужно выбрать вариант", code="variant_required")
        return None
    v = c.execute("SELECT * FROM product_variants WHERE id=? AND product_id=?", (variant_id, product_id)).fetchone()
    if not v:
        raise CoreError("Вариант не принадлежит товару", code="unknown_variant")
    return v


def reserve(c: sqlite3.Connection, product_id: int, qty: int, variant_id: int | None = None) -> None:
    if type(qty) is not int or qty <= 0:
        raise CoreError("Количество должно быть положительным целым числом")
    p = c.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
    if not p:
        raise NotFound("Товар")
    v = _variant(c, product_id, variant_id)
    free = v["stock"] - v["reserved"] if v else p["stock"] - p["reserved"]
    if qty > free or qty > p["stock"] - p["reserved"]:
        raise OutOfStock(f"{p['sku']} / {v['label']}" if v else p["sku"], qty, free)
    cur = c.execute(
        "UPDATE products SET reserved = reserved + ? WHERE id = ? AND stock - reserved >= ? "
        "AND (? IS NULL OR EXISTS (SELECT 1 FROM product_variants WHERE id=? AND product_id=? AND stock-reserved>=?))",
        (qty, product_id, qty, variant_id, variant_id, product_id, qty),
    )
    if cur.rowcount == 0:
        p = c.execute("SELECT sku, stock, reserved FROM products WHERE id = ?", (product_id,)).fetchone()
        if not p:
            raise NotFound("Товар")
        raise OutOfStock(p["sku"], qty, p["stock"] - p["reserved"])
    if v:
        c.execute("UPDATE product_variants SET reserved=reserved+? WHERE id=?", (qty, variant_id))


def release(c: sqlite3.Connection, product_id: int, qty: int, variant_id: int | None = None) -> None:
    v = _variant(c, product_id, variant_id)
    if v and v["reserved"] < qty:
        raise CoreError("Недостаточно резерва варианта", status=409)
    cur = c.execute("UPDATE products SET reserved=reserved-? WHERE id=? AND reserved>=?", (qty, product_id, qty))
    if cur.rowcount == 0:
        raise CoreError("Недостаточно резерва товара", status=409)
    if v:
        c.execute("UPDATE product_variants SET reserved=reserved-? WHERE id=?", (qty, variant_id))


def ship(c: sqlite3.Connection, product_id: int, qty: int, variant_id: int | None = None) -> None:
    """Товар ушёл со склада: списываем и остаток, и резерв."""
    v = _variant(c, product_id, variant_id)
    if v and v["reserved"] < qty:
        raise CoreError("Списание варианта без резерва невозможно", status=409)
    cur = c.execute(
        "UPDATE products SET stock = stock - ?, reserved = reserved - ? WHERE id = ? AND reserved >= ?",
        (qty, qty, product_id, qty),
    )
    if cur.rowcount == 0:
        raise CoreError("Списание без резерва невозможно", status=409)
    if v:
        c.execute("UPDATE product_variants SET stock=stock-?, reserved=reserved-? WHERE id=?", (qty, qty, variant_id))


def restock(c: sqlite3.Connection, product_id: int, qty: int, variant_id: int | None = None) -> None:
    v = _variant(c, product_id, variant_id)
    c.execute("UPDATE products SET stock = stock + ? WHERE id = ?", (qty, product_id))
    if v:
        c.execute("UPDATE product_variants SET stock=stock+? WHERE id=?", (qty, variant_id))


def set_stock(c: sqlite3.Connection, product_id: int, stock: int) -> None:
    if type(stock) is not int or stock < 0:
        raise CoreError("Остаток должен быть целым неотрицательным числом")
    p = c.execute("SELECT reserved FROM products WHERE id = ?", (product_id,)).fetchone()
    if not p:
        raise NotFound("Товар")
    if variants(c, product_id):
        raise CoreError("Измените остатки отдельных вариантов — общий остаток считается автоматически")
    if stock < p["reserved"]:
        raise CoreError(f"Остаток не может быть меньше резерва ({p['reserved']})", status=409)
    c.execute("UPDATE products SET stock = ? WHERE id = ?", (stock, product_id))
