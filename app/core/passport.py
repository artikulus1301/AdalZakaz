"""Паспорт товара: версии и обязательные поля. Версия неизменна после сохранения,
заказ ссылается на ту версию, по которой товар продали."""
import sqlite3

from .. import db
from . import CoreError, NotFound

# Поля паспорта: ключ -> (подпись ru, подпись kk)
FIELDS: dict[str, tuple[str, str]] = {
    "title_ru": ("Название (рус)", "Атауы (орыс)"),
    "title_kk": ("Название (каз)", "Атауы (қаз)"),
    "desc_ru": ("Описание (рус)", "Сипаттама (орыс)"),
    "desc_kk": ("Описание (каз)", "Сипаттама (қаз)"),
    "brand": ("Бренд", "Бренд"),
    "country": ("Страна производства", "Өндіруші ел"),
    "composition": ("Состав / материал", "Құрамы / материалы"),
    "dimensions": ("Размеры", "Өлшемдері"),
    "weight": ("Вес", "Салмағы"),
    "color": ("Цвет", "Түсі"),
    "size_grid": ("Размерная сетка", "Өлшем торы"),
    "variants": ("Варианты (через запятую)", "Нұсқалар (үтір арқылы)"),
    "model": ("Модель / версия", "Моделі / нұсқасы"),
    "warranty_months": ("Гарантия, мес.", "Кепілдік, ай"),
    "package_contents": ("Комплектация", "Жиынтығы"),
    "price": ("Цена, тг", "Бағасы, тг"),
    "supplier_text": ("Текст поставщика (исходник)", "Жеткізуші мәтіні"),
    "photos": ("Фото реального товара", "Тауардың нақты фотосы"),
}

# Что требует закон о защите прав потребителей для онлайн-продажи + казахский и русский
REQUIRED_BASE = ["title_ru", "title_kk", "desc_ru", "desc_kk", "brand", "country", "composition", "dimensions", "price"]

CATEGORIES: dict[str, dict] = {
    "clothing": {"ru": "Одежда", "kk": "Киім", "extra": ["size_grid", "color"]},
    "shoes": {"ru": "Обувь", "kk": "Аяқ киім", "extra": ["size_grid", "color"]},
    "electronics": {"ru": "Электроника", "kk": "Электроника", "extra": ["model", "warranty_months", "package_contents"]},
    "cosmetics": {"ru": "Косметика", "kk": "Косметика", "extra": []},
    "home": {"ru": "Дом и быт", "kk": "Үй және тұрмыс", "extra": ["color"]},
    "underwear": {"ru": "Нательное бельё", "kk": "Іш киім", "extra": ["size_grid", "color"]},
    "hosiery": {"ru": "Чулочно-носочные изделия", "kk": "Шұлық бұйымдары", "extra": ["size_grid"]},
    "mobile_verified": {"ru": "Мобильные устройства (верифицир.)", "kk": "Мобильді құрылғылар", "extra": ["model", "warranty_months"]},
    "medicine": {"ru": "Лекарства и медизделия", "kk": "Дәрілер мен медбұйымдар", "extra": []},
    "metered": {"ru": "Метражные товары", "kk": "Метрлік тауарлар", "extra": ["dimensions"]},
    "other": {"ru": "Другое", "kk": "Басқа", "extra": []},
}


def required_fields(category: str) -> list[str]:
    extra = CATEGORIES.get(category, CATEGORIES["other"])["extra"]
    return REQUIRED_BASE + [f for f in extra if f not in REQUIRED_BASE]


def _empty(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip()) or (isinstance(v, list) and not v)


def normalize(data: dict) -> dict:
    out = {k: data.get(k) for k in FIELDS if k in data}
    for k, v in list(out.items()):
        if isinstance(v, str):
            out[k] = v.strip()
    if not _empty(out.get("price")):
        try:
            out["price"] = int(float(str(out["price"]).replace(" ", "").replace(",", ".")))
        except ValueError:
            raise CoreError("Цена должна быть числом")
    if not _empty(out.get("warranty_months")):
        try:
            out["warranty_months"] = int(out["warranty_months"])
        except (TypeError, ValueError):
            raise CoreError("Гарантия указывается числом месяцев")
    out["photos"] = list(data.get("photos") or [])
    return out


