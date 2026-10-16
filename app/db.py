"""SQLite-хранилище ядра. Факты (цена, остаток, оплата, сроки) живут только здесь."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    sku TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL,
    current_version INTEGER NOT NULL DEFAULT 0,
    stock INTEGER NOT NULL DEFAULT 0 CHECK (stock >= 0),
    reserved INTEGER NOT NULL DEFAULT 0 CHECK (reserved >= 0),
    created_at TEXT NOT NULL,
    CHECK (reserved <= stock)
);

CREATE TABLE IF NOT EXISTS passport_versions (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id),
    version INTEGER NOT NULL,
    data TEXT NOT NULL,
    check_result TEXT,
    note TEXT,
    author TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (product_id, version)
);

CREATE TABLE IF NOT EXISTS product_variants (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id),
    label TEXT NOT NULL,
    stock INTEGER NOT NULL CHECK (stock >= 0),
    reserved INTEGER NOT NULL DEFAULT 0 CHECK (reserved >= 0 AND reserved <= stock),
    UNIQUE(product_id, label)
);

CREATE TABLE IF NOT EXISTS inbox (
    id INTEGER PRIMARY KEY,
    idem_key TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT,
    result_order_id INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY,
    number TEXT UNIQUE,
    channel TEXT NOT NULL,
    external_id TEXT,
    buyer_name TEXT,
    buyer_phone TEXT,
    buyer_address TEXT,
    buyer_lang TEXT NOT NULL DEFAULT 'ru',
    status TEXT NOT NULL,
    payment_status TEXT NOT NULL DEFAULT 'unpaid',
    payment_source TEXT,
    payment_ref TEXT UNIQUE,
    total INTEGER NOT NULL DEFAULT 0,
    public_token TEXT UNIQUE,
    delivery_zone TEXT,
    delivery_window TEXT,
    address_confirmed INTEGER NOT NULL DEFAULT 0,
    clarification TEXT,
    created_at TEXT NOT NULL,
    delivered_at TEXT,
    cancel_reason TEXT,
    UNIQUE (channel, external_id)
);

CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    passport_version INTEGER NOT NULL,
    qty INTEGER NOT NULL CHECK (qty > 0),
    price INTEGER NOT NULL,
    variant TEXT,
    variant_id INTEGER REFERENCES product_variants(id)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    order_id INTEGER REFERENCES orders(id),
    claim_id INTEGER,
    idem_key TEXT UNIQUE,
    type TEXT NOT NULL,
    payload TEXT,
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence (
    id INTEGER PRIMARY KEY,
    order_id INTEGER REFERENCES orders(id),
    claim_id INTEGER,
    product_id INTEGER,
    kind TEXT NOT NULL,
    filename TEXT,
    original_name TEXT,
    sha256 TEXT,
    mime TEXT,
    size INTEGER,
    note TEXT,
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    direction TEXT NOT NULL,
    text TEXT NOT NULL,
    lang TEXT,
    ai_draft_id INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY,
    number TEXT UNIQUE,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    text TEXT,
    voice_transcript TEXT,
    lang TEXT,
    buyer_type TEXT,
    ai_type TEXT,
    type TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    received_at TEXT NOT NULL,
    deadline TEXT NOT NULL,
    summary TEXT,
    reason_tag TEXT,
    condition_ok INTEGER,
    decision TEXT,
    decision_text TEXT,
    rule_basis TEXT,
    rules_version INTEGER,
    decided_by TEXT,
    decided_at TEXT,
    source TEXT NOT NULL DEFAULT 'buyer_page',
    order_item_id INTEGER REFERENCES order_items(id),
    item_qty INTEGER CHECK (item_qty > 0)
);

CREATE TABLE IF NOT EXISTS stock_returns (
    claim_id INTEGER NOT NULL REFERENCES claims(id),
    order_item_id INTEGER NOT NULL REFERENCES order_items(id),
    qty INTEGER NOT NULL CHECK (qty > 0),
    PRIMARY KEY(claim_id, order_item_id)
);

CREATE TABLE IF NOT EXISTS refunds (
    id INTEGER PRIMARY KEY,
    claim_id INTEGER NOT NULL UNIQUE REFERENCES claims(id),
    amount INTEGER NOT NULL,
    idem_key TEXT NOT NULL UNIQUE,
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_drafts (
    id INTEGER PRIMARY KEY,
    task TEXT NOT NULL,
    order_id INTEGER,
    claim_id INTEGER,
    product_id INTEGER,
    output TEXT NOT NULL,
    text_original TEXT,
    text_final TEXT,
    guard TEXT,
    status TEXT NOT NULL,
    mode TEXT NOT NULL,
    model TEXT,
    created_at TEXT NOT NULL,
    reviewed_at TEXT
);

CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY,
    version INTEGER NOT NULL UNIQUE,
    data TEXT NOT NULL,
    approved_by TEXT,
    approved_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS copilot_messages (
    id INTEGER PRIMARY KEY,
    chat_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    text TEXT NOT NULL,
    meta TEXT,
    plan_id INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS copilot_messages_chat ON copilot_messages(chat_id, id);

CREATE TABLE IF NOT EXISTS copilot_plans (
    id INTEGER PRIMARY KEY,
    chat_id TEXT NOT NULL,
    request TEXT NOT NULL,
    steps TEXT NOT NULL,
    status TEXT NOT NULL,
    model TEXT,
    created_at TEXT NOT NULL,
    decided_by TEXT,
    decided_at TEXT,
    finished_at TEXT
);
"""


