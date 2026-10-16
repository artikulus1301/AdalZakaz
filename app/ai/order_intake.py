"""Message -> editable order draft. Matching, prices and availability stay local."""
import re
import unicodedata
from collections import Counter
from datetime import timedelta

from pydantic import BaseModel, Field

from .. import db
from ..core import CoreError, inventory, orders, passport
from . import orchestrator
from .redact import PATTERNS, redact


class RequestLine(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class ExtractedLines(BaseModel):
    items: list[RequestLine] = Field(min_length=1, max_length=20)


COLORS = {
    "white": ("бел", "ақ"), "black": ("черн", "қара"), "blue": ("син", "голуб", "көк"),
    "green": ("зелен", "изумруд", "жасыл"), "red": ("красн", "қызыл"),
    "gray": ("сер", "сұр"), "beige": ("беж", "сарғыш"), "pink": ("розов", "қызғылт"),
    "yellow": ("желт", "сары"), "brown": ("коричн", "қоңыр"), "orange": ("оранж", "қызғылтсары"),
    "purple": ("фиолет", "күлгін"),
}
PRODUCT_WORDS = {
    "dress": ("плать", "көйлек"), "sneakers": ("кроссов", "кроссы"),
    "headphones": ("наушник", "құлаққап"), "socks": ("носки", "носков", "носок", "шұлық"),
    "blanket": ("плед", "жамылғы"),
}
NUMBERS = {"один": 1, "одна": 1, "одну": 1, "бір": 1, "два": 2, "две": 2, "екі": 2,
           "три": 3, "үш": 3, "четыре": 4, "төрт": 4, "пять": 5, "бес": 5}
STOP = set("нужен нужна нужны нужно хочу куплю заказать заказ пожалуйста мне для размер размера размеры өлшем өлшемі доставка завтра сегодня ертең бүгін және и с в на по шт штук штуки пары пару штук комплект цена тенге тг адрес телефон имя мені керек беріңіз қалып размером".split())
SPOKEN_SIZE = re.compile(r"\b(тридцать|сорок|пятьдесят|шестьдесят)\s+(один|одного|два|две|три|четыре|пять|шесть|семь|восемь|девять|перв\w*|втор\w*|трет\w*|четверт\w*|пят\w*|шест\w*|седьм\w*|восьм\w*|девят\w*)\s+размер\w*\b")


def spoken_size(text: str) -> str:
    match = SPOKEN_SIZE.search(normalized(text))
    if not match:
        return ""
    unit = next((n for stems, n in ((('один', 'одного', 'перв'), 1), (('два', 'две', 'втор'), 2),
        (('три', 'трет'), 3), (('четыре', 'четверт'), 4), (('пять', 'пят'), 5), (('шесть', 'шест'), 6),
        (('семь', 'седьм'), 7), (('восемь', 'восьм'), 8), (('девять', 'девят'), 9))
        if match.group(2).startswith(stems)), 0)
    return str({"тридцать": 30, "сорок": 40, "пятьдесят": 50, "шестьдесят": 60}[match.group(1)] + unit)


def normalized(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold().replace("ё", "е")


def colors(text: str) -> set[str]:
    words = re.findall(r"[^\W\d_]+", normalized(text))
    # Prefer the longest prefix, e.g. қызғылтсары (orange) over қызғылт (pink).
    result = set()
    for word in words:
        hits = [(len(prefix), key) for key, prefixes in COLORS.items() for prefix in prefixes if word.startswith(prefix)]
        if hits:
            result.add(max(hits)[1])
    return result


def tokens(text: str) -> set[str]:
    result = set()
    for word in re.findall(r"[^\W\d_]+", normalized(text)):
        if len(word) < 3 or word in STOP or colors(word):
            continue
        alias = next((key for key, roots in PRODUCT_WORDS.items() if any(word.startswith(root) for root in roots)), None)
        result.add(alias or word[:5])
    return result


def local_fields(text: str) -> dict:
    phone_match = PATTERNS[0][0].search(text)
    phone = ""
    if phone_match:
        digits = re.sub(r"\D", "", phone_match.group())
        phone = "+7" + digits[1:]
    name_match = re.search(r"(?:\bимя\s*:|меня зовут|\bатым\s*[:—-]?|аты-жөнім\s*:)\s*([^,;\n]{1,80})", text, re.I)
    address_match = re.search(r"(?:адрес|мекенжай)\s*[:—-]\s*([^;\n]{1,300})", text, re.I)
    if not address_match:
        address_match = re.search(r"((?:ул\.|улица|көшесі|пр\.|проспект|мкр\.?|микрорайон)\s+[^;\n]{1,250})", text, re.I)
    address = address_match.group(1).strip() if address_match else ""
    address = re.split(r",\s*(?:достав\w*|телефон|имя)\b", address, flags=re.I)[0]
    name = name_match.group(1).strip() if name_match else ""
    buyer = {"name": name, "phone": phone, "address": address,
             "lang": orchestrator.reply_lang(orchestrator.detect_lang(text))}
    low = normalized(text)
    pickup = bool(re.search(r"самовывоз|алып\s+кет", low))
    window = ""
    relative = re.search(r"\b(послезавтра|завтра|сегодня|ертең|бүгін)\b", low)
    if relative:
        days = {"послезавтра": 2, "завтра": 1, "ертең": 1, "сегодня": 0, "бүгін": 0}[relative.group()]
        window = f"{relative.group()} ({(db.now().date() + timedelta(days=days)).strftime('%d.%m.%Y')})"
        time = re.search(r"\b\d{1,2}:\d{2}(?:\s*[-–]\s*\d{1,2}:\d{2})?\b", text)
        if time:
            window += " " + time.group()
    else:
        delivery = re.search(r"(?:достав\w*|жеткіз\w*)\s*[:—-]?\s+([^;\n,]{1,100})", text, re.I)
        if delivery:
            window = delivery.group(1).strip()
    zone = "Самовывоз" if pickup else next((z for stem, z in (
        ("байзак", "Байзакский район"), ("жамбылск", "Жамбылский район"),
        ("таласск", "Таласский район"), ("тараз", "Тараз")) if stem in low), "")
    return {"buyer": buyer, "delivery_window": window, "delivery_zone": zone, "pickup": pickup}


def requested_quantity(text: str) -> tuple[int, bool]:
    low = SPOKEN_SIZE.sub("", normalized(text))
    explicit = re.search(r"(?:количество|кол-во|саны)\s*[:=]?\s*(\d+)\b|\b(\d+)\s*(?:шт\b|штук|штуки|пары|пару|пар\b|дана|жұп)", low)
    if explicit:
        return int(explicit.group(1) or explicit.group(2)), False
    leading = re.match(r"\s*(?:нужно|нужны|хочу|мне|керек)?\s*(\d+)\s+([^\W\d_]+)", low)
    if leading and leading.group(2) not in ("размер", "размера", "өлшем", "өлшемі"):
        return int(leading.group(1)), False
    for word, count in NUMBERS.items():
        if re.search(rf"\b{word}\b", low):
            return count, False
    return 1, True


def requested_size(text: str) -> str:
    low = normalized(text)
    found = re.search(r"(?:размер\w*|өлшем\w*)\s*[:—-]?\s*(\d{1,3}|xxxl|xxl|xl|xs|s|m|l)\b|\b(\d{1,3}|xxxl|xxl|xl|xs|s|m|l)\s*(?:-?\s*размер\w*|-?\s*өлшем\w*)", low)
    if found:
        return (found.group(1) or found.group(2)).upper()
    letter = re.search(r"\b(?:xxxl|xxl|xl|xs|s|m|l)\b", low)
    if letter:
        return letter.group().upper()
    voice_size = spoken_size(text)
    if voice_size:
        return voice_size
    # A bare apparel/shoe size is distinct from an explicitly labelled quantity.
    bare = re.search(r"\b(3[0-9]|4[0-9]|5[0-9]|6[0-4])\b(?!\s*(?:шт|штук|дана|пар))", low)
    return bare.group() if bare else ""


def size_matches(label: str, size: str) -> bool:
    if not size:
        return True
    if re.search(rf"(?<!\w){re.escape(normalized(size))}(?!\w)", normalized(label)):
        return True
    if size.isdigit():
        return any(int(a) <= int(size) <= int(b) for a, b in re.findall(r"\b(\d+)\s*[-–]\s*(\d+)\b", label))
    return False


def eligible_variants(p: dict, size: str, requested_colors: set[str]) -> list[str]:
    options = p["variant_options"]
    result = []
    for label in options:
        available_colors = colors(label) or colors(p["passport"]["data"].get("color") or "")
        if size_matches(label, size) and (not requested_colors or requested_colors <= available_colors):
            result.append(label)
    return result


def rank_products(text: str, catalog: list[dict]) -> list[dict]:
    query = tokens(text)
    ranked = []
    for p in catalog:
        data = p["passport"]["data"]
        product_tokens = tokens(" ".join(str(data.get(k) or "") for k in ("title_ru", "title_kk", "brand", "model")))
        score = sum(3 if t in PRODUCT_WORDS else 1 for t in query & product_tokens)
        if re.search(rf"(?<!\w){re.escape(p['sku'])}(?!\w)", text, re.I):
            score += 100
        if score:
            ranked.append({"product": p, "score": score})
    return sorted(ranked, key=lambda x: (-x["score"], x["product"]["sku"]))


def fallback_lines(text: str) -> list[dict]:
    parts = re.split(r"[;\n]+|,\s*(?!\d)|\s+(?:и|және)\s+", text)
    ignored = r"^(?:достав\w*|жеткіз\w*|самовывоз|адрес|мекенжай|имя|телефон|меня зовут|атым|аты-жөнім|сегодня|завтра|ертең|бүгін|тараз)\b|^\["
    lines = [{"text": part.strip()[:500]} for part in parts if part.strip() and not re.match(ignored, part.strip(), re.I)
             and re.search(r"[^\W\d_]", part) and not re.fullmatch(r"[\d\s+:()–.-]+", part)]
    return lines[:20] or [{"text": text[:500]}]


def preview_line(text: str, catalog: list[dict]) -> dict:
    qty, inferred = requested_quantity(text)
    size, wanted_colors = requested_size(text), colors(text)
    ranked = rank_products(text, catalog)
    row = {"request": text, "sku": None, "variant": None, "qty": qty, "size": size,
           "requested_colors": sorted(wanted_colors), "price": None, "available": None, "errors": [], "warnings": [], "candidates": []}
    if inferred:
        row["warnings"].append("Количество не указано — предложена 1 шт., проверьте.")
    if qty < 1 or qty > 1000:
        row["errors"].append("Уточните количество: от 1 до 1000 шт.")
    if not ranked:
        row["errors"].append("Товар не найден в каталоге — выберите его вручную.")
        return row
    best = [r["product"] for r in ranked if r["score"] == ranked[0]["score"]]
    if len(best) > 1 and (size or wanted_colors):
        fitting = [p for p in best if eligible_variants(p, size, wanted_colors) or
                   (not p["variant_options"] and not size and wanted_colors <= colors(p["passport"]["data"].get("color") or ""))]
        if fitting:
            best = fitting
    row["candidates"] = [{"sku": r["product"]["sku"], "title": r["product"]["passport"]["data"].get("title_ru"),
                          "price": r["product"]["passport"]["data"].get("price"), "free": r["product"]["free"]} for r in ranked[:5]]
    if len(best) != 1:
        row["errors"].append("Подходит несколько товаров — выберите нужную модель.")
        return row
    p = best[0]
    data = p["passport"]["data"]
    row.update(sku=p["sku"], title=data.get("title_ru"), color=data.get("color") or "", price=data.get("price"))
    if type(row["price"]) is not int or row["price"] <= 0:
        row["errors"].append("Укажите цену товара в каталоге.")
    options = eligible_variants(p, size, wanted_colors)
    if p["variant_options"]:
        if len(options) == 1:
            row["variant"] = options[0]
        elif not options:
            row["errors"].append("Запрошенный размер / цвет не найден у этого товара.")
        else:
            row["errors"].append("Уточните размер / цвет: подходит несколько вариантов.")
    elif size:
        row["errors"].append("У товара не настроены размеры — проверьте выбранный размер вручную.")
    elif wanted_colors and not wanted_colors <= colors(data.get("color") or ""):
        row["errors"].append("Запрошенный цвет не подтверждён карточкой товара.")
    if p["variant_options"] and not p["inventory_variants"]:
        row["warnings"].append("Остаток общий: наличие конкретного размера продавец проверяет вручную.")
    row["stock_scope"] = "common" if p["variant_options"] and not p["inventory_variants"] else "variant" if p["inventory_variants"] else "product"
    v = next((v for v in p["inventory_variants"] if v["label"] == row["variant"]), None)
    row["free"] = v["free"] if v else p["free"]
    if not row["errors"]:
        row["available"] = row["free"] >= qty
        if not row["available"]:
            row["errors"].append(f"Недостаточно остатка: запрошено {qty}, доступно {row['free']}.")
    return row


def create_draft(text: str, channel: str = "whatsapp", source_kind: str = "text") -> dict:
    if channel not in orders.CHANNELS or channel == "kaspi":
        raise CoreError("Выберите канал соцсетей, OLX или сайта")
    text = text.strip()
    if not text or len(text) > 6000:
        raise CoreError("Сообщение должно содержать от 1 до 6000 символов")
    fields = local_fields(text)
    safe_text = redact(text, fields["buyer"])
    with db.read() as c:
        catalog = [passport.get_product(c, r[0]) for r in c.execute("SELECT id FROM products ORDER BY id")]
    heuristic = {"items": fallback_lines(safe_text)}
    shortlist = rank_products(safe_text, catalog)[:30]
    result, model = orchestrator._call("order_intake", {
        "facts": {"catalog": [{"sku": x["product"]["sku"], "title_ru": x["product"]["passport"]["data"].get("title_ru"),
                               "title_kk": x["product"]["passport"]["data"].get("title_kk")} for x in shortlist]},
        "buyer_message": safe_text, "reply_lang": "ru",
        "instructions": "Раздели сообщение на запрошенные товарные позиции. Для каждой скопируй точный фрагмент сообщения в text, "
                        "включая количество, размер и цвет. Не включай контактные данные и доставку. Не выбирай SKU и не придумывай "
                        "товары, количество, цены или оплату. Каждая позиция, включая неизвестный товар, должна остаться в списке.",
        "output_schema": {"items": [{"text": "точный фрагмент buyer_message"}]},
    }, heuristic)
    warnings = []
    try:
        lines = ExtractedLines.model_validate(result).items
        if any(normalized(line.text) not in normalized(safe_text) for line in lines):
            raise ValueError("Ungrounded item excerpt")
        covered = tokens(" ".join(line.text for line in lines))
        if any(not tokens(line["text"]) <= covered for line in heuristic["items"]):
            raise ValueError("Model omitted a requested item")
    except (ValueError, TypeError):
        lines = ExtractedLines.model_validate(heuristic).items
        result = {"_source": "fallback"}
        model = "fallback-heuristic"
        warnings.append("Ответ модели не удалось проверить — использован локальный разбор.")
    rows = [preview_line(line.text, catalog) for line in lines]
    # Availability is checked for the complete basket, including repeated lines.
    totals = Counter()
    common_stock = {p["sku"] for p in catalog if not p["inventory_variants"]}
    def stock_key(row):
        return (row["sku"], None if row["sku"] in common_stock else row["variant"])
    for row in rows:
        if row["sku"]:
            totals[stock_key(row)] += row["qty"]
    for row in rows:
        if row["sku"] and row["available"] and totals[stock_key(row)] > row["free"]:
            row["available"] = False
            row["errors"].append("Суммарное количество повторяющихся позиций превышает остаток.")
    missing = []
    for key, label in (("name", "Имя покупателя"), ("phone", "Телефон покупателя"), ("address", "Адрес доставки")):
        if not fields["buyer"][key] and not (key == "address" and fields["pickup"]):
            missing.append({"field": "buyer." + key, "label": label, "required": key == "name"})
    if not fields["delivery_window"]:
        missing.append({"field": "delivery_window", "label": "Желаемый день / время получения", "required": False})
    if not fields["delivery_zone"]:
        missing.append({"field": "delivery_zone", "label": "Город / зона доставки", "required": False})
    output = {**fields, "items": rows, "channel": channel, "missing_fields": missing,
              "missing": [x["label"] for x in missing], "warnings": warnings,
              "source_kind": source_kind, "_source": result.get("_source", "fallback")}
    with db.tx() as c:
        saved = orchestrator._save_draft(c, "order_intake", output, None, {}, model)
    return {**output, "draft_id": saved["draft_id"], "model": model, "source": output["_source"],
            "transcript": text if source_kind == "voice" else None}
