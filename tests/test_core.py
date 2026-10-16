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
from app.core import CoreError, OutOfStock, claims, evidence, inventory, metrics, orders, passport, rules  # noqa: E402
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


def intake_shoes(sku="SN", white=2, black=3):
    pid = make_product(sku, category="shoes", data={**FULL, "title_ru": "Кроссовки беговые", "title_kk": "Жеңіл кроссовкалар", "color": "белый", "variants": "42 / белый, 42 / чёрный", "price": 18500})
    with db.tx() as c:
        inventory.set_variants(c, pid, [{"label": "42 / белый", "stock": white}, {"label": "42 / чёрный", "stock": black}])
    return pid


def intake_client():
    from fastapi.testclient import TestClient
    from app.main import app, SESSION
    client = TestClient(app)
    client.cookies.set("adal_session", SESSION)
    return client


def test_message_draft_uses_catalog_and_does_not_reserve():
    from app.ai.order_intake import create_draft
    intake_shoes()
    r = create_draft("Нужны белые кроссовки 42 размера, доставка завтра")
    item, = r["items"]
    assert (item["sku"], item["variant"], item["qty"], item["price"], item["available"]) == ("SN", "42 / белый", 1, 18500, True)
    assert "завтра" in r["delivery_window"]
    assert {"buyer.name", "buyer.phone", "buyer.address"} <= {m["field"] for m in r["missing_fields"]}
    with db.read() as c:
        assert c.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
        assert c.execute("SELECT SUM(reserved) FROM products").fetchone()[0] == 0


def test_spoken_size_is_distinct_from_quantity():
    from app.ai.order_intake import preview_line
    pid = intake_shoes()
    with db.read() as c:
        item = preview_line("Нужны две пары белых кроссовок сорок второго размера", [passport.get_product(c, pid)])
    assert (item["qty"], item["variant"]) == (2, "42 / белый")


def test_intake_kazakh_multiple_items_and_local_contacts():
    from app.ai.order_intake import create_draft
    intake_shoes(white=4)
    make_product("DR", stock=4)
    r = create_draft("Ақ кроссовки 42 өлшем 2 дана; көк көйлек 1 дана; Атым: Алия; телефон: +7 701 123 45 67; адрес: Тараз, Абая 10; жеткізу ертең")
    assert [(x["sku"], x["qty"]) for x in r["items"]] == [("SN", 2), ("DR", 1)]
    assert r["buyer"]["name"] == "Алия"
    assert r["buyer"]["phone"] == "+77011234567"
    assert r["buyer"]["address"] == "Тараз, Абая 10"
    assert r["buyer"]["lang"] == "kk"


@pytest.mark.parametrize("message", ["кроссовки 45 размера белые", "кроссовки 42 размера красные"])
def test_intake_does_not_substitute_unavailable_variant(message):
    from app.ai.order_intake import create_draft
    intake_shoes()
    item = create_draft(message)["items"][0]
    assert item["variant"] is None and item["errors"] and item["available"] is None


def test_intake_ambiguous_and_unknown_products_stay_visible():
    from app.ai.order_intake import create_draft
    intake_shoes("A")
    intake_shoes("B")
    rows = create_draft("белые кроссовки 42 размера; пылесос 1 шт")["items"]
    assert len(rows) == 2
    assert rows[0]["sku"] is None and len(rows[0]["candidates"]) == 2
    assert rows[1]["sku"] is None and rows[1]["errors"]


def test_intake_checks_full_basket_stock():
    from app.ai.order_intake import create_draft
    intake_shoes(white=1)
    rows = create_draft("белые кроссовки 42 размера; белые кроссовки 42 размера")["items"]
    assert all(x["available"] is False for x in rows)
    make_product("DR", stock=1, data={**FULL, "variants": "M, L"})
    rows = create_draft("платье M; платье L")["items"]
    assert all(x["available"] is False and x["warnings"] for x in rows)


def test_intake_rejects_hallucinated_model_excerpt_and_redacts(monkeypatch):
    from app.ai import order_intake
    intake_shoes()
    def fake_call(task, payload, heuristic):
        assert "Алия" not in payload["buyer_message"] and "77011234567" not in payload["buyer_message"]
        assert "Абая" not in payload["buyer_message"]
        return {"items": [{"text": "красные кроссовки 44 размера"}], "_source": "llm"}, "fake"
    monkeypatch.setattr(orchestrator, "_call", fake_call)
    r = order_intake.create_draft("белые кроссовки 42 размера; имя: Алия; телефон: +77011234567; адрес: Абая 10")
    assert r["source"] == "fallback" and r["items"][0]["variant"] == "42 / белый"


