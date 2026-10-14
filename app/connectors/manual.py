"""Ручной / полуавтоматический коннектор для OLX, Instagram, WhatsApp, Telegram и страницы товара.
Продавец (или покупатель со страницы-паспорта) вводит заказ — он идёт через ту же очередь, что и Kaspi."""
import uuid

from .. import db
from ..core import CoreError, orders


def submit(payload: dict, actor: str, idem_key: str | None = None) -> dict:
    if payload.get("channel") == "kaspi":
        raise CoreError("Заказы Kaspi приходят только через API Kaspi")
    key = f"manual:{idem_key or uuid.uuid4().hex}"
    with db.tx() as c:
        fresh = orders.enqueue(c, payload.get("channel", "manual"), key, payload)
    results = orders.process_inbox(actor)
    if not fresh:
        with db.read() as c:
            r = c.execute("SELECT status, error, result_order_id FROM inbox WHERE idem_key = ?", (key,)).fetchone()
        return {"status": r["status"], "order_id": r["result_order_id"], "error": r["error"], "repeated": True}
    mine = next((r for r in results if r["idem_key"] == key), None)
    if mine is None:
        with db.read() as c:
            r = c.execute("SELECT status, error, result_order_id FROM inbox WHERE idem_key = ?", (key,)).fetchone()
        mine = {"status": r["status"], "order_id": r["result_order_id"], "error": r["error"]}
    return mine
