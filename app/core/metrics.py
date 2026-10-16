"""Метрики пилота. Считаются из фактов ядра, без ИИ."""
import math
import sqlite3
from datetime import datetime

from .. import db
from . import claims as claims_mod


def _p90(values: list[float]) -> float | None:
    if not values:
        return None
    v = sorted(values)
    return v[max(0, math.ceil(0.9 * len(v)) - 1)]


def compute(c: sqlite3.Connection) -> dict:
    orders_total = c.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    rejected_no_stock = c.execute("SELECT COUNT(*) FROM inbox WHERE status = 'rejected_no_stock'").fetchone()[0]
    cancelled_no_stock = c.execute(
        "SELECT COUNT(*) FROM orders WHERE status = 'cancelled' AND cancel_reason = 'no_stock'").fetchone()[0]
    attempts = orders_total + rejected_no_stock
    delivered = c.execute(
        "SELECT COUNT(*) FROM orders WHERE delivered_at IS NOT NULL").fetchone()[0]

    all_claims = claims_mod.list_claims(c)
    by_type: dict[str, int] = {}
    for cl in all_claims:
        t = cl["type"] or cl["ai_type"] or "unknown"
        by_type[t] = by_type.get(t, 0) + 1
    decided = [cl for cl in all_claims if cl["decided_at"]]
    on_time = [cl for cl in decided if cl["timer"]["answered_on_time"]]
    overdue_open = [cl for cl in all_claims if cl["timer"]["overdue"]]
    durations = [
        (datetime.fromisoformat(cl["decided_at"]) - datetime.fromisoformat(cl["received_at"])).total_seconds() / 86400
        for cl in decided
    ]

    delivery_problem_orders = c.execute(
        "SELECT COUNT(DISTINCT order_id) FROM claims WHERE COALESCE(type, ai_type) = 'delivery'").fetchone()[0]

    # P90 первого содержательного ответа: от первого входящего до первого исходящего сообщения
    resp = []
    for r in c.execute(
        "SELECT order_id, MIN(CASE WHEN direction='in' THEN created_at END) fi, "
        "MIN(CASE WHEN direction='out' THEN created_at END) fo FROM messages GROUP BY order_id"
    ):
        if r["fi"] and r["fo"] and r["fo"] >= r["fi"]:
            resp.append((datetime.fromisoformat(r["fo"]) - datetime.fromisoformat(r["fi"])).total_seconds() / 60)

    drafts = db.rows(c.execute("SELECT status, text_original, text_final, mode FROM ai_drafts WHERE text_original IS NOT NULL"))
    reviewed = [d for d in drafts if d["status"] in ("approved", "sent", "rejected")]
    edited = [d for d in reviewed if d["status"] == "rejected" or (d["text_final"] or "") != (d["text_original"] or "")]

    orders_value = c.execute("SELECT COALESCE(SUM(total),0) FROM orders WHERE status != 'cancelled'").fetchone()[0]
    payments_received = c.execute("SELECT COALESCE(SUM(total),0) FROM orders WHERE payment_status='paid'").fetchone()[0]
    unpaid_orders_value = c.execute("SELECT COALESCE(SUM(total),0) FROM orders WHERE payment_status!='paid' AND status!='cancelled'").fetchone()[0]
    refunds = c.execute("SELECT COALESCE(SUM(amount),0) FROM refunds").fetchone()[0]

    def pct(a, b):
        return round(100 * a / b, 1) if b else None

    return {
        "orders_total": orders_total,
        "delivered": delivered,
        "no_stock_cancellations": rejected_no_stock + cancelled_no_stock,
        "no_stock_rate": pct(rejected_no_stock + cancelled_no_stock, attempts),
        "otif_approx": pct(delivered - delivery_problem_orders, delivered),
        "first_response_p90_min": round(_p90(resp), 1) if resp else None,
        "claims_total": len(all_claims),
        "claims_by_type": by_type,
        "not_as_described_rate": pct(by_type.get("not_as_described", 0), delivered),
        "claims_on_time_rate": pct(len(on_time), len(decided)),
        "claims_overdue_open": len(overdue_open),
        "claim_resolution_avg_days": round(sum(durations) / len(durations), 1) if durations else None,
        "ai_drafts_reviewed": len(reviewed),
        "ai_drafts_edited_rate": pct(len(edited), len(reviewed)),
        "orders_value": orders_value,
        "payments_received": payments_received,
        "unpaid_orders_value": unpaid_orders_value,
        "revenue": payments_received - refunds,
        "refunds": refunds,
    }