def now() -> datetime:
    return datetime.now(config.TZ).replace(microsecond=0)


def now_iso() -> str:
    return now().isoformat()


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def tx(immediate: bool = True):
    """Одна транзакция: либо всё, либо ничего.
    immediate=False — блокировка на запись берётся только при первой записи
    (для запросов к ИИ: долгий вызов модели не держит базу)."""
    conn = connect()
    try:
        if immediate:
            conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def read():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def init() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        # Additive migration: keep existing orders and their original stock facts.
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("PRAGMA user_version").fetchone()[0] < 1:
            for table, column, definition in (
                ("order_items", "variant_id", "INTEGER REFERENCES product_variants(id)"),
                ("claims", "order_item_id", "INTEGER REFERENCES order_items(id)"),
                ("claims", "item_qty", "INTEGER CHECK (item_qty > 0)"),
            ):
                if column not in {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            conn.execute(
                "UPDATE claims SET order_item_id = (SELECT MIN(id) FROM order_items WHERE order_id=claims.order_id), "
                "item_qty = (SELECT qty FROM order_items WHERE order_id=claims.order_id) "
                "WHERE (SELECT COUNT(*) FROM order_items WHERE order_id=claims.order_id)=1")
            # The previous implementation restocked every line: record what it actually did.
            conn.execute(
                "INSERT OR IGNORE INTO stock_returns(claim_id, order_item_id, qty) "
                "SELECT e.claim_id, oi.id, oi.qty FROM events e "
                "JOIN claims cl ON cl.id=e.claim_id JOIN order_items oi ON oi.order_id=cl.order_id "
                "WHERE e.type='returned_to_stock'")
            conn.execute("PRAGMA user_version = 1")
        conn.commit()
    finally:
        conn.close()


def row(r: sqlite3.Row | None) -> dict | None:
    return dict(r) if r is not None else None


def rows(rs) -> list[dict]:
    return [dict(r) for r in rs]


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def loads(s, default=None):
    if s is None:
        return default
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return default


def get_setting(c: sqlite3.Connection, key: str, default=None):
    r = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return loads(r["value"], default) if r else default


def set_setting(c: sqlite3.Connection, key: str, value) -> None:
    c.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, dumps(value)),
    )
