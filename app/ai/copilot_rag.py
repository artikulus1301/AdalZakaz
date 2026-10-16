"""RAG для Adal Copilot: найти факты в ядре до обращения к модели.

Поиск в два слоя:
1. Точные выборки из SQLite по фильтрам запроса (период, статус, канал, оплата, номера).
   Отсюда берутся количества и суммы — модель их не считает и не угадывает.
2. Лексический поиск BM25 по базе знаний: заказы, товары, претензии, правила и справка по приложению.

Модель получает только найденные фрагменты. Имена, телефоны и адреса покупателей в контекст не попадают."""
import math
import re
import sqlite3
from collections import Counter
from datetime import timedelta

from .. import db
from ..core import claims as claims_mod
from ..core import metrics, orders, passport, rules
from .redact import redact

PERIODS = {"today": "сегодня", "yesterday": "вчера", "week": "за 7 дней", "month": "за 30 дней", "all": "за всё время"}
TOPICS = ("overview", "orders", "claims", "products", "metrics", "help")
CLAIM_FILTERS = {"open": "ждут решения", "urgent": "срок истекает (≤ 2 дней)", "overdue": "просрочены",
                 "decided": "решение принято", "closed": "закрыты"}
CLAIM_STATUS_LABELS = {"open": "ждёт решения", "decided": "решение принято, не закрыта", "closed": "закрыта"}
ACTIVE_STATUSES = ["new", "paid", "assembled", "shipped"]
LOW_STOCK = 2
MAX_ORDERS = 40
MAX_CLAIMS = 30
MAX_PRODUCTS = 30


def norm(text: str | None) -> str:
    return (text or "").lower().replace("ё", "е")


# ---------- разбор запроса без модели ----------

_PERIOD_RX = [("today", r"сегодн|бүгін|за день"), ("yesterday", r"вчера|кеше"),
              ("week", r"недел|апта|7 дн"), ("month", r"месяц|30 дн|осы ай")]
_STATUS_RX = {
    "new": r"\bнов(ый|ые|ых|ое|ая|ым|ыми)\b|\bжаңа",
    "assembled": r"собранн|\bсобран(ы)?\b|жиналған",
    "shipped": r"отправленн|\bотправлен(ы)?\b|в доставке|в пути|передан\w* в доставку|жолда|жіберілген",
    "delivered": r"выданн|\bвыдан(ы)?\b|доставленн|\bдоставлен(ы)?\b|\bполучен|врученн|жеткізілген|тапсырылған",
    "closed": r"закрыт(ые|ых|ый)?\s+заказ|заказ\w*\s+закрыт|жабылған",
    "cancelled": r"отмен[её]нн|\bотмен[её]н(ы)?\b|бас тартылған",
}
_ACTIVE_RX = r"активн|в работе|незавершенн|белсенді"
_UNPAID_RX = r"не ?оплач|без оплаты|не оплатил|төленбеген|төлем жоқ|ожида\w* оплат|ждут оплат"
_PAID_RX = r"оплаченн|\bоплачен(ы)?\b|төленген"
_CHANNEL_RX = {"kaspi": r"kaspi|каспи", "olx": r"\bolx|\bолх", "instagram": r"insta|инста", "whatsapp": r"whats ?app|ват?сап|вотсап|уатсап",
               "telegram": r"telegram|телеграм|телег", "site": r"\bсайт|\bsite\b"}
_TOPIC_RX = [
    ("overview", r"важн|срочн\w* дел|главн|сводк|обзор|приоритет|с чего начать|что делать сегодня|маңызды"),
    ("claims", r"претенз|жалоб|шағым|\bспор"),
    ("products", r"товар|остат|склад|наличи|паспорт|карточк|тауар|қалдық|қойма"),
    ("metrics", r"выручк|доход|деньг|метрик|аналитик|статистик|табыс|оборот|сколько заработ"),
    ("help", r"\bкак\b|почему|зачем|что делать|можно ли|қалай|неге|правил|закон"),
    ("orders", r"заказ|тапсырыс|продаж"),
]
_CLAIM_FILTER_RX = [("overdue", r"просроч|мерзімі өткен"), ("urgent", r"горят|срочн|истека|скоро срок"),
                    ("closed", r"закрыт\w* претенз|претенз\w* закрыт"), ("decided", r"с решением|решенн"),
                    ("open", r"открыт|без решения|ждут решения|не решен")]
