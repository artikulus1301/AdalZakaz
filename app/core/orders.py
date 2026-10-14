"""Заказы: приём из очереди, резерв, статусы, подтверждение оплаты из платёжного источника."""
import secrets
import sqlite3
from datetime import datetime

from .. import db
from . import CoreError, NotFound, OutOfStock, events, inventory, passport

CHANNELS = {
    "kaspi": "Kaspi Магазин",
    "olx": "OLX",
    "instagram": "Instagram",
    "whatsapp": "WhatsApp",
    "telegram": "Telegram",
    "site": "Сайт / страница товара",
}

STATUSES = {
    "new": "Новый (резерв)",
    "paid": "Оплачен",
    "assembled": "Собран",
    "shipped": "Передан в доставку",
    "delivered": "Выдан",
    "closed": "Закрыт",
    "cancelled": "Отменён",
}

TRANSITIONS = {
    "new": {"paid", "cancelled"},
    "paid": {"assembled", "cancelled"},
    "assembled": {"shipped", "cancelled"},
    "shipped": {"delivered"},
    "delivered": {"closed"},
    "closed": set(),
    "cancelled": set(),
}

# Источник подтверждения оплаты. Скриншот от покупателя — НЕ источник.
PAYMENT_SOURCES = {
    "kaspi_api": "Kaspi (данные площадки)",
    "bank_statement": "Выписка банка / уведомление банка продавцу",
    "payment_provider": "Платёжный провайдер (вебхук/отчёт)",
    "cash_register": "Наличные с фискальным чеком",
}


def _new_number(c: sqlite3.Connection, order_id: int) -> str:
    num = f"AZ-{order_id:06d}"
    c.execute("UPDATE orders SET number = ? WHERE id = ?", (num, order_id))
    return num


def create(c: sqlite3.Connection, payload: dict, actor: str) -> int:
    """Создаёт заказ из нормализованного входящего события. Без свободного остатка заказ не принимается."""
    channel = payload.get("channel")
    if channel not in CHANNELS:
        raise CoreError("Неизвестный канал")
    items = payload.get("items") or []
    if not items:
        raise CoreError("В заказе нет товаров")
    ext = payload.get("external_id") or None
    if ext and c.execute("SELECT 1 FROM orders WHERE channel = ? AND external_id = ?", (channel, ext)).fetchone():
        raise CoreError("Такой заказ уже принят", status=409, code="duplicate")

    buyer = payload.get("buyer") or {}
    lang = buyer.get("lang") if buyer.get("lang") in ("ru", "kk") else "ru"
    token = None if channel == "kaspi" else secrets.token_urlsafe(12)
    cur = c.execute(
        "INSERT INTO orders(channel, external_id, buyer_name, buyer_phone, buyer_address, buyer_lang, status, "
        "total, public_token, delivery_zone, delivery_window, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            channel, ext, buyer.get("name"), buyer.get("phone"), buyer.get("address"), lang, "new", 0, token,
            payload.get("delivery_zone"), payload.get("delivery_window"), payload.get("created_at") or db.now_iso(),
        ),
    )
    oid = cur.lastrowid
    number = _new_number(c, oid)
    total = 0
    for it in items:
        p = c.execute("SELECT * FROM products WHERE sku = ?", (it.get("sku"),)).fetchone()
        if not p:
            raise CoreError(f"Товар {it.get('sku')} не найден в паспортах")
        qty = int(it.get("qty") or 1)
        if qty <= 0:
            raise CoreError("Количество должно быть больше нуля")
        inventory.reserve(c, p["id"], qty)
        pv = passport.get_version(c, p["id"])
        price = int(it.get("price") or pv["data"].get("price") or 0)
        variant = (it.get("variant") or "").strip() or None
        c.execute(
            "INSERT INTO order_items(order_id, product_id, passport_version, qty, price, variant) VALUES (?,?,?,?,?,?)",
            (oid, p["id"], pv["version"], qty, price, variant),
        )
        total += price * qty
    c.execute("UPDATE orders SET total = ? WHERE id = ?", (total, oid))
    events.log(c, "order_created", actor, order_id=oid, payload={"number": number, "channel": channel, "total": total})

    if payload.get("paid_via") == "kaspi_api":
        confirm_payment(c, oid, "kaspi_api", f"kaspi:{ext or number}", total, actor)
    return oid


