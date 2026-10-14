"""Проверка черновиков ИИ по фактам ядра до того, как их увидит человек.
Не блокирует отправку молча — показывает продавцу, что именно не так."""
import re

REFUSAL_PHRASES = [
    "возврату не подлежит", "не подлежит возврату", "возврат невозможен", "вернуть нельзя",
    "қайтарылмайды", "қайтаруға жатпайды", "қайтару мүмкін емес",
]
MONEY_PROMISES = [
    "вернём деньги", "вернем деньги", "деньги вернём", "деньги вернем", "оформим возврат денег",
    "ақшаңызды қайтарамыз", "ақшаны қайтарамыз",
]
PRICE_RX = re.compile(r"(\d[\d\s]{2,})\s*(?:тг|тенге|₸|теңге)", re.I)


def check(text: str, facts: dict) -> list[dict]:
    """facts: prices (list[int]), refusal_allowed (bool), warranty_months (list[int])."""
    issues: list[dict] = []
    low = (text or "").lower()
    if not facts.get("refusal_allowed"):
        for p in REFUSAL_PHRASES:
            if p in low:
                issues.append({"severity": "block", "message": f"Черновик содержит отказ («{p}»), а правила его не допускают."})
                break
    for p in MONEY_PROMISES:
        if p in low:
            issues.append({"severity": "warn", "message": "Черновик обещает возврат денег — это решение человека, не ИИ."})
            break
    allowed = {int(x) for x in facts.get("prices", []) if x}
    for m in PRICE_RX.finditer(text or ""):
        val = int(re.sub(r"\s", "", m.group(1)))
        if allowed and val not in allowed:
            issues.append({"severity": "block", "message": f"Цена {val} тг не совпадает с ядром ({', '.join(map(str, sorted(allowed)))} тг)."})
    for m in re.finditer(r"гаранти\w*\s*(\d+)\s*(мес|год|лет)", low):
        n = int(m.group(1)) * (12 if m.group(2) in ("год", "лет") else 1)
        if n not in set(facts.get("warranty_months", [])):
            issues.append({"severity": "block", "message": f"Гарантия {m.group(0)} не совпадает с паспортом товара."})
    return issues