_LOW_STOCK_RX = r"заканчива|кончил|мало\b|низк\w* остат|нет в наличии|аз қалды|бітіп"
_REFERS_RX = r"\b(эти|этих|этим|их|них|они|им|те же|все эти|оларды|бұларды|осыларды|соларды)\b"
ORDER_NUM_RX = re.compile(r"\b(?:az|аз)[\s\-]?0*(\d{1,6})\b|(?:заказ\w*|тапсырыс\w*)\s*(?:№|#|номер)?\s*0*(\d{1,6})\b", re.I)
CLAIM_NUM_RX = re.compile(r"\bcl[\s\-]?0*(\d{1,6})\b|(?:претензи\w*|шағым\w*)\s*(?:№|#|номер)?\s*0*(\d{1,6})\b", re.I)


def order_number(n: int) -> str:
    return f"AZ-{n:06d}"


def claim_number(n: int) -> str:
    return f"CL-{n:06d}"


def empty_query(text: str) -> dict:
    return {"text": text, "topics": [], "period": "all", "statuses": [], "channels": [], "payment": None,
            "claim_filter": None, "orders": [], "claims": [], "skus": [], "low_stock": False,
            "refers_previous": False, "search": text}


def parse_query(text: str, skus: list[str]) -> dict:
    """Локальный разбор: фильтры и номера из текста продавца (ru/kk)."""
    q = empty_query(text)
    low = norm(text)
    q["topics"] = [t for t, rx in _TOPIC_RX if re.search(rx, low)]
    q["period"] = next((p for p, rx in _PERIOD_RX if re.search(rx, low)), "all")
    q["statuses"] = [s for s, rx in _STATUS_RX.items() if re.search(rx, low)]
    if not q["statuses"] and re.search(_ACTIVE_RX, low):
        q["statuses"] = list(ACTIVE_STATUSES)
    if re.search(_UNPAID_RX, low):
        q["payment"] = "unpaid"
    elif re.search(_PAID_RX, low):
        q["payment"] = "paid"
    q["channels"] = [ch for ch, rx in _CHANNEL_RX.items() if re.search(rx, low)]
    q["claim_filter"] = next((f for f, rx in _CLAIM_FILTER_RX if re.search(rx, low)), None)
    q["orders"] = list(dict.fromkeys(order_number(int(a or b)) for a, b in ORDER_NUM_RX.findall(text)))
    q["claims"] = list(dict.fromkeys(claim_number(int(a or b)) for a, b in CLAIM_NUM_RX.findall(text)))
    q["skus"] = [s for s in skus if re.search(r"(?<![\w-])" + re.escape(s.lower()) + r"(?![\w-])", low)]
    q["low_stock"] = bool(re.search(_LOW_STOCK_RX, low))
    q["refers_previous"] = bool(re.search(_REFERS_RX, low))
    return q


def coerce_query(raw: dict, base: dict, skus: list[str]) -> dict:
    """Проверяет разбор модели и объединяет его с локальным. Неизвестные значения отбрасываются."""
    if not isinstance(raw, dict):
        return base
    q = dict(base)
    lists = lambda key: [str(x) for x in raw.get(key) or [] if isinstance(x, (str, int))]  # noqa: E731
    topics = [t for t in lists("topics") if t in TOPICS]
    q["topics"] = list(dict.fromkeys(topics + base["topics"]))
    if base["period"] == "all" and isinstance(raw.get("period"), str) and raw["period"] in PERIODS:
        q["period"] = raw["period"]
    statuses = [s for s in lists("statuses") if s in orders.STATUSES]
    if statuses or isinstance(raw.get("statuses"), list):
        q["statuses"] = statuses
    if isinstance(raw.get("channels"), list):
        q["channels"] = [ch for ch in lists("channels") if ch in orders.CHANNELS]
    if raw.get("payment") in ("paid", "unpaid", None):
        q["payment"] = raw.get("payment")
    if raw.get("claim_filter") is None or (isinstance(raw["claim_filter"], str) and raw["claim_filter"] in CLAIM_FILTERS):
        q["claim_filter"] = raw.get("claim_filter") or base["claim_filter"]
    num = lambda s, rx, fmt: [fmt(int(a or b)) for a, b in rx.findall(s)]  # noqa: E731
    q["orders"] = list(dict.fromkeys(base["orders"] + [n for s in lists("orders") for n in num(s, ORDER_NUM_RX, order_number)]))
    q["claims"] = list(dict.fromkeys(base["claims"] + [n for s in lists("claims") for n in num(s, CLAIM_NUM_RX, claim_number)]))
    by_lower = {s.lower(): s for s in skus}
    q["skus"] = list(dict.fromkeys(base["skus"] + [by_lower[s.lower()] for s in lists("skus") if s.lower() in by_lower]))
    q["low_stock"] = bool(raw.get("low_stock")) or base["low_stock"]
    q["refers_previous"] = bool(raw.get("refers_previous")) or base["refers_previous"]
    return q


