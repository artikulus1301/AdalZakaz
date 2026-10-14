"""Правила возврата. Версионируются и утверждаются юристом; ИИ их не меняет и не обходит.
Значения по умолчанию взяты из публикаций finance.kz и inbusiness.kz и ДОЛЖНЫ быть
сверены юристом с текстом закона перед запуском."""
import sqlite3
from datetime import date

from .. import db
from . import CoreError

DEFAULT_RULES = {
    "return_days_non_food": 14,
    "claim_response_days": 10,
    "defect_claim_days_no_warranty": 730,
    "return_conditions_ru": [
        "товар не был в употреблении",
        "сохранён товарный вид",
        "сохранены пломбы и ярлыки",
    ],
    "return_conditions_kk": [
        "тауар пайдаланылмаған",
        "тауарлық түрі сақталған",
        "пломбалары мен жапсырмалары сақталған",
    ],
    "excluded_categories": {
        "medicine": "Лекарственные средства и медицинские изделия (ст. 30)",
        "underwear": "Нательное бельё (ст. 30)",
        "hosiery": "Чулочно-носочные изделия (ст. 30)",
        "metered": "Отдельные метражные товары (ст. 30) — проверить по перечню",
        "mobile_verified": "Верифицированные мобильные устройства (ст. 30)",
    },
    "kaspi": {"unopened_days": 14, "defect_days": 30},
    "escalation_ru": "Территориальный департамент по защите прав потребителей, в том числе через E-otinish",
    "escalation_kk": "Тұтынушылардың құқықтарын қорғау жөніндегі аумақтық департамент, оның ішінде E-otinish арқылы",
}

DECISIONS = {
    "refund": "Расторжение договора и возврат денег",
    "replace": "Замена на такой же товар",
    "repair": "Безвозмездное устранение недостатков",
    "price_reduction": "Соразмерное уменьшение цены",
    "exchange": "Обмен на аналогичный товар",
    "redeliver": "Повторная доставка / досыл",
    "refusal": "Мотивированный письменный отказ",
}

CLAIM_TYPES = {
    "not_as_described": ("Не соответствует описанию", "Сипаттамаға сәйкес емес"),
    "defect": ("Брак / недостаток", "Ақау"),
    "changed_mind": ("Не подошёл / передумал", "Сәйкес келмеді / ойымнан айныдым"),
    "delivery": ("Проблема доставки / некомплект", "Жеткізу / толық емес"),
}


def active(c: sqlite3.Connection) -> dict:
    r = c.execute("SELECT * FROM rules ORDER BY version DESC LIMIT 1").fetchone()
    if not r:
        c.execute(
            "INSERT INTO rules(version, data, created_at) VALUES (1, ?, ?)", (db.dumps(DEFAULT_RULES), db.now_iso())
        )
        r = c.execute("SELECT * FROM rules ORDER BY version DESC LIMIT 1").fetchone()
    out = dict(r)
    out["data"] = db.loads(out["data"], {})
    out["approved"] = bool(out["approved_by"])
    return out


def new_version(c: sqlite3.Connection, data: dict) -> int:
    cur = active(c)
    for key in ("return_days_non_food", "claim_response_days", "defect_claim_days_no_warranty"):
        if not isinstance(data.get(key), int) or data[key] <= 0:
            raise CoreError(f"Поле {key} должно быть положительным целым числом")
    v = cur["version"] + 1
    c.execute("INSERT INTO rules(version, data, created_at) VALUES (?, ?, ?)", (v, db.dumps(data), db.now_iso()))
    return v


def approve(c: sqlite3.Connection, version: int, lawyer: str) -> None:
    if not lawyer.strip():
        raise CoreError("Укажите ФИО юриста")
    cur = c.execute(
        "UPDATE rules SET approved_by = ?, approved_at = ? WHERE version = ? AND approved_by IS NULL",
        (lawyer.strip(), db.now_iso(), version),
    )
    if cur.rowcount == 0:
        raise CoreError("Версия не найдена или уже утверждена", status=409)