def test_intake_confirm_is_explicit_and_idempotent_with_catalog_price():
    intake_shoes()
    with intake_client() as client:
        draft = client.post("/api/ai/order-intake", json={"text": "белые кроссовки 42 размера"}).json()
        body = {"channel": "whatsapp", "buyer": {"name": "Алия"}, "items": [{"sku": "SN", "variant": "42 / белый", "qty": 1, "price": 1}], "delivery_window": "завтра"}
        url = f"/api/ai/order-intake/{draft['draft_id']}/confirm"
        assert client.post(url, json=body).status_code == 422
        assert client.post(url, json={**body, "reviewed": False}).status_code == 422
        first = client.post(url, json={**body, "reviewed": True})
        assert first.status_code == 200, first.text
        second = client.post(url, json={**body, "reviewed": True})
        assert first.json()["order_id"] == second.json()["order_id"]
        order = client.get(f"/api/orders/{first.json()['order_id']}").json()
        assert order["total"] == 18500 and order["address_confirmed"] == 0
        assert order["delivery_window"] == "завтра"
    with db.read() as c:
        assert c.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 1
        assert c.execute("SELECT SUM(reserved) FROM products").fetchone()[0] == 1
        assert c.execute("SELECT status FROM ai_drafts").fetchone()[0] == "approved"


def test_intake_rechecks_stock_and_allows_corrected_retry():
    intake_shoes(white=1)
    with intake_client() as client:
        draft = client.post("/api/ai/order-intake", json={"text": "белые кроссовки 42 размера"}).json()
        manual.submit({"channel": "telegram", "buyer": {}, "items": [{"sku": "SN", "variant": "42 / белый", "qty": 1}]}, "t")
        body = {"channel": "whatsapp", "buyer": {"name": "Алия"}, "items": [{"sku": "SN", "variant": "42 / белый", "qty": 1}], "reviewed": True}
        url = f"/api/ai/order-intake/{draft['draft_id']}/confirm"
        assert client.post(url, json=body).status_code == 409
        with db.read() as c:
            assert c.execute("SELECT status FROM ai_drafts").fetchone()[0] in ("draft", "shadow")
        body["items"][0]["variant"] = "42 / чёрный"
        assert client.post(url, json=body).status_code == 200


def test_voice_intake_with_mocked_recognition_and_invalid_audio(monkeypatch):
    import io
    import wave
    from app.ai import speech
    intake_shoes()
    # Exercise the speech boundary without an external service or a real microphone.
    monkeypatch.setattr(speech, "available", lambda: True)
    monkeypatch.setattr(speech, "transcribe", lambda audio, mime: {"text": "белые кроссовки 42 размера"})
    monkeypatch.setattr(orchestrator, "_call", lambda task, payload, heuristic: ({**heuristic, "_source": "demo"}, "test"))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed")); wav.writeframes(b"\0\0" * 1600)
    with intake_client() as client:
        r = client.post("/api/ai/order-intake/voice", files={"file": ("voice.wav", buf.getvalue(), "audio/wav")})
        assert r.status_code == 200, r.text
        assert r.json()["source_kind"] == "voice" and r.json()["transcript"]
        assert r.json()["items"][0]["variant"] == "42 / белый"
        assert client.post("/api/ai/order-intake/voice", files={"file": ("bad.wav", b"bad", "audio/wav")}).status_code == 400
        monkeypatch.setattr(speech, "transcribe", lambda audio, mime: {"text": ""})
        assert client.post("/api/ai/order-intake/voice", files={"file": ("voice.wav", buf.getvalue(), "audio/wav")}).status_code == 422
    with db.read() as c:
        assert c.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0


def test_intake_auth_and_unconfigured_speech(monkeypatch):
    from app.ai import speech
    monkeypatch.setattr(speech, "available", lambda: False)
    with intake_client() as client:
        assert client.post("/api/ai/order-intake/voice", files={"file": ("voice.wav", b"bad", "audio/wav")}).status_code == 503
        client.cookies.clear()
        assert client.post("/api/ai/order-intake", json={"text": "заказ"}).status_code == 401