def describe(q: dict) -> str:
    """Человекочитаемое описание того, что искали — показывается продавцу."""
    parts = []
    if q["orders"]:
        parts.append(", ".join(q["orders"][:5]))
    if q["claims"]:
        parts.append(", ".join(q["claims"][:5]))
    if q["period"] != "all":
        parts.append(PERIODS[q["period"]])
    if q["statuses"] and set(q["statuses"]) != set(ACTIVE_STATUSES):
        parts.append("статус: " + ", ".join(orders.STATUSES[s] for s in q["statuses"]))
    elif q["statuses"]:
        parts.append("активные")
    if q["payment"]:
        parts.append("оплачены" if q["payment"] == "paid" else "не оплачены")
    if q["channels"]:
        parts.append(", ".join(orders.CHANNELS[ch] for ch in q["channels"]))
    if q["claim_filter"]:
        parts.append("претензии: " + CLAIM_FILTERS[q["claim_filter"]])
    if q["skus"]:
        parts.append(", ".join(q["skus"]))
    if q["low_stock"]:
        parts.append("малый остаток")
    if q["refers_previous"] and not q["orders"]:
        parts.append("из предыдущего ответа")
    return " · ".join(parts) or "общий поиск"


# ---------- точные выборки из ядра ----------

def period_bounds(period: str) -> tuple[str, str] | None:
    today = db.now().date()
    days = {"today": (0, 0), "yesterday": (1, 1), "week": (6, 0), "month": (29, 0)}.get(period)
    if not days:
        return None
    return (today - timedelta(days=days[0])).isoformat(), (today - timedelta(days=days[1])).isoformat()


def resolve_orders(c: sqlite3.Connection, numbers: list[str]) -> dict[str, int]:
    out = {}
    for n in numbers:
        r = c.execute("SELECT id FROM orders WHERE number = ?", (n,)).fetchone()
        if r:
            out[n] = r[0]
    return out


def resolve_claims(c: sqlite3.Connection, numbers: list[str]) -> dict[str, int]:
    out = {}
    for n in numbers:
        r = c.execute("SELECT id FROM claims WHERE number = ?", (n,)).fetchone()
        if r:
            out[n] = r[0]
    return out


def _item_line(it: dict) -> str:
    title = it["passport"].get("title_ru") or it["sku"]
    return f"{title} ({it['sku']}){' · ' + it['variant'] if it['variant'] else ''} × {it['qty']} · {it['price']:,} тг".replace(",", " ")


def order_record(c: sqlite3.Connection, oid: int) -> dict:
    """Карточка заказа для модели: без имени, телефона и адреса покупателя."""
    o = orders.get(c, oid)
    has_pack = orders._has_packing_evidence(c, oid)
    open_claims = c.execute("SELECT COUNT(*) FROM claims WHERE order_id = ? AND status != 'closed'", (oid,)).fetchone()[0]
    last = c.execute("SELECT direction, text FROM messages WHERE order_id = ? ORDER BY id DESC LIMIT 1", (oid,)).fetchone()
    known = {"name": o["buyer_name"], "phone": o["buyer_phone"], "address": o["buyer_address"]}
    blockers = []
    if o["status"] == "new" and o["payment_status"] != "paid":
        blockers.append("ждёт подтверждения оплаты из платёжного источника (номер операции)")
    if o["status"] == "paid" and not has_pack:
        blockers.append("перед статусом «Собран» нужно видео или фото упаковки")
    if o["status"] in ("paid", "assembled") and o["channel"] != "kaspi" and not o["address_confirmed"]:
        blockers.append("перед отправкой нужно подтвердить адрес и окно доставки")
    if open_claims:
        blockers.append(f"открытых претензий: {open_claims}")
    rec = {
        "ref": f"order:{oid}", "number": o["number"], "channel": o["channel_label"], "status": o["status_label"],
        "status_code": o["status"], "payment": "оплачен" if o["payment_status"] == "paid" else "не оплачен",
        "total_tg": o["total"], "created": o["created_at"][:16].replace("T", " "),
        "delivered": (o["delivered_at"] or "")[:10] or None, "items": [_item_line(it) for it in o["items"]],
        "address_confirmed": bool(o["address_confirmed"]), "delivery_window": o["delivery_window"],
        "packing_evidence": has_pack, "next_statuses": [orders.STATUSES[s] for s in o["next_statuses"]],
        "blockers": blockers, "open_claims": open_claims,
    }
    if o["cancel_reason"]:
        rec["cancel_reason"] = o["cancel_reason"]
    if last and last["direction"] == "in":
        rec["last_buyer_message"] = redact(last["text"], known)[:400]
    return rec


