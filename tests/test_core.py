"""Тесты ядра и защитных механизмов. Запуск: .venv\\Scripts\\python -m pytest -q"""
import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ["ADAL_DATA_DIR"] = _tmp
os.environ["ADAL_DB"] = os.path.join(_tmp, "test.db")
os.environ["QWEN_API_KEY"] = ""
os.environ["LLM_API_KEY"] = ""
os.environ["ADAL_SEED_DEMO"] = "0"

from datetime import date, timedelta  # noqa: E402

import pytest  # noqa: E402

from app import db  # noqa: E402
from app.ai import guard, orchestrator  # noqa: E402
from app.ai.redact import redact  # noqa: E402
from app.core import CoreError, OutOfStock, claims, evidence, orders, passport, rules  # noqa: E402
from app.connectors import manual  # noqa: E402

FULL = {"title_ru": "Платье", "title_kk": "Көйлек", "desc_ru": "Платье", "desc_kk": "Көйлек", "brand": "B",
        "country": "KZ", "composition": "хлопок", "dimensions": "110 см", "size_grid": "M: 88-92 см", "color": "синий",
        "price": 10000}


@pytest.fixture(autouse=True)
def fresh_db():
    if os.path.exists(os.environ["ADAL_DB"]):
        os.remove(os.environ["ADAL_DB"])
    db.init()
    yield


def make_product(sku="P1", stock=1, category="clothing", data=None):
    with db.tx() as c:
        return passport.create_product(c, sku, category, stock, data or FULL, "t")


def make_order(sku="P1", qty=1, channel="instagram"):
    return manual.submit({"channel": channel, "buyer": {"name": "Тест", "phone": "+7 701 111 22 33"},
                          "items": [{"sku": sku, "qty": qty}]}, "t")


def deliver(oid):
    with db.tx() as c:
        o = orders.get(c, oid)
        orders.confirm_payment(c, oid, "bank_statement", f"R{oid}", o["total"], "t")
        st = evidence.store_file(b"video", "pack.mp4", "video/mp4")
        evidence.add(c, "packing_video", st, "t", order_id=oid)
        orders.set_status(c, oid, "assembled", "t")
        orders.confirm_address(c, oid, "Тараз", "Тараз", "10-14", "t")
        orders.set_status(c, oid, "shipped", "t")
        orders.set_status(c, oid, "delivered", "t")


def test_required_fields_and_versions():
    with db.tx() as c:
        pid = passport.create_product(c, "X", "clothing", 1, {"title_ru": "Платье"}, "t")
        v1 = passport.get_version(c, pid)
        assert not v1["check_result"]["ok"]
        assert {"brand", "composition", "size_grid", "title_kk"} <= {m["field"] for m in v1["check_result"]["missing"]}
        passport.save_version(c, pid, FULL, "fill", "t")
        assert passport.get_version(c, pid)["check_result"]["ok"]
        assert passport.get_version(c, pid, 1)["data"]["title_ru"] == "Платье"  # старая версия неизменна


def test_single_stock_prevents_double_sale():
    make_product(stock=1)
    assert make_order()["status"] == "done"
    r = make_order(channel="whatsapp")
    assert r["status"] == "rejected_no_stock"
    with db.read() as c:
        p = c.execute("SELECT stock, reserved FROM products").fetchone()
        assert (p["stock"], p["reserved"]) == (1, 1)


def test_cancel_releases_reserve():
    make_product(stock=1)
    oid = make_order()["order_id"]
    with db.tx() as c:
        orders.set_status(c, oid, "cancelled", "t", "buyer_request")
    assert make_order()["status"] == "done"


def test_screenshot_is_not_payment_and_payment_idempotent():
    make_product(stock=2)
    a = make_order()["order_id"]
    b = make_order()["order_id"]
    with db.tx() as c:
        with pytest.raises(CoreError):
            orders.confirm_payment(c, a, "screenshot", "img", 10000, "t")
        assert orders.confirm_payment(c, a, "bank_statement", "OP-1", 10000, "t") is True
        assert orders.confirm_payment(c, a, "bank_statement", "OP-1", 10000, "t") is False  # повтор
        with pytest.raises(CoreError):
            orders.confirm_payment(c, b, "bank_statement", "OP-1", 10000, "t")  # та же операция на другой заказ


def test_payment_amount_must_match():
    make_product()
    a = make_order()["order_id"]
    with db.tx() as c, pytest.raises(CoreError):
        orders.confirm_payment(c, a, "bank_statement", "OP-2", 9000, "t")