def test_speech_refusal_is_not_a_transcript(monkeypatch):
    from app import config
    from app.ai import speech
    monkeypatch.setattr(config, "ASR_PROVIDER", "llm")
    monkeypatch.setattr(speech, "available", lambda: True)
    monkeypatch.setattr(speech.llm, "transcribe", lambda audio, mime: ("Я не могу обработать аудио в этом запросе.", "test"))
    with pytest.raises(CoreError, match="не смогла принять аудио"):
        speech.transcribe(b"audio")


def test_local_speech_output_and_busy_handling(monkeypatch):
    from types import SimpleNamespace
    from app import config
    from app.ai import speech
    monkeypatch.setattr(config, "ASR_PROVIDER", "local")
    monkeypatch.setattr(speech, "available", lambda: True)
    class Model:
        def transcribe(self, audio, **kwargs):
            return iter([SimpleNamespace(text=" Белые кроссовки 42 размера ")]), SimpleNamespace(language="ru")
    monkeypatch.setattr(speech, "local_model", lambda: Model())
    assert speech.transcribe(b"audio")["text"] == "Белые кроссовки 42 размера"
    assert speech._lock.acquire(blocking=False)
    try:
        with pytest.raises(CoreError, match="другая запись"):
            speech.transcribe(b"audio")
    finally:
        speech._lock.release()


def test_concurrent_intake_confirmation_reserves_once():
    from concurrent.futures import ThreadPoolExecutor
    from app.ai.order_intake import create_draft
    intake_shoes(white=1)
    did = create_draft("белые кроссовки 42 размера")["draft_id"]
    payload = {"channel": "whatsapp", "intake_draft_id": did, "buyer": {"name": "Тест"},
               "items": [{"sku": "SN", "variant": "42 / белый", "qty": 1}]}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: manual.submit(payload, "t"), range(2)))
    assert all(r["status"] == "done" for r in results)
    assert results[0]["order_id"] == results[1]["order_id"]
    with db.read() as c:
        assert c.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 1
        assert c.execute("SELECT SUM(reserved) FROM products").fetchone()[0] == 1


def test_intake_model_cannot_silently_omit_unknown_product(monkeypatch):
    from app.ai.order_intake import create_draft
    intake_shoes()
    monkeypatch.setattr(orchestrator, "_call", lambda *args: ({"items": [{"text": "белые кроссовки 42 размера"}], "_source": "llm"}, "test"))
    result = create_draft("белые кроссовки 42 размера; пылесос")
    assert result["source"] == "fallback"
    assert len(result["items"]) == 2 and result["items"][1]["sku"] is None


def test_voice_limits_are_checked_before_recognition(monkeypatch):
    import io
    import wave
    from app.ai import speech
    monkeypatch.setattr(speech, "available", lambda: True)
    def should_not_run(*args):
        pytest.fail("Invalid audio must not reach recognition")
    monkeypatch.setattr(speech, "transcribe", should_not_run)
    with intake_client() as client:
        assert client.post("/api/ai/order-intake/voice", files={"file": ("huge.wav", b"0" * (10 * 1024 * 1024 + 1), "audio/wav")}).status_code == 413
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            wav.setparams((1, 2, 16000, 0, "NONE", "not compressed")); wav.writeframes(b"\0\0" * 16000 * 121)
        assert client.post("/api/ai/order-intake/voice", files={"file": ("long.wav", buf.getvalue(), "audio/wav")}).status_code == 400


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


def variant_order(label, qty=1, sku="P1"):
    return manual.submit({"channel": "instagram", "buyer": {}, "items": [{"sku": sku, "qty": qty, "variant": label}]}, "t")


def test_unknown_and_missing_variants_leave_stock_untouched():
    make_product(stock=3, data={**FULL, "variants": "M, L"})
    for label in ("NONEXISTENT-99", None):
        assert variant_order(label)["status"] == "rejected"
    with db.read() as c:
        assert c.execute("SELECT reserved FROM products").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0