def select_orders(c: sqlite3.Connection, q: dict, prev_ids: list[int] | None = None) -> dict:
    explicit = resolve_orders(c, q["orders"])
    sql, args = "SELECT id, status, channel, total FROM orders WHERE 1=1", []
    ids = list(explicit.values())
    if not ids and q["refers_previous"] and prev_ids:
        ids = list(prev_ids)
    if ids:
        sql += f" AND id IN ({','.join('?' * len(ids))})"
        args += ids
    if q["orders"]:
        # Номера названы явно — остальные слова («оплата на Kaspi») не сужают выборку
        q = {**q, "statuses": [], "channels": [], "payment": None, "period": "all"}
    if q["statuses"]:
        sql += f" AND status IN ({','.join('?' * len(q['statuses']))})"
        args += q["statuses"]
    if q["channels"]:
        sql += f" AND channel IN ({','.join('?' * len(q['channels']))})"
        args += q["channels"]
    if q["payment"] == "paid":
        sql += " AND payment_status = 'paid'"
    elif q["payment"] == "unpaid":
        sql += " AND payment_status != 'paid'"
    bounds = period_bounds(q["period"])
    if bounds:
        field = "delivered_at" if q["statuses"] == ["delivered"] else "created_at"
        sql += f" AND substr({field}, 1, 10) BETWEEN ? AND ?"
        args += list(bounds)
    rows = db.rows(c.execute(sql + " ORDER BY id DESC", args))
    not_cancelled = [r for r in rows if r["status"] != "cancelled"]
    return {
        "filters": describe(q),
        "count": len(rows),
        "sum_tg_without_cancelled": sum(r["total"] for r in not_cancelled),
        "by_status": dict(Counter(orders.STATUSES[r["status"]] for r in rows)),
        "by_channel": dict(Counter(orders.CHANNELS.get(r["channel"], r["channel"]) for r in rows)),
        "date_field": "дата выдачи" if q["statuses"] == ["delivered"] and bounds else "дата создания заказа",
        "shown": min(len(rows), MAX_ORDERS),
        "items": [order_record(c, r["id"]) for r in rows[:MAX_ORDERS]],
        "not_found": [n for n in q["orders"] if n not in explicit],
    }


def claim_record(c: sqlite3.Connection, cl: dict) -> dict:
    o = c.execute("SELECT number, buyer_name, buyer_phone, buyer_address FROM orders WHERE id = ?", (cl["order_id"],)).fetchone()
    known = {"name": o["buyer_name"], "phone": o["buyer_phone"], "address": o["buyer_address"]}
    t = cl["timer"]
    rec = {
        "ref": f"claim:{cl['id']}", "number": cl["number"], "order": o["number"],
        "type": cl["type_label"] if cl["type"] else f"не подтверждён (ИИ предполагает: {cl['ai_type_label']})",
        "status": CLAIM_STATUS_LABELS.get(cl["status"], cl["status"]), "status_code": cl["status"],
        "received": cl["received_at"][:10], "deadline": cl["deadline"], "days_left": t["days_left"],
        "overdue": t["overdue"], "urgent": t["urgent"],
        "summary": redact(cl.get("summary") or cl.get("text") or "", known)[:400],
    }
    if cl.get("decision"):
        rec["decision"] = rules.DECISIONS.get(cl["decision"], cl["decision"])
    return rec


