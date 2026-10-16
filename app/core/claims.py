"""Претензии: одна очередь, счётчик 10 календарных дней, решение — только человек."""
import sqlite3
from datetime import date, datetime, timedelta

from .. import db
from . import CoreError, NotFound, events, inventory, orders, rules

OPEN_STATUSES = ("open", "decided")


def create(
    c: sqlite3.Connection,
    order_id: int,
    text: str,
    actor: str,
    buyer_type: str | None = None,
    lang: str = "ru",
    voice_transcript: str | None = None,
    source: str = "buyer_page",
    idem_key: str | None = None,
) -> int:
    o = c.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not o:
        raise NotFound("Заказ")
    if o["status"] in ("new", "paid", "assembled", "cancelled"):
        raise CoreError("Претензию можно подать после передачи товара в доставку", status=409)
    if not (text or "").strip() and not (voice_transcript or "").strip():
        raise CoreError("Опишите проблему текстом или голосом")
    if idem_key:
        r = c.execute("SELECT claim_id FROM events WHERE idem_key = ?", (idem_key,)).fetchone()
        if r:
            return r["claim_id"]
    r = rules.active(c)
    received = db.now()
    deadline = received.date() + timedelta(days=r["data"]["claim_response_days"])
    items = c.execute("SELECT id,qty FROM order_items WHERE order_id=?", (order_id,)).fetchall()
    single = items[0] if len(items) == 1 else None
    cur = c.execute(
        "INSERT INTO claims(order_id, text, voice_transcript, lang, buyer_type, status, received_at, deadline, source, order_item_id, item_qty) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (order_id, (text or "").strip(), voice_transcript, lang, buyer_type if buyer_type in rules.CLAIM_TYPES else None,
         "open", received.isoformat(), deadline.isoformat(), source, single["id"] if single else None, single["qty"] if single else None),
    )
    cid = cur.lastrowid
    c.execute("UPDATE claims SET number = ? WHERE id = ?", (f"CL-{cid:06d}", cid))
    events.log(c, "claim_received", actor, order_id=order_id, claim_id=cid,
               payload={"deadline": deadline.isoformat(), "source": source}, idem_key=idem_key)
    return cid


def deadline_info(claim: dict, today: date | None = None) -> dict:
    today = today or db.now().date()
    dl = date.fromisoformat(claim["deadline"])
    days_left = (dl - today).days
    answered = claim["status"] != "open"
    on_time = None
    if claim.get("decided_at"):
        on_time = datetime.fromisoformat(claim["decided_at"]).date() <= dl
    return {
        "deadline": claim["deadline"],
        "days_left": days_left,
        "overdue": (not answered) and days_left < 0,
        "urgent": (not answered) and 0 <= days_left <= 2,
        "answered_on_time": on_time,
    }


def get(c: sqlite3.Connection, claim_id: int) -> dict:
    cl = db.row(c.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone())
    if not cl:
        raise NotFound("Претензия")
    cl["timer"] = deadline_info(cl)
    cl["type_label"] = rules.CLAIM_TYPES.get(cl["type"] or "", ("—",))[0]
    cl["ai_type_label"] = rules.CLAIM_TYPES.get(cl["ai_type"] or "", ("—",))[0]
    cl["decision_label"] = rules.DECISIONS.get(cl["decision"] or "", None)
    cl["refund"] = db.row(c.execute("SELECT * FROM refunds WHERE claim_id = ?", (claim_id,)).fetchone())
    cl["item"] = db.row(c.execute(
        "SELECT oi.*,p.category,p.sku FROM order_items oi JOIN products p ON p.id=oi.product_id "
        "WHERE oi.id=? AND oi.order_id=?", (cl["order_item_id"], cl["order_id"])).fetchone())
    cl["item_total"] = cl["item"]["price"] * cl["item_qty"] if cl["item"] else None
    cl["return_variants"] = inventory.variants(c, cl["item"]["product_id"]) if cl["item"] else []
    if cl["item"]:
        refunded = c.execute("SELECT COALESCE(SUM(r.amount),0) FROM refunds r JOIN claims cl ON cl.id=r.claim_id "
                             "WHERE cl.order_item_id=?", (cl["order_item_id"],)).fetchone()[0]
        cl["refund_limit"] = max(0, min(cl["item_total"], cl["item"]["price"] * cl["item"]["qty"] - refunded))
    else:
        cl["refund_limit"] = 0
    return cl