def test_variant_reserve_cancel_and_ship_are_independent():
    pid = make_product(stock=4)
    with db.tx() as c:
        inventory.set_variants(c, pid, [{"label": "42 / белый", "stock": 1}, {"label": "42 / чёрный", "stock": 3}])
    first = variant_order("42 / белый")["order_id"]
    assert variant_order("42 / белый")["status"] == "rejected_no_stock"
    black = variant_order("42 / чёрный")["order_id"]
    with db.tx() as c:
        orders.set_status(c, first, "cancelled", "t")
        assert [(v["stock"], v["reserved"]) for v in inventory.variants(c, pid)] == [(1, 0), (3, 1)]
    deliver(black)
    with db.read() as c:
        p = passport.get_product(c, pid)
        assert (p["stock"], p["reserved"]) == (3, 0)
        assert [(v["stock"], v["reserved"]) for v in p["inventory_variants"]] == [(1, 0), (2, 0)]


def test_concurrent_orders_cannot_reserve_same_variant_twice():
    from concurrent.futures import ThreadPoolExecutor
    pid = make_product(stock=3)
    with db.tx() as c:
        inventory.set_variants(c, pid, [{"label": "M", "stock": 1}, {"label": "L", "stock": 2}])
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(variant_order, ["M", "M"]))
    assert sorted(r["status"] for r in result) == ["done", "rejected_no_stock"]
    with db.read() as c:
        assert [v["reserved"] for v in inventory.variants(c, pid)] == [1, 0]
        assert passport.get_product(c, pid)["reserved"] == 1


def test_variant_stock_edits_preserve_reserves_and_existing_labels():
    pid = make_product(stock=3)
    with db.tx() as c:
        inventory.set_variants(c, pid, [{"label": "M", "stock": 1}, {"label": "L", "stock": 2}])
    variant_order("M")
    for entries in (
        [{"label": "M", "stock": 0}, {"label": "L", "stock": 2}],
        [{"label": "L", "stock": 2}],
        [{"label": "M", "stock": 1}, {"label": "m", "stock": 2}],
        [{"label": "M", "stock": 1.5}],
    ):
        with pytest.raises(CoreError), db.tx() as c:
            inventory.set_variants(c, pid, entries)
    with db.read() as c:
        assert [(v["stock"], v["reserved"]) for v in inventory.variants(c, pid)] == [(1, 1), (2, 0)]
    with db.tx() as c, pytest.raises(CoreError):
        inventory.set_stock(c, pid, 20)


def test_variant_setup_does_not_guess_existing_reservations():
    pid = make_product(stock=3)
    make_order()
    with db.tx() as c, pytest.raises(CoreError):
        inventory.set_variants(c, pid, [{"label": "M", "stock": 3}])
    with db.read() as c:
        assert inventory.variants(c, pid) == []
        assert passport.get_product(c, pid)["reserved"] == 1


def test_partial_return_only_restocks_selected_line_and_quantity():
    a = make_product(stock=3)
    b = make_product(sku="P2", stock=2)
    oid = manual.submit({"channel": "instagram", "buyer": {}, "items": [{"sku": "P1", "qty": 2}, {"sku": "P2", "qty": 1}]}, "t")["order_id"]
    deliver(oid)
    with db.tx() as c:
        o = orders.get(c, oid)
        cid = claims.create(c, oid, "Вернуть одно платье из двух", "t")
        with pytest.raises(CoreError):
            claims.confirm(c, cid, "not_as_described", None, "size", "t")
        claims.confirm(c, cid, "not_as_described", None, "size", "t", o["items"][0]["id"], 1)
        with pytest.raises(CoreError):
            claims.decide(c, cid, "refund", "Возврат", "t", refund_amount=20000, return_to_stock=True)
        assert claims.get(c, cid)["status"] == "open"  # validation precedes any mutation
        claims.decide(c, cid, "refund", "Возвращено одно платье", "t", refund_amount=10000, return_to_stock=True)
        assert claims.decide(c, cid, "refund", "Повтор", "t", return_to_stock=True)["repeated"]
        assert passport.get_product(c, a)["stock"] == 2
        assert passport.get_product(c, b)["stock"] == 1
        assert c.execute("SELECT SUM(qty) FROM stock_returns").fetchone()[0] == 1
        assert metrics.compute(c)["revenue"] == 20000