def select_claims(c: sqlite3.Connection, q: dict, prev_ids: list[int] | None = None) -> dict:
    explicit = resolve_claims(c, q["claims"])
    rows = claims_mod.list_claims(c)
    ids = set(explicit.values()) or (set(prev_ids or []) if q["refers_previous"] else set())
    if ids:
        rows = [r for r in rows if r["id"] in ids]
    f = None if explicit else q["claim_filter"]
    if f in ("open", "decided", "closed"):
        rows = [r for r in rows if r["status"] == f]
    elif f in ("urgent", "overdue"):
        rows = [r for r in rows if r["timer"][f]]
    elif not ids:
        rows = [r for r in rows if r["status"] != "closed"]
    order_ids = set(resolve_orders(c, q["orders"]).values())
    if order_ids and not ids:
        rows = [r for r in rows if r["order_id"] in order_ids]
    bounds = None if explicit else period_bounds(q["period"])
    if bounds:
        rows = [r for r in rows if bounds[0] <= r["received_at"][:10] <= bounds[1]]
    return {
        "filters": describe(q), "count": len(rows),
        "overdue": sum(1 for r in rows if r["timer"]["overdue"]), "urgent": sum(1 for r in rows if r["timer"]["urgent"]),
        "items": [claim_record(c, r) for r in rows[:MAX_CLAIMS]],
        "not_found": [n for n in q["claims"] if n not in explicit],
    }


def product_record(c: sqlite3.Connection, pid: int) -> dict:
    p = passport.get_product(c, pid)
    d = p["passport"]["data"]
    return {
        "ref": f"product:{pid}", "sku": p["sku"], "title": d.get("title_ru"), "category": p["category_label"],
        "price_tg": d.get("price"), "stock": p["stock"], "reserved": p["reserved"], "free": p["free"],
        "variants": [{"label": v["label"], "stock": v["stock"], "free": v["free"]} for v in p["inventory_variants"]],
        "passport_version": p["current_version"],
        "passport_missing": [m["label"] for m in (p["passport"]["check_result"] or {}).get("missing", [])],
    }


def _is_low(rec: dict) -> bool:
    return rec["free"] <= LOW_STOCK or any(v["free"] == 0 for v in rec["variants"])


def select_products(c: sqlite3.Connection, q: dict, prev_ids: list[int] | None = None) -> dict:
    ids = [r[0] for r in c.execute("SELECT id FROM products ORDER BY id")]
    if q["skus"]:
        ids = [r[0] for r in c.execute(f"SELECT id FROM products WHERE sku IN ({','.join('?' * len(q['skus']))})", q["skus"])]
    elif q["refers_previous"] and prev_ids:
        ids = [i for i in ids if i in prev_ids]
    recs = [product_record(c, i) for i in ids]
    if q["low_stock"]:
        recs = [r for r in recs if _is_low(r)]
    return {"filters": describe(q), "count": len(recs), "low_stock_threshold": LOW_STOCK, "items": recs[:MAX_PRODUCTS]}


METRIC_LABELS = {
    "orders_total": "Всего заказов", "delivered": "Выдано", "orders_value": "Сумма заказов без отменённых, тг",
    "payments_received": "Подтверждено оплат, тг", "unpaid_orders_value": "Ожидается оплата, тг",
    "refunds": "Возвраты денег, тг", "revenue": "Оплаты за вычетом возвратов, тг",
    "claims_total": "Претензий всего", "claims_overdue_open": "Просроченных открытых претензий",
    "claims_on_time_rate": "Претензий решено в срок, %", "no_stock_rate": "Отказы из-за нехватки остатка, %",
    "not_as_described_rate": "Претензий «не соответствует описанию» на выданные, %",
}


def metrics_record(c: sqlite3.Connection) -> dict:
    m = metrics.compute(c)
    return {"ref": "metrics", **{label: m.get(k) for k, label in METRIC_LABELS.items()}}


def overview(c: sqlite3.Connection) -> dict:
    today = db.now().date().isoformat()
    one = lambda sql, *a: c.execute(sql, a).fetchone()[0]  # noqa: E731
    active = claims_mod.list_claims(c, "active")
    low = sum(1 for (pid,) in c.execute("SELECT id FROM products") if _is_low(product_record(c, pid)))
    unanswered = one("SELECT COUNT(*) FROM messages m WHERE m.id = (SELECT MAX(id) FROM messages WHERE order_id = m.order_id) "
                     "AND m.direction = 'in'")
    return {
        "today": today,
        "orders_created_today": one("SELECT COUNT(*) FROM orders WHERE substr(created_at, 1, 10) = ?", today),
        "awaiting_payment": one("SELECT COUNT(*) FROM orders WHERE status = 'new' AND payment_status != 'paid'"),
        "to_assemble": one("SELECT COUNT(*) FROM orders WHERE status = 'paid'"),
        "to_ship": one("SELECT COUNT(*) FROM orders WHERE status = 'assembled'"),
        "in_delivery": one("SELECT COUNT(*) FROM orders WHERE status = 'shipped'"),
        "claims_waiting_decision": len(active),
        "claims_urgent": sum(1 for cl in active if cl["timer"]["urgent"]),
        "claims_overdue": sum(1 for cl in active if cl["timer"]["overdue"]),
        "low_stock_products": low,
        "unanswered_buyer_messages": unanswered,
    }