def evaluation(c: sqlite3.Connection, claim_id: int) -> dict:
    cl = get(c, claim_id)
    o = orders.get(c, cl["order_id"])
    item = cl["item"]
    result = rules.evaluate(
        rules.active(c),
        cl["type"] if item else None,
        item["category"] if item else "other",
        o["channel"],
        orders.delivered_date(o),
        datetime.fromisoformat(cl["received_at"]).date(),
        None if cl["condition_ok"] is None else bool(cl["condition_ok"]),
    )
    if not item:
        result["warnings"].append("Выберите позицию заказа и количество. Для разных позиций оформите отдельные претензии.")
    return result


def set_ai_result(c: sqlite3.Connection, claim_id: int, ai_type: str | None, summary: str, reason_tag: str) -> None:
    if ai_type not in rules.CLAIM_TYPES:
        ai_type = None
    c.execute("UPDATE claims SET ai_type = ?, summary = ?, reason_tag = ? WHERE id = ?",
              (ai_type, summary, reason_tag, claim_id))


def confirm(c: sqlite3.Connection, claim_id: int, claim_type: str, condition_ok: bool | None,
            reason_tag: str | None, actor: str, order_item_id: int | None = None, item_qty: int | None = None) -> None:
    if claim_type not in rules.CLAIM_TYPES:
        raise CoreError("Неизвестный тип претензии")
    cl = get(c, claim_id)
    if cl["status"] != "open":
        raise CoreError("По претензии уже принято решение", status=409)
    item_id = order_item_id if order_item_id is not None else cl["order_item_id"]
    item = c.execute("SELECT * FROM order_items WHERE id=? AND order_id=?", (item_id, cl["order_id"])).fetchone()
    if not item:
        raise CoreError("Выберите позицию этого заказа", code="claim_item_required")
    qty = item_qty if item_qty is not None else (cl["item_qty"] if item_id == cl["order_item_id"] else item["qty"])
    if type(qty) is not int or not 0 < qty <= item["qty"]:
        raise CoreError("Количество в претензии должно быть от 1 до количества в выбранной позиции")
    c.execute(
        "UPDATE claims SET type = ?, condition_ok = ?, reason_tag = COALESCE(?, reason_tag), order_item_id=?, item_qty=? WHERE id = ?",
        (claim_type, None if condition_ok is None else int(condition_ok), reason_tag or None, item_id, qty, claim_id),
    )
    events.log(c, "claim_type_confirmed", actor, order_id=cl["order_id"], claim_id=claim_id,
               payload={"type": claim_type, "condition_ok": condition_ok, "ai_type": cl["ai_type"], "order_item_id": item_id, "qty": qty})


