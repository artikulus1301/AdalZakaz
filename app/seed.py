"""Демо-данные для показа: несколько товаров Тараза, заказы из разных каналов, претензии."""
from datetime import timedelta

from . import db
from .ai.orchestrator import classify_claim_heuristic
from .core import claims, evidence, orders, passport, rules
from .connectors import manual

DEMO_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="480" height="270"><rect width="100%" height="100%" fill="#e8efe9"/>
<text x="50%" y="50%" font-family="sans-serif" font-size="22" text-anchor="middle" fill="#245c3a">{text}</text></svg>"""

PRODUCTS = [
    ("DR-001", "clothing", 6, {
        "title_ru": "Платье миди трикотажное", "title_kk": "Трикотаж миди көйлек",
        "desc_ru": "Платье миди из трикотажа, прямой крой, рукав 3/4. Цвет: изумрудный. Состав: 95% вискоза, 5% эластан.",
        "desc_kk": "Трикотаждан тігілген миди көйлек, тік пішім, жеңі 3/4. Түсі: изумруд. Құрамы: 95% вискоза, 5% эластан.",
        "brand": "Adal Style", "country": "Турция", "composition": "95% вискоза, 5% эластан",
        "dimensions": "Длина 110 см (размер M)", "color": "изумрудный",
        "size_grid": "S: грудь 84-88 см; M: грудь 88-92 см; L: грудь 92-96 см",
        "variants": "S, M, L", "price": 14900,
        "supplier_text": "Платье женское модное, качество люкс, размеры в наличии",
    }),
    ("SN-002", "shoes", 8, {
        "title_ru": "Кроссовки беговые", "title_kk": "Жүгіруге арналған кроссовкалар",
        "desc_ru": "Лёгкие беговые кроссовки, сетчатый верх, подошва EVA. Вес пары 520 г. Цвет: белый с серым.",
        "desc_kk": "Жеңіл жүгіру кроссовкалары, торлы үсті, EVA табаны. Жұбының салмағы 560 г. Түсі: ақ, сұр.",
        "brand": "RunFast", "country": "Китай", "composition": "Верх — текстиль, подошва — EVA",
        "dimensions": "Высота подошвы 3 см", "color": "белый с серым",
        "size_grid": "39, 40, 41, 42, 43, 44", "variants": "39, 40, 41, 42, 43, 44", "price": 18500,
    }),
    ("EH-003", "electronics", 4, {
        "title_ru": "Беспроводные наушники TWS", "title_kk": "Сымсыз TWS құлаққаптары",
        "desc_ru": "Наушники Bluetooth 5.3, до 6 часов работы, кейс до 24 часов.",
        "desc_kk": "Bluetooth 5.3 құлаққаптары, 6 сағатқа дейін жұмыс, кейс 24 сағатқа дейін.",
        "brand": "SoundKZ", "country": "Китай", "composition": "Пластик ABS",
        "dimensions": "Кейс 6 × 4,5 × 2,5 см", "model": "SK-TWS-2 (версия 2024)", "warranty_months": 12,
        "package_contents": "наушники 2 шт., кейс, кабель USB-C, 3 пары амбушюр", "price": 12990,
    }),
    ("SK-004", "hosiery", 30, {
        "title_ru": "Носки хлопковые, 5 пар", "title_kk": "Мақта шұлықтар, 5 жұп",
        "desc_ru": "Набор из 5 пар хлопковых носков. Состав: 80% хлопок, 20% полиэстер.",
        "desc_kk": "5 жұп мақта шұлық жиынтығы. Құрамы: 80% мақта, 20% полиэстер.",
        "brand": "Taraz Textile", "country": "Казахстан", "composition": "80% хлопок, 20% полиэстер",
        "dimensions": "Высота паголенка 15 см", "size_grid": "36-40, 41-45", "variants": "36-40, 41-45", "price": 3500,
    }),
    ("BL-005", "home", 1, {
        "title_ru": "Плед флисовый 150×200", "title_kk": "Флис жамылғы 150×200",
        "desc_ru": "Мягкий флисовый плед 150×200 см.", "desc_kk": "Жұмсақ флис жамылғы 150×200 см.",
        "brand": "HomeKZ", "country": "Узбекистан", "composition": "100% полиэстер (флис)",
        "dimensions": "150 × 200 см", "color": "бежевый", "price": 7900,
    }),
]


def _demo_file(text: str) -> dict:
    return evidence.store_file(DEMO_SVG.format(text=text).encode(), "demo.svg", "image/svg+xml")


def _advance(order_id: int, to: str, days_ago: int = 0):
    """Проводит заказ по статусам через ядро (с доказательствами), затем сдвигает даты для демо."""
    with db.tx() as c:
        o = orders.get(c, order_id)
        if o["payment_status"] != "paid":
            orders.confirm_payment(c, order_id, "bank_statement", f"BANK-{order_id:05d}", o["total"], "demo")
        if to == "paid":
            return
        evidence.add(c, "packing_video" if order_id % 2 else "packing_photo", _demo_file("Демо: видео/фото упаковки"),
                     "demo", order_id=order_id, note="Демонстрационный файл")
        orders.set_status(c, order_id, "assembled", "demo")
        if to == "assembled":
            return
        if o["channel"] != "kaspi":
            orders.confirm_address(c, order_id, o["buyer_address"] or "Тараз", "Тараз", "10:00–14:00", "demo")
        orders.set_status(c, order_id, "shipped", "demo")
        if to == "shipped":
            return
        orders.set_status(c, order_id, "delivered", "demo")
        when = (db.now() - timedelta(days=days_ago)).isoformat()
        c.execute("UPDATE orders SET delivered_at = ?, created_at = ? WHERE id = ?",
                  (when, (db.now() - timedelta(days=days_ago + 2)).isoformat(), order_id))


def _claim(order_id: int, text: str, lang: str, days_ago: int = 0) -> int:
    with db.tx() as c:
        cid = claims.create(c, order_id, text, "buyer", lang=lang)
        received = db.now() - timedelta(days=days_ago)
        deadline = received.date() + timedelta(days=rules.active(c)["data"]["claim_response_days"])
        c.execute("UPDATE claims SET received_at = ?, deadline = ? WHERE id = ?", (received.isoformat(), deadline.isoformat(), cid))
        ctype, tag = classify_claim_heuristic(text)
        claims.set_ai_result(c, cid, ctype, f"Демо-резюме: {text[:160]}", tag)
    return cid


def run() -> None:
    with db.read() as c:
        if c.execute("SELECT COUNT(*) FROM products").fetchone()[0]:
            return
    with db.tx() as c:
        db.set_setting(c, "ai_mode", "draft")
        db.set_setting(c, "seller_name", "ИП «Адал Сауда», Тараз")
        rules.active(c)
        for sku, cat, stock, data in PRODUCTS:
            pid = passport.create_product(c, sku, cat, stock, data, "demo")
            if sku == "DR-001":
                st = _demo_file("Демо: фото платья")
                evidence.add(c, "product_photo", st, "demo", product_id=pid)
                passport.save_version(c, pid, {**data, "photos": [st["filename"]]}, "Добавлено фото реального товара", "demo")

    def order(channel, name, phone, addr, lang, items, zone="Тараз"):
        r = manual.submit({"channel": channel, "buyer": {"name": name, "phone": phone, "address": addr, "lang": lang},
                           "items": items, "delivery_zone": zone}, "demo")
        return r.get("order_id")

    o1 = order("instagram", "Айгерім Сейітқызы", "+7 701 234 56 78", "Тараз, мкр. Самал 12, кв. 5", "ru",
               [{"sku": "DR-001", "qty": 1, "variant": "M"}])
    o2 = order("telegram", "Нұрлан Әбенов", "+7 747 111 22 33", "Жамбылский р-н, с. Аса, ул. Абая 7", "kk",
               [{"sku": "SK-004", "qty": 1, "variant": "41-45"}], zone="Жамбылский район")
    o3 = order("olx", "Сергей П.", "+7 705 999 88 77", "Тараз, ул. Толе би 45", "ru", [{"sku": "EH-003", "qty": 1}])
    o4 = order("whatsapp", "Гүлназ Төлеуова", "+7 776 555 44 33", "Тараз, пр. Жамбыла 100", "kk",
               [{"sku": "SN-002", "qty": 1}])
    o5 = order("instagram", "Динара Қ.", "+7 702 777 66 55", "Тараз, ул. Сулейманова 3", "ru",
               [{"sku": "EH-003", "qty": 1}])
    # Попытка продать больше, чем есть: заказ не принимается (метрика «отмены из-за отсутствия товара»)
    order("whatsapp", "Асель М.", "+7 700 000 11 22", "Тараз", "ru", [{"sku": "BL-005", "qty": 3}])

    _advance(o1, "delivered", days_ago=4)
    _advance(o2, "delivered", days_ago=6)
    _advance(o3, "delivered", days_ago=25)
    _advance(o5, "paid")

    _claim(o1, "Платье маломерит: заказывала M, а по замерам как S. И цвет на фото был ярче, в жизни темнее.", "ru", days_ago=2)
    _claim(o2, "Шұлық ұнамады, өлшемі сәйкес келмеді, қайтарғым келеді. Қаптамасы ашылмаған.", "kk", days_ago=1)
    c3 = _claim(o3, "Левый наушник не работает, не заряжается в кейсе. Брак.", "ru", days_ago=20)
    with db.tx() as c:
        claims.confirm(c, c3, "defect", None, "defect", "demo")
        claims.decide(c, c3, "replace", "Подтверждён брак левого наушника по видео покупателя; замена на новый комплект.", "demo")
        c.execute("UPDATE claims SET decided_at = ? WHERE id = ?", ((db.now() - timedelta(days=17)).isoformat(), c3))
        claims.close(c, c3, "demo")
        c.execute("INSERT INTO messages(order_id, direction, text, lang, created_at) VALUES (?, 'in', ?, 'kk', ?)",
                  (o4, "Сәлеметсіз бе, 42 өлшем бар ма? Қашан жеткізесіз?", (db.now() - timedelta(minutes=40)).isoformat()))
    # Kaspi-заказ (оплата подтверждена данными площадки)
    with db.tx() as c:
        orders.enqueue(c, "kaspi", "kaspi:demo-512345678", {
            "channel": "kaspi", "external_id": "512345678",
            "buyer": {"name": "Марат Ж.", "phone": "+7 700 123 45 67", "address": "Тараз (Kaspi Доставка)", "lang": "ru"},
            "items": [{"sku": "SN-002", "qty": 1}], "paid_via": "kaspi_api"})
    orders.process_inbox("connector:kaspi")