# ---------- база знаний и BM25 ----------

HELP = [
    ("flow", "Статусы заказа",
     "Заказ проходит путь: Новый (резерв) → Оплачен → Собран → Передан в доставку → Выдан → Закрыт. Отменить можно до передачи "
     "в доставку, резерв при этом снимается. При передаче в доставку товар списывается со склада."),
    ("payment", "Подтверждение оплаты",
     "Оплата подтверждается только из платёжного источника: Kaspi (данные площадки), выписка или уведомление банка, "
     "платёжный провайдер, наличные с фискальным чеком. Скриншот от покупателя не подтверждает оплату. Нужен номер операции; "
     "сумма должна совпасть с суммой заказа; один номер операции нельзя привязать к двум заказам."),
    ("packing", "Видео упаковки",
     "Статус «Собран» нельзя поставить без видео или фото упаковки. Загрузите его в карточке заказа: что положили, "
     "состояние и комплект. Это главное доказательство в споре."),
    ("address", "Адрес и окно доставки",
     "Перед статусом «Передан в доставку» нужно подтвердить с покупателем адрес и окно доставки (кроме заказов Kaspi). "
     "Это делается в карточке заказа."),
    ("claims", "Претензии и сроки",
     "Ответить на претензию нужно в течение 10 календарных дней. ИИ только предлагает тип претензии, человек подтверждает "
     "тип, позицию, количество и состояние товара, затем выбирает решение из допустимых по правилам. Решение принимается "
     "в разделе «Претензии»; Adal Copilot решения по претензиям и возвраты денег не выполняет."),
    ("refusal", "Отказ в возврате",
     "Отказ возможен, только если правила это допускают (например, истёк срок, товар из перечня невозвратных или утратил "
     "товарный вид), и только с мотивировкой не короче 40 символов. Фраза «возврату не подлежит» без основания запрещена."),
    ("return_rules", "Возврат товара надлежащего качества",
     "Исправный непродовольственный товар можно вернуть или обменять в течение 14 календарных дней, если он не был в "
     "употреблении, сохранены товарный вид, пломбы и ярлыки. Исключения — бельё, чулочно-носочные изделия, лекарства, "
     "метражные товары, верифицированные мобильные устройства. Правила утверждает юрист в разделе «Правила и ИИ»."),
    ("variants", "Остатки по размерам и цветам",
     "В карточке товара откройте «Остатки по размерам и цветам» и укажите количество каждой комбинации. Сумма становится "
     "общим остатком. Резерв, отмена, отправка и возврат учитывают выбранный вариант. Снять вариант с продажи — остаток 0."),
    ("stock", "Остаток и резерв",
     "Свободный остаток = остаток − резерв. Заказ без свободного остатка не принимается. Остаток нельзя установить меньше "
     "резерва. Для товаров с вариантами меняются остатки вариантов, общий считается автоматически."),
    ("intake", "Заказ из сообщения или голосового",
     "В «Сводке» и «Заказах» кнопка «Из сообщения / голоса» разбирает сообщение покупателя в черновик заказа. "
     "Резерв появляется только после проверки и подтверждения продавцом."),
    ("kaspi", "Заказы Kaspi",
     "Заказы Kaspi приходят через API Kaspi (кнопка синхронизации); оплата подтверждается данными площадки. Возвраты Kaspi "
     "идут через процесс Kaspi. Покупателей Kaspi не уводят на внешние страницы."),
    ("ai_mode", "Теневой режим ИИ",
     "В теневом режиме ИИ-черновики не отправляются покупателям — продавец оценивает их и отвечает сам. Режим меняется в "
     "разделе «Правила и ИИ»."),
    ("passport", "Паспорт товара",
     "Паспорт содержит обязательные поля: название и описание на казахском и русском, бренд, страна, состав, размеры, цена "
     "и поля категории. Каждая правка создаёт новую версию; заказ хранит версию, по которой товар продан."),
    ("copilot", "Что умеет Adal Copilot",
     "Adal Copilot отвечает на вопросы по заказам, товарам, претензиям и метрикам и выполняет поручения: сменить статус "
     "заказа, подтвердить оплату с номером операции, подтвердить адрес и окно доставки, отправить сообщение покупателю, "
     "закрыть претензию с решением, установить остаток, забрать заказы Kaspi. Перед выполнением он показывает план (to-do) "
     "и ждёт одобрения; выполняет только одобренные шаги по порядку."),
]