def decide(c: sqlite3.Connection, claim_id: int, decision: str, text: str, actor: str,
           refund_amount: int | None = None, return_to_stock: bool = False, return_variant_id: int | None = None) -> dict:
    """Решение человека. Возврат денег регистрируется один раз (идемпотентно)."""
    cl = get(c, claim_id)
    if cl["status"] != "open":
        if cl["decision"] == decision:
            return {"repeated": True}
        raise CoreError("По претензии уже принято другое решение", status=409)
    if not cl["type"]:
        raise CoreError("Сначала подтвердите тип претензии (предложение ИИ — только подсказка)")
    it = cl["item"]
    if not it or not cl["item_qty"]:
        raise CoreError("Сначала подтвердите позицию и количество в претензии", code="claim_item_required")
    ev = evaluation(c, claim_id)
    basis = rules.validate_decision(ev, decision, text)
    o = orders.get(c, cl["order_id"])
    amount = None
    if decision in ("refund", "price_reduction"):
        amount = refund_amount if refund_amount is not None else cl["item_total"]
        refunded_order = c.execute("SELECT COALESCE(SUM(r.amount),0) FROM refunds r JOIN claims cl ON cl.id=r.claim_id "
                                   "WHERE cl.order_id=?", (o["id"],)).fetchone()[0]
        refunded_item = c.execute("SELECT COALESCE(SUM(r.amount),0) FROM refunds r JOIN claims cl ON cl.id=r.claim_id "
                                  "WHERE cl.order_item_id=?", (it["id"],)).fetchone()[0]
        unscoped = c.execute("SELECT 1 FROM refunds r JOIN claims cl ON cl.id=r.claim_id "
                             "WHERE cl.order_id=? AND cl.order_item_id IS NULL", (o["id"],)).fetchone()
        if unscoped:
            raise CoreError("У заказа есть старый возврат без указания позиции. Сначала необходимо сверить его вручную.", status=409)
        limit = min(cl["item_total"], it["qty"] * it["price"] - refunded_item, o["total"] - refunded_order)
        if type(amount) is not int or not 0 < amount <= limit:
            raise CoreError(f"Сумма возврата должна быть больше нуля и не больше {max(0, limit)} тг для выбранной позиции")
    variant_id = it["variant_id"]
    if return_to_stock:
        if decision not in ("refund", "exchange", "replace"):
            raise CoreError("Возврат на склад доступен при возврате денег, обмене или замене")
        returned = c.execute("SELECT COALESCE(SUM(qty),0) FROM stock_returns WHERE order_item_id=?", (it["id"],)).fetchone()[0]
        if cl["item_qty"] > it["qty"] - returned:
            raise CoreError("Это количество товара уже возвращено на склад по другой претензии", status=409)
        tracked = cl["return_variants"]
        if variant_id is None and tracked:
            match = next((v for v in tracked if v["label"].casefold() == (it["variant"] or "").casefold()), None)
            variant_id = match["id"] if match else return_variant_id
        if return_variant_id is not None and return_variant_id != variant_id:
            raise CoreError("Нельзя вернуть товар в другой вариант")
        if tracked and variant_id is None:
            raise CoreError("Выберите размер / цвет возвращаемого товара")
        if variant_id is not None and not any(v["id"] == variant_id for v in tracked):
            raise CoreError("Вариант возврата не принадлежит товару")
    r = rules.active(c)
    c.execute(
        "UPDATE claims SET status = 'decided', decision = ?, decision_text = ?, rule_basis = ?, rules_version = ?, "
        "decided_by = ?, decided_at = ? WHERE id = ?",
        (decision, (text or "").strip(), basis, r["version"], actor, db.now_iso(), claim_id),
    )
    events.log(c, "claim_decided", actor, order_id=cl["order_id"], claim_id=claim_id,
               payload={"decision": decision, "rules_version": r["version"], "order_item_id": it["id"], "qty": cl["item_qty"]})
    if amount is not None:
        idem = f"refund:{claim_id}"
        if events.log(c, "refund_registered", actor, order_id=cl["order_id"], claim_id=claim_id,
                      payload={"amount": amount}, idem_key=idem):
            c.execute("INSERT INTO refunds(claim_id, amount, idem_key, actor, created_at) VALUES (?,?,?,?,?)",
                      (claim_id, amount, idem, actor, db.now_iso()))
    if return_to_stock:
        inventory.restock(c, it["product_id"], cl["item_qty"], variant_id)
        c.execute("INSERT INTO stock_returns(claim_id,order_item_id,qty) VALUES (?,?,?)", (claim_id, it["id"], cl["item_qty"]))
        events.log(c, "returned_to_stock", actor, order_id=cl["order_id"], claim_id=claim_id,
                   payload={"order_item_id": it["id"], "qty": cl["item_qty"], "variant_id": variant_id})
    return {"repeated": False}


def close(c: sqlite3.Connection, claim_id: int, actor: str) -> None:
    cl = get(c, claim_id)
    if cl["status"] != "decided":
        raise CoreError("Закрыть можно только претензию с решением", status=409)
    c.execute("UPDATE claims SET status = 'closed' WHERE id = ?", (claim_id,))
    events.log(c, "claim_closed", actor, order_id=cl["order_id"], claim_id=claim_id)


def list_claims(c: sqlite3.Connection, status: str | None = None) -> list[dict]:
    q = "SELECT cl.*, o.number AS order_number, o.channel FROM claims cl JOIN orders o ON o.id = cl.order_id"
    args = []
    if status == "active":
        q += " WHERE cl.status = 'open'"
    elif status:
        q += " WHERE cl.status = ?"
        args.append(status)
    q += " ORDER BY CASE cl.status WHEN 'open' THEN 0 ELSE 1 END, cl.deadline, cl.id"
    out = db.rows(c.execute(q, args))
    for cl in out:
        cl["timer"] = deadline_info(cl)
        cl["type_label"] = rules.CLAIM_TYPES.get(cl["type"] or "", ("—",))[0]
        cl["ai_type_label"] = rules.CLAIM_TYPES.get(cl["ai_type"] or "", ("—",))[0]
    return out
