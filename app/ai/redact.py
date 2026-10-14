"""Обезличивание перед отправкой в модель. Удалить имя мало — адрес и телефон тоже идентифицируют."""
import re

PATTERNS = [
    (re.compile(r"(?:\+?7|8)[\s\-(]*7\d{2}[\s\-)]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}"), "[ТЕЛЕФОН]"),
    (re.compile(r"\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b"), "[КАРТА]"),
    (re.compile(r"\b\d{12}\b"), "[ИИН]"),
    (re.compile(r"\bKZ\d{2}[A-Z0-9]{13,17}\b", re.I), "[IBAN]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[EMAIL]"),
    (re.compile(r"(?:ул\.|улица|көшесі|пр\.|проспект|мкр\.?|микрорайон)\s*[\w\-]+[^\n,.;]*", re.I), "[АДРЕС]"),
    (re.compile(r"(?<!\w)(?:д|дом|кв|үй|пәтер)\.?\s*\d+\w*", re.I), "[АДРЕС]"),
]


def redact(text: str | None, known: dict | None = None) -> str:
    if not text:
        return ""
    out = text
    for key, label in (("address", "[АДРЕС]"), ("phone", "[ТЕЛЕФОН]"), ("name", "[ИМЯ]")):
        val = (known or {}).get(key)
        if not val:
            continue
        for part in ([val] + (val.split() if key == "name" else [])):
            if len(part) >= 3:
                out = re.sub(re.escape(part), label, out, flags=re.I)
    for rx, label in PATTERNS:
        out = rx.sub(label, out)
    return out