_WORD = re.compile(r"[0-9a-zа-яәғқңөұүһі]+")
_STOP = set("и в во на по с со к ко о об от до за из у а но или ли же бы не ни что как какой какие сколько это эти мне "
            "нас у нас все всё для при есть был была были будет его ее их они мы вы ты я пожалуйста покажи скажи "
            "және мен бар ма ме қанша қандай".split())
_ENDINGS = sorted("ами ями ого его ому ему ыми ими ых их ой ей ий ый ая яя ое ее ые ие ов ев ам ям ах ях ом ем ую юю "
                  "а я о е ы и у ю ь".split(), key=len, reverse=True)


def stem(word: str) -> str:
    for end in _ENDINGS:
        if word.endswith(end) and len(word) - len(end) >= 3:
            word = word[:-len(end)]
            break
    return word[:6]


def tokens(text: str) -> list[str]:
    return [w if w.isdigit() else stem(w) for w in _WORD.findall(norm(text)) if w not in _STOP and (len(w) > 1 or w.isdigit())]


class BM25:
    def __init__(self, docs: list[dict], k1: float = 1.4, b: float = 0.75):
        self.docs, self.k1, self.b = docs, k1, b
        self.tfs = [Counter(tokens(d["search"])) for d in docs]
        self.lens = [sum(tf.values()) for tf in self.tfs]
        self.avg = (sum(self.lens) / len(self.lens)) if self.lens else 1.0
        self.df = Counter(t for tf in self.tfs for t in tf)

    def search(self, query: str, k: int = 6, min_score: float = 0.5) -> list[tuple[float, dict]]:
        q = set(tokens(query))
        n = len(self.docs)
        scored = []
        for i, tf in enumerate(self.tfs):
            s = 0.0
            for t in q & tf.keys():
                idf = math.log(1 + (n - self.df[t] + 0.5) / (self.df[t] + 0.5))
                s += idf * tf[t] * (self.k1 + 1) / (tf[t] + self.k1 * (1 - self.b + self.b * self.lens[i] / self.avg))
            if s >= min_score:
                scored.append((s, self.docs[i]))
        return sorted(scored, key=lambda x: -x[0])[:k]


def _documents(c: sqlite3.Connection) -> list[dict]:
    """Документы базы знаний. search — для поиска (может содержать имя покупателя), text — для модели (без него)."""
    docs = []
    for (oid,) in c.execute("SELECT id FROM orders ORDER BY id"):
        rec = order_record(c, oid)
        o = c.execute("SELECT buyer_name, buyer_phone FROM orders WHERE id = ?", (oid,)).fetchone()
        text = (f"Заказ {rec['number']} · {rec['channel']} · {rec['status']} · {rec['payment']} · {rec['total_tg']} тг · "
                f"создан {rec['created']} · товары: {'; '.join(rec['items'])}")
        if rec["blockers"]:
            text += " · мешает: " + "; ".join(rec["blockers"])
        if rec.get("last_buyer_message"):
            text += f" · последнее сообщение покупателя (данные): {rec['last_buyer_message']}"
        docs.append({"ref": rec["ref"], "kind": "order", "id": oid, "label": rec["number"], "text": text,
                     "search": f"{text} {o['buyer_name'] or ''} {o['buyer_phone'] or ''}"})
    for (pid,) in c.execute("SELECT id FROM products ORDER BY id"):
        p = passport.get_product(c, pid)
        d = p["passport"]["data"]
        fields = "; ".join(f"{passport.FIELDS[k][0]}: {v}" for k, v in d.items()
                           if k in passport.FIELDS and k not in ("photos", "supplier_text") and v not in (None, "", []))
        text = f"Товар {p['sku']} · {p['category_label']} · свободно {p['free']} из {p['stock']} · {fields}"
        docs.append({"ref": f"product:{pid}", "kind": "product", "id": pid, "label": p["sku"], "text": text[:1500], "search": text})
    for cl in claims_mod.list_claims(c):
        rec = claim_record(c, cl)
        text = (f"Претензия {rec['number']} по заказу {rec['order']} · {rec['type']} · {rec['status']} · срок ответа "
                f"{rec['deadline']} · суть (данные покупателя): {rec['summary']}")
        docs.append({"ref": rec["ref"], "kind": "claim", "id": cl["id"], "label": rec["number"], "text": text,
                     "search": f"{text} {cl.get('text') or ''}"})
    r = rules.active(c)
    rd = r["data"]
    rules_text = (f"Правила возврата, версия {r['version']} ({'утверждены юристом' if r['approved'] else 'НЕ утверждены юристом'}): "
                  f"возврат исправного товара {rd['return_days_non_food']} дней при условиях: {'; '.join(rd['return_conditions_ru'])}. "
                  f"Ответ на претензию — {rd['claim_response_days']} календарных дней. Невозвратные категории: "
                  f"{'; '.join(rd['excluded_categories'].values())}. Куда обращаться: {rd['escalation_ru']}.")
    docs.append({"ref": "rules", "kind": "rules", "id": r["version"], "label": f"Правила v{r['version']}",
                 "text": rules_text, "search": rules_text})
    for key, title, text in HELP:
        docs.append({"ref": f"help:{key}", "kind": "help", "id": key, "label": f"Справка: {title}",
                     "text": f"{title}. {text}", "search": f"{title} {text}"})
    return docs