def test_assembly_requires_packing_evidence():
    make_product()
    oid = make_order()["order_id"]
    with db.tx() as c:
        orders.confirm_payment(c, oid, "bank_statement", "OP-3", 10000, "t")
        with pytest.raises(CoreError) as e:
            orders.set_status(c, oid, "assembled", "t")
        assert e.value.code == "need_packing_evidence"


def test_claim_deadline_and_refund_once():
    make_product()
    oid = make_order()["order_id"]
    deliver(oid)
    with db.tx() as c:
        cid = claims.create(c, oid, "Цвет не соответствует фото", "buyer")
        cl = claims.get(c, cid)
        assert cl["deadline"] == (db.now().date() + timedelta(days=10)).isoformat()
        with pytest.raises(CoreError):
            claims.decide(c, cid, "refund", "x", "t")  # тип не подтверждён человеком
        claims.confirm(c, cid, "not_as_described", None, "color", "t")
        with pytest.raises(CoreError) as e:
            claims.decide(c, cid, "refusal", "Отказываем, потому что так решили, без каких-либо оснований вообще", "t")
        assert e.value.code == "refusal_not_allowed"
        claims.decide(c, cid, "refund", "Возврат", "t", refund_amount=10000)
        assert claims.decide(c, cid, "refund", "Возврат", "t")["repeated"] is True
        assert c.execute("SELECT COUNT(*) FROM refunds").fetchone()[0] == 1


def test_rules_changed_mind():
    r = {"data": rules.DEFAULT_RULES, "approved": True}
    today = date(2026, 10, 14)
    ev = rules.evaluate(r, "changed_mind", "clothing", "olx", today - timedelta(days=5), today, None)
    assert ev["options"] == ["exchange", "refund"] and not ev["refusal_allowed"]
    ev = rules.evaluate(r, "changed_mind", "clothing", "olx", today - timedelta(days=15), today, None)
    assert ev["refusal_allowed"]
    ev = rules.evaluate(r, "changed_mind", "underwear", "olx", today - timedelta(days=1), today, None)
    assert ev["refusal_allowed"] and "бельё" in ev["refusal_basis"]
    ev = rules.evaluate(r, "defect", "underwear", "olx", today - timedelta(days=1), today, None)
    assert not ev["refusal_allowed"] and "refund" in ev["options"]  # брак — права сохраняются и для исключений


def test_claim_idempotent_by_key():
    make_product()
    oid = make_order()["order_id"]
    deliver(oid)
    with db.tx() as c:
        a = claims.create(c, oid, "брак", "buyer", idem_key="k1")
        b = claims.create(c, oid, "брак", "buyer", idem_key="k1")
        assert a == b


def test_guard_blocks_wrong_price_and_refusal():
    issues = guard.check("Цена 9 990 тг, возврату не подлежит", {"prices": [10000], "refusal_allowed": False})
    msgs = " ".join(i["message"] for i in issues)
    assert "9990" in msgs and "отказ" in msgs
    assert guard.check("Цена 10 000 тг", {"prices": [10000]}) == []


def test_redact():
    out = redact("Меня зовут Айгерім, тел +7 701 234 56 78, ул. Абая 7, кв. 5, ИИН 990101300123",
                 {"name": "Айгерім Сейітқызы"})
    for leaked in ("Айгерім", "701 234", "Абая", "990101300123"):
        assert leaked not in out


def test_lang_and_claim_classification():
    assert orchestrator.detect_lang("Шұлық ұнамады") == "kk"
    assert orchestrator.detect_lang("Размер не подошёл") == "ru"
    assert orchestrator.classify_claim_heuristic("Шұлық ұнамады, өлшемі сәйкес келмеді")[0] == "changed_mind"
    assert orchestrator.classify_claim_heuristic("Наушник не работает")[0] == "defect"
    assert orchestrator.classify_claim_heuristic("Цвет не такой как на фото")[0] == "not_as_described"


def test_shadow_mode_blocks_sending():
    make_product()
    oid = make_order()["order_id"]
    with db.tx() as c:
        db.set_setting(c, "ai_mode", "shadow")
        d = orchestrator.reply_draft(c, oid, "Какой состав?")
        with pytest.raises(CoreError):
            orchestrator.review_draft(c, d["draft_id"], "send", None, "t")
        assert orchestrator.review_draft(c, d["draft_id"], "approve", "Состав: хлопок.", "t")["edited"]
