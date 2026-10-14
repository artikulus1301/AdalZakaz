"""Журнал событий. Каждое событие может нести ключ идемпотентности:
повтор той же команды не создаёт второе событие (и второй возврат или платёж)."""
import sqlite3

from .. import db


def log(
    c: sqlite3.Connection,
    type: str,
    actor: str,
    order_id: int | None = None,
    claim_id: int | None = None,
    payload: dict | None = None,
    idem_key: str | None = None,
) -> bool:
    """Записывает событие. Возвращает False, если событие с таким ключом уже было."""
    cur = c.execute(
        "INSERT OR IGNORE INTO events(order_id, claim_id, idem_key, type, payload, actor, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (order_id, claim_id, idem_key, type, db.dumps(payload or {}), actor, db.now_iso()),
    )
    return cur.rowcount == 1


def seen(c: sqlite3.Connection, idem_key: str) -> bool:
    return c.execute("SELECT 1 FROM events WHERE idem_key = ?", (idem_key,)).fetchone() is not None


def for_order(c: sqlite3.Connection, order_id: int) -> list[dict]:
    out = db.rows(
        c.execute(
            "SELECT * FROM events WHERE order_id = ? OR claim_id IN "
            "(SELECT id FROM claims WHERE order_id = ?) ORDER BY id",
            (order_id, order_id),
        )
    )
    for e in out:
        e["payload"] = db.loads(e["payload"], {})
    return out