_INDEX: dict = {"key": None, "bm25": None}


def _fingerprint(c: sqlite3.Connection) -> tuple:
    tables = ("events", "passport_versions", "rules", "messages", "claims", "orders", "product_variants", "ai_drafts")
    key = tuple(c.execute(f"SELECT COALESCE(MAX(id), 0) FROM {t}").fetchone()[0] for t in tables)
    return key + tuple(c.execute("SELECT COALESCE(SUM(stock), 0), COALESCE(SUM(reserved), 0) FROM products").fetchone())


def index(c: sqlite3.Connection) -> BM25:
    """Индекс перестраивается, только когда меняются данные ядра."""
    key = _fingerprint(c)
    if _INDEX["key"] != key:
        _INDEX["bm25"], _INDEX["key"] = BM25(_documents(c)), key
    return _INDEX["bm25"]


# ---------- сборка контекста ----------

def retrieve(c: sqlite3.Connection, q: dict, prev: dict | None = None) -> dict:
    """Контекст для модели: обзор дня + точные выборки + найденные документы. Плюс список источников."""
    prev = prev or {}
    topics = set(q["topics"])
    order_filters = q["statuses"] or q["channels"] or q["payment"] or q["orders"]
    ctx: dict = {"overview": overview(c), "query": describe(q), "focus": "overview" if "overview" in topics else None}
    if "orders" in topics or order_filters or (q["refers_previous"] and prev.get("orders") and not q["claims"]) \
            or (q["period"] != "all" and not topics):
        ctx["orders"] = select_orders(c, q, prev.get("orders"))
    if "claims" in topics or q["claims"] or q["claim_filter"] or (q["refers_previous"] and prev.get("claims") and not q["orders"]):
        ctx["claims"] = select_claims(c, q, prev.get("claims"))
    if "products" in topics or q["skus"] or q["low_stock"]:
        ctx["products"] = select_products(c, q, prev.get("products"))
    if "metrics" in topics:
        ctx["metrics"] = metrics_record(c)
    seen = {it["ref"] for part in ("orders", "claims", "products") if part in ctx for it in ctx[part]["items"]}
    hits = index(c).search(q["search"] or q["text"], k=8 if "help" in topics else 5)
    ctx["documents"] = [{"ref": d["ref"], "text": d["text"]} for _, d in hits if d["ref"] not in seen]
    sources = []
    for part, kind, label in (("orders", "order", "number"), ("claims", "claim", "number"), ("products", "product", "sku")):
        for it in ctx.get(part, {}).get("items", []):
            sources.append({"ref": it["ref"], "kind": kind, "id": int(it["ref"].split(":")[1]), "label": it[label]})
    sources += [{"ref": d["ref"], "kind": d["kind"], "id": d["id"], "label": d["label"]} for _, d in hits if d["ref"] not in seen]
    if "metrics" in ctx:
        sources.append({"ref": "metrics", "kind": "metrics", "id": None, "label": "Аналитика"})
    return {"context": ctx, "sources": sources}