def get(c: sqlite3.Connection, order_id: int) -> dict:
    o = db.row(c.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone())
    if not o:
        raise NotFound("Заказ")
    o["items"] = db.rows(
        c.execute(
            "SELECT oi.*, p.sku, p.category FROM order_items oi JOIN products p ON p.id = oi.product_id "
            "WHERE oi.order_id = ?",
            (order_id,),
        )
    )
    for it in o["items"]:
        pv = passport.get_version(c, it["product_id"], it["passport_version"])
        it["passport"] = pv["data"]
        it["title_ru"] = pv["data"].get("title_ru")
        it["title_kk"] = pv["data"].get("title_kk")
        cur = c.execute("SELECT current_version FROM products WHERE id = ?", (it["product_id"],)).fetchone()[0]
        it["passport_outdated"] = cur != it["passport_version"]
    o["channel_label"] = CHANNELS.get(o["channel"], o["channel"])
    o["status_label"] = STATUSES.get(o["status"], o["status"])
    o["next_statuses"] = sorted(TRANSITIONS.get(o["status"], set()))
    return o


def by_token(c: sqlite3.Connection, token: str) -> dict:
    r = c.execute("SELECT id FROM orders WHERE public_token = ?", (token,)).fetchone()
    if not r:
        raise NotFound("Заказ")
    return get(c, r["id"])


def confirm_payment(c: sqlite3.Connection, order_id: int, source: str, ref: str, amount: int, actor: str) -> bool:
    if source == "screenshot" or source not in PAYMENT_SOURCES:
        raise CoreError(
            "Скриншот оплаты не подтверждает платёж. Подтвердите оплату из банка, Kaspi или платёжного провайдера.",
            code="bad_payment_source",
        )
    ref = (ref or "").strip()
    if not ref:
        raise CoreError("Нужен номер операции из платёжного источника")
    idem = f"payment:{source}:{ref}"
    o = c.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not o:
        raise NotFound("Заказ")
    if events.seen(c, idem):
        if o["payment_ref"] == ref:
            return False  # повтор той же команды — ничего не делаем
        raise CoreError("Эта операция уже привязана к другому заказу", status=409, code="payment_reused")
    if o["payment_status"] == "paid":
        raise CoreError("Заказ уже оплачен", status=409)
    if int(amount) != o["total"]:
        raise CoreError(f"Сумма операции {amount} тг не совпадает с суммой заказа {o['total']} тг", status=409)
    if o["status"] != "new":
        raise CoreError("Оплату можно подтвердить только для нового заказа", status=409)
    c.execute(
        "UPDATE orders SET payment_status = 'paid', payment_source = ?, payment_ref = ?, status = 'paid' WHERE id = ?",
        (source, ref, order_id),
    )
    events.log(c, "payment_confirmed", actor, order_id=order_id,
               payload={"source": source, "ref": ref, "amount": amount}, idem_key=idem)
    return True


def _has_packing_evidence(c: sqlite3.Connection, order_id: int) -> bool:
    return c.execute(
        "SELECT 1 FROM evidence WHERE order_id = ? AND kind IN ('packing_video', 'packing_photo')", (order_id,)
    ).fetchone() is not None


def set_status(c: sqlite3.Connection, order_id: int, status: str, actor: str, reason: str | None = None) -> None:
    o = c.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not o:
        raise NotFound("Заказ")
    if status not in TRANSITIONS.get(o["status"], set()):
        raise CoreError(f"Переход «{STATUSES[o['status']]}» → «{STATUSES.get(status, status)}» не разрешён", status=409)
    items = c.execute("SELECT product_id, qty FROM order_items WHERE order_id = ?", (order_id,)).fetchall()

    if status == "assembled" and not _has_packing_evidence(c, order_id):
        raise CoreError("Перед статусом «Собран» загрузите видео или фото упаковки — это доказательство в споре.",
                        code="need_packing_evidence")
    if status == "shipped":
        if o["channel"] != "kaspi" and not o["address_confirmed"]:
            raise CoreError("Подтвердите адрес и окно доставки с покупателем до отправки.", code="need_address")
        for it in items:
            inventory.ship(c, it["product_id"], it["qty"])
    if status == "cancelled":
        for it in items:
            inventory.release(c, it["product_id"], it["qty"])
        c.execute("UPDATE orders SET cancel_reason = ? WHERE id = ?", (reason or "other", order_id))
    if status == "delivered":
        c.execute("UPDATE orders SET delivered_at = ? WHERE id = ?", (db.now_iso(), order_id))
    if status == "closed":
        if c.execute("SELECT 1 FROM claims WHERE order_id = ? AND status != 'closed'", (order_id,)).fetchone():
            raise CoreError("Есть незакрытая претензия — заказ закрыть нельзя", status=409)

    c.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))
    events.log(c, f"status_{status}", actor, order_id=order_id, payload={"from": o["status"], "reason": reason})


