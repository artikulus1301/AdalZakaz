"""Коннектор Kaspi Магазина: читает заказы и кладёт их в очередь событий.
Формат по Kaspi Гиду (API JSON:API, заголовок X-Auth-Token). Как получить токен и какие поля
отдаёт API о возвратах — проверить до старта; маппинг ниже помечен как best-effort.
Без токена работает демо-режим: генерирует заказы по товарам из паспортов."""
import random
import time
from datetime import timedelta

import httpx

from .. import config, db
from ..core import orders

HEADERS = {"Content-Type": "application/vnd.api+json", "Accept": "application/vnd.api+json"}


def _client() -> httpx.Client:
    return httpx.Client(base_url=config.KASPI_BASE_URL, headers={**HEADERS, "X-Auth-Token": config.KASPI_TOKEN},
                        timeout=30)


def fetch_new_orders(days: int = 3) -> list[dict]:
    now_ms = int(time.time() * 1000)
    since = now_ms - days * 86400 * 1000
    params = {
        "page[number]": 0, "page[size]": 50,
        "filter[orders][state]": "NEW",
        "filter[orders][creationDate][$ge]": since,
        "filter[orders][creationDate][$le]": now_ms,
    }
    out = []
    with _client() as cli:
        r = cli.get("/orders", params=params)
        r.raise_for_status()
        for o in r.json().get("data", []):
            a = o.get("attributes", {})
            entries = cli.get(f"/orders/{o['id']}/entries").json().get("data", [])
            items = []
            for e in entries:
                ea = e.get("attributes", {})
                sku = (ea.get("offer") or {}).get("code") or ea.get("merchantProduct", {}).get("code")
                items.append({"sku": sku, "qty": int(ea.get("quantity") or 1), "price": int(ea.get("basePrice") or 0)})
            cust = a.get("customer") or {}
            out.append({
                "channel": "kaspi",
                "external_id": a.get("code") or o["id"],
                "buyer": {"name": f"{cust.get('firstName', '')} {cust.get('lastName', '')}".strip(),
                          "phone": cust.get("cellPhone"), "address": (a.get("deliveryAddress") or {}).get("formattedAddress"),
                          "lang": "ru"},
                "items": items,
                "paid_via": "kaspi_api",
            })
    return out


def demo_orders(n: int = 2) -> list[dict]:
    with db.read() as c:
        skus = [r["sku"] for r in c.execute("SELECT sku FROM products WHERE stock - reserved > 0")]
    if not skus:
        return []
    out = []
    for _ in range(n):
        code = str(random.randint(100000000, 999999999))
        out.append({
            "channel": "kaspi", "external_id": code,
            "buyer": {"name": random.choice(["Айгерім С.", "Ержан Т.", "Марина К.", "Дәулет Б."]),
                      "phone": "+7 701 000 00 00", "address": "Тараз (Kaspi Доставка)", "lang": random.choice(["ru", "kk"])},
            "items": [{"sku": random.choice(skus), "qty": 1}],
            "paid_via": "kaspi_api",
            "created_at": (db.now() - timedelta(minutes=random.randint(1, 120))).isoformat(),
        })
    return out


def sync() -> dict:
    """Забрать новые заказы и пропустить через очередь в ядро."""
    mode = "api" if config.KASPI_TOKEN else "demo"
    try:
        incoming = fetch_new_orders() if mode == "api" else demo_orders()
    except httpx.HTTPError as e:
        return {"mode": mode, "error": f"Kaspi API недоступен: {e}", "results": []}
    queued = 0
    with db.tx() as c:
        for p in incoming:
            if orders.enqueue(c, "kaspi", f"kaspi:{p['external_id']}", p):
                queued += 1
    return {"mode": mode, "queued": queued, "results": orders.process_inbox("connector:kaspi")}