def test_return_restores_correct_variant_and_prevents_repeated_restock():
    pid = make_product(stock=3)
    with db.tx() as c:
        inventory.set_variants(c, pid, [{"label": "M", "stock": 1}, {"label": "L", "stock": 2}])
    oid = variant_order("M")["order_id"]
    deliver(oid)
    with db.tx() as c:
        cid = claims.create(c, oid, "Брак", "t")
        claims.confirm(c, cid, "defect", None, "defect", "t")
        claims.decide(c, cid, "replace", "Возврат для замены", "t", return_to_stock=True)
        second = claims.create(c, oid, "Повторное обращение", "t")
        claims.confirm(c, second, "defect", None, "defect", "t")
        with pytest.raises(CoreError):
            claims.decide(c, second, "replace", "Повторный возврат", "t", return_to_stock=True)
        assert [(v["stock"], v["reserved"]) for v in inventory.variants(c, pid)] == [(1, 0), (2, 0)]


def test_refunds_across_claims_cannot_exceed_item_value():
    make_product()
    oid = make_order()["order_id"]
    deliver(oid)
    with db.tx() as c:
        for amount in (7000, 3000):
            cid = claims.create(c, oid, "Уменьшение цены", "t")
            claims.confirm(c, cid, "defect", None, "defect", "t")
            claims.decide(c, cid, "price_reduction", "Компенсация", "t", refund_amount=amount)
        cid = claims.create(c, oid, "Ещё возврат", "t")
        claims.confirm(c, cid, "defect", None, "defect", "t")
        with pytest.raises(CoreError):
            claims.decide(c, cid, "refund", "Возврат", "t", refund_amount=1)
        assert metrics.compute(c)["refunds"] == 10000


def test_claim_uses_selected_item_category_and_rejects_foreign_line():
    make_product(stock=2)
    make_product(sku="P2", stock=1, category="underwear")
    oid = manual.submit({"channel": "instagram", "buyer": {}, "items": [{"sku": "P1"}, {"sku": "P2"}]}, "t")["order_id"]
    foreign = make_order()["order_id"]
    deliver(oid)
    with db.tx() as c:
        cid = claims.create(c, oid, "Не подошло бельё", "t")
        with pytest.raises(CoreError):
            claims.confirm(c, cid, "changed_mind", True, None, "t", orders.get(c, foreign)["items"][0]["id"], 1)
        item = orders.get(c, oid)["items"][1]
        claims.confirm(c, cid, "changed_mind", True, None, "t", item["id"], 1)
        assert claims.evaluation(c, cid)["refusal_allowed"]
        assert "refund" not in claims.evaluation(c, cid)["options"]


def test_money_metrics_separate_unpaid_and_cancelled_orders():
    make_product(stock=3)
    a, b, cancelled = [make_order()["order_id"] for _ in range(3)]
    with db.tx() as c:
        orders.set_status(c, cancelled, "cancelled", "t")
        m = metrics.compute(c)
        assert (m["orders_value"], m["unpaid_orders_value"], m["payments_received"], m["revenue"]) == (20000, 20000, 0, 0)
        orders.confirm_payment(c, a, "bank_statement", "PAID", 10000, "t")
        orders.set_status(c, a, "cancelled", "t")
        m = metrics.compute(c)
        assert (m["orders_value"], m["unpaid_orders_value"], m["payments_received"], m["revenue"]) == (10000, 10000, 10000, 10000)


def test_legacy_migration_is_additive_and_records_previous_restock(monkeypatch, tmp_path):
    import sqlite3
    from app import config
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "legacy.db")
    with sqlite3.connect(config.DB_PATH) as conn:
        conn.executescript("""
            CREATE TABLE products(id INTEGER PRIMARY KEY,stock INTEGER,reserved INTEGER);
            CREATE TABLE orders(id INTEGER PRIMARY KEY);
            CREATE TABLE order_items(id INTEGER PRIMARY KEY,order_id INTEGER,product_id INTEGER,qty INTEGER,price INTEGER,variant TEXT);
            CREATE TABLE claims(id INTEGER PRIMARY KEY,order_id INTEGER);
            CREATE TABLE events(id INTEGER PRIMARY KEY,claim_id INTEGER,type TEXT);
            INSERT INTO products VALUES(1,7,1);
            INSERT INTO orders VALUES(1);
            INSERT INTO order_items VALUES(1,1,1,2,10000,'M');
            INSERT INTO claims VALUES(1,1);
            INSERT INTO events VALUES(1,1,'returned_to_stock');
        """)
    db.init()
    db.init()
    with db.read() as c:
        assert tuple(c.execute("SELECT stock,reserved FROM products").fetchone()) == (7, 1)
        assert tuple(c.execute("SELECT order_item_id,item_qty FROM claims").fetchone()) == (1, 2)
        assert tuple(c.execute("SELECT claim_id,order_item_id,qty FROM stock_returns").fetchone()) == (1, 1, 2)
        assert inventory.variants(c, 1) == []