def confirm_address(c: sqlite3.Connection, order_id: int, address: str, zone: str, window: str, actor: str) -> None:
    if not (address or "").strip() or not (window or "").strip():
        raise CoreError("Нужны адрес и окно доставки")
    c.execute(
        "UPDATE orders SET buyer_address = ?, delivery_zone = ?, delivery_window = ?, address_confirmed = 1 WHERE id = ?",
        (address.strip(), zone, window.strip(), order_id),
    )
    events.log(c, "address_confirmed", actor, order_id=order_id, payload={"zone": zone, "window": window})


def record_clarification(c: sqlite3.Connection, order_id: int, question: str, answer: str, actor: str) -> None:
    if not (answer or "").strip():
        raise CoreError("Нужен ответ покупателя")
    c.execute("UPDATE orders SET clarification = ? WHERE id = ?", (f"{question.strip()} → {answer.strip()}", order_id))
    events.log(c, "clarification_recorded", actor, order_id=order_id, payload={"question": question, "answer": answer})


def list_orders(c: sqlite3.Connection, status: str | None = None, channel: str | None = None) -> list[dict]:
    q = ("SELECT o.*, (SELECT COUNT(*) FROM claims cl WHERE cl.order_id = o.id AND cl.status != 'closed') AS open_claims "
         "FROM orders o WHERE 1=1")
    args: list = []
    if status:
        q += " AND o.status = ?"
        args.append(status)
    if channel:
        q += " AND o.channel = ?"
        args.append(channel)
    q += " ORDER BY o.id DESC"
    out = db.rows(c.execute(q, args))
    for o in out:
        o["channel_label"] = CHANNELS.get(o["channel"], o["channel"])
        o["status_label"] = STATUSES.get(o["status"], o["status"])
    return out


def delivered_date(o: dict):
    return datetime.fromisoformat(o["delivered_at"]).date() if o.get("delivered_at") else None


# --- Очередь входящих событий (коннекторы -> очередь -> ядро) ---

def enqueue(c: sqlite3.Connection, source: str, idem_key: str, payload: dict) -> bool:
    cur = c.execute(
        "INSERT OR IGNORE INTO inbox(idem_key, source, payload, created_at) VALUES (?, ?, ?, ?)",
        (idem_key, source, db.dumps(payload), db.now_iso()),
    )
    return cur.rowcount == 1


def process_inbox(actor: str = "system") -> list[dict]:
    """Обрабатывает необработанные события. Каждое — в своей транзакции."""
    results = []
    with db.read() as c:
        pending = db.rows(c.execute("SELECT * FROM inbox WHERE status = 'pending' ORDER BY id"))
    for item in pending:
        payload = db.loads(item["payload"], {})
        try:
            with db.tx() as c:
                if c.execute("SELECT status FROM inbox WHERE id = ?", (item["id"],)).fetchone()[0] != "pending":
                    continue  # уже обработано параллельным вызовом
                oid = create(c, payload, actor)
                c.execute("UPDATE inbox SET status = 'done', result_order_id = ? WHERE id = ?", (oid, item["id"]))
            results.append({"idem_key": item["idem_key"], "status": "done", "order_id": oid})
        except CoreError as e:
            status = "rejected_no_stock" if isinstance(e, OutOfStock) else "rejected"
            with db.tx() as c:
                c.execute("UPDATE inbox SET status = ?, error = ? WHERE id = ?", (status, e.message, item["id"]))
                events.log(c, "order_rejected", actor, payload={"idem_key": item["idem_key"], "reason": e.message,
                                                                 "code": e.code, "channel": payload.get("channel")})
            results.append({"idem_key": item["idem_key"], "status": status, "error": e.message})
    return results