def program_check(data: dict, category: str) -> dict:
    """Детерминированная проверка карточки (без ИИ)."""
    missing = [f for f in required_fields(category) if _empty(data.get(f))]
    warnings: list[str] = []
    if not data.get("photos"):
        warnings.append("Нет фото реального товара — только фото поставщика повышают риск «не соответствует описанию».")
    if isinstance(data.get("price"), int) and data["price"] <= 0:
        missing.append("price")
    sup = (data.get("supplier_text") or "").strip().lower()
    for f in ("desc_ru", "desc_kk"):
        if sup and (data.get(f) or "").strip().lower() == sup:
            warnings.append(f"Поле «{FIELDS[f][0]}» дословно скопировано у поставщика — сверьте с реальным товаром.")
    variants = [v.strip() for v in (data.get("variants") or "").split(",") if v.strip()]
    if len(variants) > 1:
        warnings.append("У товара несколько вариантов — перед отправкой уточните вариант у покупателя.")
    if category in ("clothing", "shoes", "underwear") and data.get("size_grid") and "см" not in data["size_grid"].lower():
        warnings.append("В размерной сетке нет замеров в сантиметрах — покупателю трудно выбрать размер.")
    return {
        "ok": not missing,
        "missing": [{"field": f, "label": FIELDS[f][0]} for f in missing],
        "warnings": warnings,
    }


def create_product(c: sqlite3.Connection, sku: str, category: str, stock: int, data: dict, author: str) -> int:
    if category not in CATEGORIES:
        raise CoreError("Неизвестная категория")
    if not sku.strip():
        raise CoreError("Нужен артикул (SKU)")
    if c.execute("SELECT 1 FROM products WHERE sku = ?", (sku,)).fetchone():
        raise CoreError("Товар с таким артикулом уже есть", status=409)
    cur = c.execute(
        "INSERT INTO products(sku, category, stock, created_at) VALUES (?, ?, ?, ?)",
        (sku.strip(), category, max(0, int(stock)), db.now_iso()),
    )
    pid = cur.lastrowid
    save_version(c, pid, data, "Создание паспорта", author)
    return pid


def save_version(c: sqlite3.Connection, product_id: int, data: dict, note: str, author: str) -> int:
    p = c.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if not p:
        raise NotFound("Товар")
    clean = normalize(data)
    check = program_check(clean, p["category"])
    version = p["current_version"] + 1
    c.execute(
        "INSERT INTO passport_versions(product_id, version, data, check_result, note, author, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (product_id, version, db.dumps(clean), db.dumps(check), note, author, db.now_iso()),
    )
    c.execute("UPDATE products SET current_version = ? WHERE id = ?", (version, product_id))
    return version


def get_version(c: sqlite3.Connection, product_id: int, version: int | None = None) -> dict:
    if version is None:
        version = c.execute("SELECT current_version FROM products WHERE id = ?", (product_id,)).fetchone()
        if not version:
            raise NotFound("Товар")
        version = version[0]
    r = c.execute(
        "SELECT * FROM passport_versions WHERE product_id = ? AND version = ?", (product_id, version)
    ).fetchone()
    if not r:
        raise NotFound("Версия паспорта")
    out = dict(r)
    out["data"] = db.loads(out["data"], {})
    out["check_result"] = db.loads(out["check_result"], {})
    return out


def get_product(c: sqlite3.Connection, product_id: int) -> dict:
    p = db.row(c.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone())
    if not p:
        raise NotFound("Товар")
    p["passport"] = get_version(c, product_id)
    p["free"] = p["stock"] - p["reserved"]
    p["category_label"] = CATEGORIES.get(p["category"], {}).get("ru", p["category"])
    p["required"] = required_fields(p["category"])
    return p


def list_versions(c: sqlite3.Connection, product_id: int) -> list[dict]:
    out = db.rows(
        c.execute(
            "SELECT version, note, author, created_at, check_result FROM passport_versions "
            "WHERE product_id = ? ORDER BY version DESC",
            (product_id,),
        )
    )
    for v in out:
        v["check_result"] = db.loads(v["check_result"], {})
    return out


def diff(old: dict, new: dict) -> list[dict]:
    keys = [k for k in FIELDS if k != "photos"]
    return [
        {"field": k, "label": FIELDS[k][0], "old": old.get(k), "new": new.get(k)}
        for k in keys
        if (old.get(k) or "") != (new.get(k) or "")
    ]