def test_api_variant_setup_and_strict_stock_validation():
    from fastapi.testclient import TestClient
    from app.main import app, SESSION
    pid = make_product()
    with TestClient(app) as client:
        client.cookies.set("adal_session", SESSION)
        response = client.put(f"/api/products/{pid}/variants", json={"variants": [{"label": "M / синий", "stock": 2}]})
        assert response.status_code == 200
        assert response.json()["variant_options"] == ["M / синий"]
        for bad in (-1, 1.5, True):
            assert client.put(f"/api/products/{pid}/variants", json={"variants": [{"label": "M / синий", "stock": bad}]}).status_code == 422
        response = client.post("/api/orders", json={"channel": "instagram", "buyer": {}, "items": [{"sku": "P1", "variant": "M / синий", "qty": 1}]})
        assert response.status_code == 200
        item = client.get(f"/api/orders/{response.json()['order_id']}").json()["items"][0]
        assert item["variant_id"] is not None


def test_fresh_demo_contains_working_variant_stock():
    from app import seed
    seed.run()
    seed.run()
    with db.read() as c:
        assert c.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 6
        p = passport.get_product(c, 2)
        assert sum(v["stock"] for v in p["inventory_variants"]) == p["stock"]
        assert sum(v["reserved"] for v in p["inventory_variants"]) == p["reserved"] == 2


@pytest.mark.parametrize("qty", [0, -1, 1.5, "2", True])
def test_invalid_order_quantities_are_rejected_without_reservation(qty):
    make_product(stock=3)
    assert variant_order(None, qty=qty)["status"] == "rejected"
    with db.read() as c:
        assert c.execute("SELECT reserved FROM products").fetchone()[0] == 0


def test_old_order_requires_explicit_variant_for_restock_after_setup():
    pid = make_product(stock=2)
    oid = make_order()["order_id"]
    deliver(oid)
    with db.tx() as c:
        inventory.set_variants(c, pid, [{"label": "M / синий", "stock": 1}])
        variant_id = inventory.variants(c, pid)[0]["id"]
        cid = claims.create(c, oid, "Возврат старого заказа", "t")
        claims.confirm(c, cid, "defect", None, None, "t")
        with pytest.raises(CoreError):
            claims.decide(c, cid, "refund", "Возврат", "t", return_to_stock=True)
        assert claims.get(c, cid)["status"] == "open"
        claims.decide(c, cid, "refund", "Проверен размер возвращаемого товара", "t", return_to_stock=True, return_variant_id=variant_id)
        assert inventory.variants(c, pid)[0]["stock"] == 2


def test_api_partial_refund_selects_position_and_keeps_other_stock():
    from fastapi.testclient import TestClient
    from app.main import app, SESSION
    first = make_product(stock=3)
    second = make_product(sku="P2", stock=2)
    oid = manual.submit({"channel": "instagram", "buyer": {}, "items": [{"sku": "P1", "qty": 2}, {"sku": "P2", "qty": 1}]}, "t")["order_id"]
    deliver(oid)
    with TestClient(app) as client:
        client.cookies.set("adal_session", SESSION)
        cid = client.post("/api/claims", json={"order_id": oid, "text": "Возврат только одной единицы P1"}).json()["id"]
        items = client.get(f"/api/orders/{oid}").json()["items"]
        assert client.post(f"/api/claims/{cid}/confirm", json={"type": "defect", "order_item_id": items[0]["id"], "item_qty": 1}).status_code == 200
        request = {"decision": "refund", "text": "Вернуть только одну единицу", "refund_amount": 10000, "return_to_stock": True}
        assert client.post(f"/api/claims/{cid}/decide", json=request).status_code == 200
        assert client.post(f"/api/claims/{cid}/decide", json=request).json()["repeated"]
        assert client.get(f"/api/products/{first}").json()["stock"] == 2
        assert client.get(f"/api/products/{second}").json()["stock"] == 1
        assert client.get("/api/metrics").json()["revenue"] == 20000
        draft = client.post(f"/api/claims/{cid}/ai/response").json()
        assert "P1" in draft["text"] and "количество: 1" in draft["text"]