def evaluate(
    rules: dict,
    claim_type: str | None,
    category: str,
    channel: str,
    delivered_at: date | None,
    received_at: date,
    condition_ok: bool | None,
) -> dict:
    """Какие решения допустимы по правилам. Решение всё равно принимает человек."""
    r = rules["data"]
    days = (received_at - delivered_at).days if delivered_at else None
    out = {
        "claim_type": claim_type,
        "days_since_delivery": days,
        "options": [],
        "refusal_allowed": False,
        "refusal_basis": None,
        "basis": [],
        "checks": [],
        "warnings": [],
    }
    if not rules.get("approved"):
        out["warnings"].append("Правила возврата не утверждены юристом — решение принимать с осторожностью.")
    if claim_type is None:
        out["warnings"].append("Тип претензии не подтверждён человеком.")
        return out

    if claim_type in ("not_as_described", "defect"):
        limit = r["defect_claim_days_no_warranty"]
        out["options"] = ["repair", "price_reduction", "replace", "refund"]
        out["basis"].append(
            "При недостатке или несоответствии покупатель вправе требовать устранения недостатков, "
            "уменьшения цены, замены или расторжения договора с возвратом денег."
        )
        if days is not None and days > limit:
            out["refusal_allowed"] = True
            out["refusal_basis"] = f"Истёк срок предъявления требований без гарантии ({limit // 365} года) — проверить гарантию."
        out["checks"].append("Сверить претензию с версией паспорта, по которой продан товар, и с видео упаковки.")
    elif claim_type == "changed_mind":
        excl = r["excluded_categories"].get(category)
        if excl:
            out["refusal_allowed"] = True
            out["refusal_basis"] = f"Товар не подлежит возврату надлежащего качества: {excl}."
        elif days is not None and days > r["return_days_non_food"]:
            out["refusal_allowed"] = True
            out["refusal_basis"] = f"Истёк срок {r['return_days_non_food']} календарных дней для возврата исправного товара."
        elif condition_ok is False:
            out["refusal_allowed"] = True
            out["refusal_basis"] = "Товар утратил товарный вид / был в употреблении / нет пломб и ярлыков (зафиксировано в журнале доказательств)."
        else:
            out["options"] = ["exchange", "refund"]
            out["basis"].append(
                f"Исправный непродовольственный товар можно вернуть или обменять в течение "
                f"{r['return_days_non_food']} календарных дней при условиях: " + "; ".join(r["return_conditions_ru"]) + "."
            )
            if condition_ok is None:
                out["checks"].append("Проверить и зафиксировать состояние товара (фото/видео) — от этого зависит право на отказ.")
    elif claim_type == "delivery":
        out["options"] = ["redeliver", "refund", "replace"]
        out["basis"].append("Товар не передан или передан не полностью — обязательство продавца не исполнено.")
        out["checks"].append("Сверить с видео упаковки и подтверждением комплекта при выдаче.")

    if channel == "kaspi":
        k = r["kaspi"]
        out["warnings"].append(
            f"Заказ Kaspi: возврат идёт через процесс Kaspi ({k['unopened_days']} дней при невскрытой упаковке, "
            f"{k['defect_days']} дней или гарантия — для некачественного). Решение здесь — для журнала."
        )
    return out


def validate_decision(evaluation: dict, decision: str, text: str) -> str:
    if decision not in DECISIONS:
        raise CoreError("Неизвестное решение")
    if decision == "refusal":
        if not evaluation["refusal_allowed"]:
            raise CoreError(
                "Отказ по этой претензии правилами не предусмотрен. Выберите одно из допустимых решений "
                "или измените подтверждённый тип/состояние товара.",
                status=409,
                code="refusal_not_allowed",
            )
        if len((text or "").strip()) < 40:
            raise CoreError("Отказ должен быть мотивированным: опишите основание (не короче 40 символов).")
        return evaluation["refusal_basis"]
    if decision not in evaluation["options"]:
        raise CoreError("Это решение не входит в допустимые для данного типа претензии", status=409)
    return " ".join(evaluation["basis"])
