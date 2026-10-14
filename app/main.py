"""AdalZakaz — веб-приложение: панель продавца + страницы покупателя (kk/ru).
Запуск: .venv\\Scripts\\python -m uvicorn app.main:app --reload"""
import hashlib
import hmac
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta

import segno
from fastapi import Depends, FastAPI, File, Form, Header, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, db, seed
from .ai import orchestrator as ai
from .ai.llm import llm
from .connectors import kaspi, manual
from .core import CoreError, claims, events, evidence, inventory, metrics, orders, passport, rules

logging.basicConfig(level=logging.INFO)

SELLER_PASSWORD = os.getenv("ADAL_SELLER_PASSWORD", "demo")
SESSION = hmac.new(SELLER_PASSWORD.encode(), b"adalzakaz-seller-session", hashlib.sha256).hexdigest()


@asynccontextmanager
async def lifespan(_app):
    db.init()
    if config.SEED_DEMO:
        seed.run()
    yield


app = FastAPI(title="AdalZakaz", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")


@app.exception_handler(CoreError)
async def core_error(_req: Request, e: CoreError):
    return JSONResponse({"error": e.message, "code": e.code}, status_code=e.status)


def seller(request: Request) -> str:
    if not hmac.compare_digest(request.cookies.get("adal_session", ""), SESSION):
        raise CoreError("Нужен вход продавца", status=401, code="auth")
    return "seller"


# ---------------- страницы ----------------

@app.get("/")
def index(request: Request):
    if request.cookies.get("adal_session") != SESSION:
        return RedirectResponse("/login")
    return FileResponse(config.STATIC_DIR / "index.html")


@app.get("/login")
def login_page():
    return FileResponse(config.STATIC_DIR / "login.html")


@app.post("/login")
def login(password: str = Form(...)):
    if not hmac.compare_digest(password, SELLER_PASSWORD):
        return RedirectResponse("/login?error=1", status_code=303)
    r = RedirectResponse("/", status_code=303)
    r.set_cookie("adal_session", SESSION, httponly=True, samesite="lax", max_age=7 * 86400)
    return r


@app.get("/logout")
def logout():
    r = RedirectResponse("/login", status_code=303)
    r.delete_cookie("adal_session")
    return r


@app.get("/o/{token}")
def buyer_order_page(token: str):
    return FileResponse(config.STATIC_DIR / "order.html")


@app.get("/p/{product_id}")
def passport_page(product_id: int):
    return FileResponse(config.STATIC_DIR / "passport.html")


# ---------------- справочники ----------------

@app.get("/api/meta")
def meta(who: str = Depends(seller)):
    with db.read() as c:
        mode = ai.ai_mode(c)
        seller_name = db.get_setting(c, "seller_name", "")
    return {
        "channels": orders.CHANNELS, "statuses": orders.STATUSES, "payment_sources": orders.PAYMENT_SOURCES,
        "categories": {k: v["ru"] for k, v in passport.CATEGORIES.items()},
        "fields": {k: v[0] for k, v in passport.FIELDS.items()},
        "claim_types": {k: v[0] for k, v in rules.CLAIM_TYPES.items()}, "decisions": rules.DECISIONS,
        "evidence_kinds": evidence.KINDS, "reason_tags": {k: v[0] for k, v in ai.REASON_TAGS.items()},
        "ai": {"available": llm.available, "provider": config.LLM_PROVIDER, "base_url": config.QWEN_BASE_URL, "text_model": config.QWEN_TEXT_MODEL, "vl_model": config.QWEN_VL_MODEL,
               "asr_model": config.QWEN_ASR_MODEL, "mode": mode},
        "kaspi_mode": "api" if config.KASPI_TOKEN else "demo",
        "seller_name": seller_name, "public_base_url": config.PUBLIC_BASE_URL,
    }


class ModeIn(BaseModel):
    ai_mode: str


@app.post("/api/settings/ai-mode")
def set_mode(body: ModeIn, who: str = Depends(seller)):
    if body.ai_mode not in ("shadow", "draft"):
        raise CoreError("Режим: shadow или draft")
    with db.tx() as c:
        db.set_setting(c, "ai_mode", body.ai_mode)
        events.log(c, "ai_mode_changed", who, payload={"mode": body.ai_mode})
    return {"ok": True}


# ---------------- товары и паспорта ----------------

@app.get("/api/products")
def products(who: str = Depends(seller)):
    with db.read() as c:
        ids = [r[0] for r in c.execute("SELECT id FROM products ORDER BY id")]
        return [passport.get_product(c, i) for i in ids]


class ProductIn(BaseModel):
    sku: str
    category: str
    stock: int = 0
    data: dict


@app.post("/api/products")
def create_product(body: ProductIn, who: str = Depends(seller)):
    with db.tx() as c:
        pid = passport.create_product(c, body.sku, body.category, body.stock, body.data, who)
        return passport.get_product(c, pid)


@app.get("/api/products/{pid}")
def product(pid: int, who: str = Depends(seller)):
    with db.read() as c:
        p = passport.get_product(c, pid)
        p["versions"] = passport.list_versions(c, pid)
        p["drafts"] = ai.drafts_for(c, product_id=pid)
        p["public_url"] = f"{config.PUBLIC_BASE_URL}/p/{pid}"
        return p


@app.get("/api/products/{pid}/versions/{v}")
def product_version(pid: int, v: int, who: str = Depends(seller)):
    with db.read() as c:
        cur = passport.get_version(c, pid, v)
        prev = passport.get_version(c, pid, v - 1) if v > 1 else {"data": {}}
        cur["diff"] = passport.diff(prev["data"], cur["data"])
        return cur


class PassportIn(BaseModel):
    data: dict
    note: str = "Правка продавца"


@app.put("/api/products/{pid}/passport")
def update_passport(pid: int, body: PassportIn, who: str = Depends(seller)):
    with db.tx() as c:
        old = passport.get_version(c, pid)["data"]
        data = {**old, **body.data, "photos": body.data.get("photos", old.get("photos", []))}
        v = passport.save_version(c, pid, data, body.note, who)
        return {"version": v, "product": passport.get_product(c, pid)}


class ApplyIn(BaseModel):
    field: str
    value: str
    note: str = "Правка по причинам возвратов (утверждена продавцом)"


@app.post("/api/products/{pid}/apply")
def apply_field(pid: int, body: ApplyIn, who: str = Depends(seller)):
    if body.field not in passport.FIELDS or body.field == "photos":
        raise CoreError("Неизвестное поле паспорта")
    with db.tx() as c:
        data = passport.get_version(c, pid)["data"]
        data[body.field] = body.value
        return {"version": passport.save_version(c, pid, data, body.note, who)}


class StockIn(BaseModel):
    stock: int


@app.post("/api/products/{pid}/stock")
def set_stock(pid: int, body: StockIn, who: str = Depends(seller)):
    with db.tx() as c:
        inventory.set_stock(c, pid, body.stock)
        events.log(c, "stock_set", who, payload={"product_id": pid, "stock": body.stock})
        return passport.get_product(c, pid)


@app.post("/api/products/{pid}/photos")
async def add_photo(pid: int, file: UploadFile = File(...), who: str = Depends(seller)):
    content = await file.read()
    if not (file.content_type or "").startswith("image/"):
        raise CoreError("Нужна фотография")
    stored = evidence.store_file(content, file.filename or "photo.jpg", file.content_type)
    with db.tx() as c:
        evidence.add(c, "product_photo", stored, who, product_id=pid)
        data = passport.get_version(c, pid)["data"]
        data["photos"] = list(dict.fromkeys((data.get("photos") or []) + [stored["filename"]]))
        v = passport.save_version(c, pid, data, "Добавлено фото реального товара", who)
    return {"version": v, "filename": stored["filename"]}


@app.post("/api/products/{pid}/ai/draft")
def product_ai_draft(pid: int, who: str = Depends(seller)):
    with db.tx(immediate=False) as c:
        return ai.card_draft(c, pid)


@app.post("/api/products/{pid}/ai/check")
def product_ai_check(pid: int, who: str = Depends(seller)):
    with db.tx(immediate=False) as c:
        return ai.card_check(c, pid)


def _qr(url: str) -> Response:
    buf = segno.make(url, error="m").svg_inline(scale=5, dark="#16302b", light="#ffffff")
    return Response(buf, media_type="image/svg+xml")


@app.get("/api/products/{pid}/qr.svg")
def product_qr(pid: int, who: str = Depends(seller)):
    return _qr(f"{config.PUBLIC_BASE_URL}/p/{pid}")


# ---------------- заказы ----------------

@app.get("/api/orders")
def list_orders(status: str | None = None, channel: str | None = None, who: str = Depends(seller)):
    with db.read() as c:
        return orders.list_orders(c, status, channel)


class OrderIn(BaseModel):
    channel: str
    buyer: dict
    items: list[dict]
    delivery_zone: str | None = None


@app.post("/api/orders")
def create_order(body: OrderIn, who: str = Depends(seller), idempotency_key: str | None = Header(default=None)):
    r = manual.submit(body.model_dump(), who, idempotency_key)
    if r.get("status") != "done":
        raise CoreError(r.get("error") or "Заказ не принят", status=409, code=r.get("status", "rejected"))
    return r


@app.get("/api/orders/{oid}")
def order_detail(oid: int, who: str = Depends(seller)):
    with db.read() as c:
        o = orders.get(c, oid)
        o["events"] = events.for_order(c, oid)
        o["evidence"] = evidence.for_order(c, oid)
        o["messages"] = db.rows(c.execute("SELECT * FROM messages WHERE order_id = ? ORDER BY id", (oid,)))
        o["drafts"] = ai.drafts_for(c, order_id=oid)
        o["claims"] = [claims.get(c, r[0]) for r in c.execute("SELECT id FROM claims WHERE order_id = ?", (oid,))]
        o["buyer_url"] = f"{config.PUBLIC_BASE_URL}/o/{o['public_token']}" if o["public_token"] else None
        return o


class PaymentIn(BaseModel):
    source: str
    ref: str
    amount: int


@app.post("/api/orders/{oid}/payment")
def confirm_payment(oid: int, body: PaymentIn, who: str = Depends(seller)):
    with db.tx() as c:
        changed = orders.confirm_payment(c, oid, body.source, body.ref, body.amount, who)
    return {"ok": True, "repeated": not changed}


class StatusIn(BaseModel):
    status: str
    reason: str | None = None


@app.post("/api/orders/{oid}/status")
def set_status(oid: int, body: StatusIn, who: str = Depends(seller)):
    with db.tx() as c:
        orders.set_status(c, oid, body.status, who, body.reason)
    return {"ok": True}


class AddressIn(BaseModel):
    address: str
    zone: str = "Тараз"
    window: str


@app.post("/api/orders/{oid}/address")
def confirm_address(oid: int, body: AddressIn, who: str = Depends(seller)):
    with db.tx() as c:
        orders.confirm_address(c, oid, body.address, body.zone, body.window, who)
    return {"ok": True}


class ClarIn(BaseModel):
    question: str
    answer: str


@app.post("/api/orders/{oid}/clarification")
def clarification(oid: int, body: ClarIn, who: str = Depends(seller)):
    with db.tx() as c:
        orders.record_clarification(c, oid, body.question, body.answer, who)
    return {"ok": True}


@app.post("/api/orders/{oid}/evidence")
async def order_evidence(oid: int, kind: str = Form(...), note: str = Form(""), claim_id: int | None = Form(None),
                         file: UploadFile = File(...), who: str = Depends(seller)):
    stored = evidence.store_file(await file.read(), file.filename or "file", file.content_type)
    with db.tx() as c:
        orders.get(c, oid)
        eid = evidence.add(c, kind, stored, who, order_id=oid, claim_id=claim_id, note=note or None)
    return {"id": eid, "sha256": stored["sha256"]}


class TextIn(BaseModel):
    text: str


@app.post("/api/orders/{oid}/messages")
def seller_message(oid: int, body: TextIn, who: str = Depends(seller)):
    if not body.text.strip():
        raise CoreError("Пустое сообщение")
    with db.tx() as c:
        orders.get(c, oid)
        c.execute("INSERT INTO messages(order_id, direction, text, lang, created_at) VALUES (?, 'out', ?, ?, ?)",
                  (oid, body.text.strip(), ai.detect_lang(body.text), db.now_iso()))
    return {"ok": True}


@app.post("/api/orders/{oid}/ai/clarify")
def order_ai_clarify(oid: int, who: str = Depends(seller)):
    with db.tx(immediate=False) as c:
        return ai.clarify(c, oid)


@app.post("/api/orders/{oid}/ai/reply")
def order_ai_reply(oid: int, body: TextIn, who: str = Depends(seller)):
    with db.tx(immediate=False) as c:
        orders.get(c, oid)
        return ai.reply_draft(c, oid, body.text)


@app.get("/api/orders/{oid}/qr.svg")
def order_qr(oid: int, who: str = Depends(seller)):
    with db.read() as c:
        o = orders.get(c, oid)
    if not o["public_token"]:
        raise CoreError("Для заказов Kaspi внешняя страница не создаётся", status=404)
    return _qr(f"{config.PUBLIC_BASE_URL}/o/{o['public_token']}")


@app.post("/api/connectors/kaspi/sync")
def kaspi_sync(who: str = Depends(seller)):
    return kaspi.sync()


@app.get("/api/inbox")
def inbox(who: str = Depends(seller)):
    with db.read() as c:
        return db.rows(c.execute("SELECT id, idem_key, source, status, error, result_order_id, created_at "
                                 "FROM inbox ORDER BY id DESC LIMIT 50"))


# ---------------- претензии ----------------

@app.get("/api/claims")
def list_claims(status: str | None = None, who: str = Depends(seller)):
    with db.read() as c:
        return claims.list_claims(c, status)


@app.get("/api/claims/{cid}")
def claim_detail(cid: int, who: str = Depends(seller)):
    with db.read() as c:
        cl = claims.get(c, cid)
        cl["evaluation"] = claims.evaluation(c, cid)
        cl["order"] = orders.get(c, cl["order_id"])
        cl["evidence"] = evidence.for_order(c, cl["order_id"])
        cl["drafts"] = ai.drafts_for(c, claim_id=cid)
        cl["events"] = [e for e in events.for_order(c, cl["order_id"])]
        return cl


class ClaimIn(BaseModel):
    order_id: int
    text: str
    lang: str = "ru"


@app.post("/api/claims")
def seller_claim(body: ClaimIn, who: str = Depends(seller)):
    """Претензия, пришедшая не через страницу (например, из Kaspi или по телефону) — продавец регистрирует её сам."""
    with db.tx() as c:
        cid = claims.create(c, body.order_id, body.text, who, lang=body.lang, source="seller")
    with db.tx(immediate=False) as c:
        ai.claim_intake(c, cid)
    return {"id": cid}


@app.post("/api/claims/{cid}/ai/intake")
def claim_ai_intake(cid: int, who: str = Depends(seller)):
    with db.tx(immediate=False) as c:
        return ai.claim_intake(c, cid)


class ConfirmIn(BaseModel):
    type: str
    condition_ok: bool | None = None
    reason_tag: str | None = None


@app.post("/api/claims/{cid}/confirm")
def claim_confirm(cid: int, body: ConfirmIn, who: str = Depends(seller)):
    with db.tx() as c:
        claims.confirm(c, cid, body.type, body.condition_ok, body.reason_tag, who)
        return claims.evaluation(c, cid)


class DecideIn(BaseModel):
    decision: str
    text: str
    refund_amount: int | None = None
    return_to_stock: bool = False


@app.post("/api/claims/{cid}/decide")
def claim_decide(cid: int, body: DecideIn, who: str = Depends(seller)):
    with db.tx() as c:
        return claims.decide(c, cid, body.decision, body.text, who, body.refund_amount, body.return_to_stock)


@app.post("/api/claims/{cid}/ai/response")
def claim_ai_response(cid: int, who: str = Depends(seller)):
    with db.tx(immediate=False) as c:
        return ai.claim_response(c, cid)


@app.post("/api/claims/{cid}/close")
def claim_close(cid: int, who: str = Depends(seller)):
    with db.tx() as c:
        claims.close(c, cid, who)
    return {"ok": True}


# ---------------- черновики ИИ ----------------

class ReviewIn(BaseModel):
    action: str
    text: str | None = None


@app.post("/api/drafts/{did}/review")
def review(did: int, body: ReviewIn, who: str = Depends(seller)):
    with db.tx() as c:
        return ai.review_draft(c, did, body.action, body.text, who)


@app.post("/api/ai/transcribe")
async def transcribe(file: UploadFile = File(...), who: str = Depends(seller)):
    return ai.transcribe(await file.read(), file.content_type or "audio/wav")


# ---------------- аналитика и правила ----------------

@app.get("/api/metrics")
def get_metrics(who: str = Depends(seller)):
    with db.read() as c:
        return metrics.compute(c)


@app.post("/api/ai/reasons")
def reasons(who: str = Depends(seller)):
    with db.tx(immediate=False) as c:
        return ai.reasons_analysis(c)


@app.get("/api/rules")
def get_rules(who: str = Depends(seller)):
    with db.tx() as c:
        r = rules.active(c)
        r["history"] = db.rows(c.execute("SELECT version, approved_by, approved_at, created_at FROM rules ORDER BY version DESC"))
        return r


class RulesIn(BaseModel):
    data: dict


@app.post("/api/rules")
def new_rules(body: RulesIn, who: str = Depends(seller)):
    with db.tx() as c:
        v = rules.new_version(c, body.data)
        events.log(c, "rules_new_version", who, payload={"version": v})
    return {"version": v}


class ApproveIn(BaseModel):
    lawyer: str


@app.post("/api/rules/{version}/approve")
def approve_rules(version: int, body: ApproveIn, who: str = Depends(seller)):
    with db.tx() as c:
        rules.approve(c, version, body.lawyer)
        events.log(c, "rules_approved", who, payload={"version": version, "lawyer": body.lawyer})
    return {"ok": True}


@app.get("/files/{filename}")
def get_file(filename: str, who: str = Depends(seller)):
    data, mime = evidence.read_file(filename)
    return Response(data, media_type=mime)


# ---------------- публичная часть (покупатель) ----------------

AI_NOTICE = {
    "ru": "В этом сервисе используется искусственный интеллект: он помогает продавцу готовить черновики ответов. "
          "Решения о возврате и деньгах принимает человек.",
    "kk": "Бұл сервисте жасанды интеллект қолданылады: ол сатушыға жауап жобаларын дайындауға көмектеседі. "
          "Қайтару және ақша туралы шешімді адам қабылдайды.",
}
EVIDENCE_NOTICE = {
    "ru": "Продавец фиксирует упаковку заказа на видео/фото, чтобы честно разобрать возможный спор. "
          "Материалы хранятся только для этой цели и в пределах срока подачи претензии.",
    "kk": "Сатушы ықтимал дауды әділ шешу үшін тапсырыстың қаптамасын бейне/фотоға түсіреді. "
          "Материалдар тек осы мақсатта және шағым беру мерзімі ішінде сақталады.",
}


def _public_rules(c) -> dict:
    r = rules.active(c)["data"]
    return {
        "return_days": r["return_days_non_food"], "claim_response_days": r["claim_response_days"],
        "conditions": {"ru": r["return_conditions_ru"], "kk": r["return_conditions_kk"]},
        "escalation": {"ru": r["escalation_ru"], "kk": r["escalation_kk"]},
        "excluded": list(r["excluded_categories"].values()),
    }


def _public_passport(d: dict, pid: int) -> dict:
    keep = ("title_ru", "title_kk", "desc_ru", "desc_kk", "brand", "country", "composition", "dimensions", "weight",
            "color", "size_grid", "variants", "model", "warranty_months", "package_contents", "price")
    out = {k: d.get(k) for k in keep if d.get(k) not in (None, "")}
    out["photos"] = [f"/api/public/passport/{pid}/photo/{i}" for i, _ in enumerate(d.get("photos") or [])]
    return out


@app.get("/api/public/orders/{token}")
def public_order(token: str):
    with db.read() as c:
        o = orders.by_token(c, token)
        cls = db.rows(c.execute("SELECT id FROM claims WHERE order_id = ?", (o["id"],)))
        claim_list = []
        for r in cls:
            cl = claims.get(c, r["id"])
            sent = c.execute("SELECT text_final FROM ai_drafts WHERE claim_id = ? AND task = 'claim_response' AND status = 'sent' "
                             "ORDER BY id DESC LIMIT 1", (cl["id"],)).fetchone()
            claim_list.append({"number": cl["number"], "status": cl["status"], "deadline": cl["deadline"],
                               "received_at": cl["received_at"], "response": sent[0] if sent else None})
        msgs = db.rows(c.execute("SELECT direction, text, created_at FROM messages WHERE order_id = ? ORDER BY id", (o["id"],)))
        return {
            "number": o["number"], "status": o["status"], "status_label": o["status_label"], "lang": o["buyer_lang"],
            "total": o["total"], "delivery_window": o["delivery_window"], "delivered_at": o["delivered_at"],
            "items": [{"qty": it["qty"], "price": it["price"], "variant": it["variant"], "category": it["category"],
                       "passport_version": it["passport_version"], "passport": _public_passport(it["passport"], it["product_id"]),
                       "excluded": it["category"] in rules.active(c)["data"]["excluded_categories"]}
                      for it in o["items"]],
            "rules": _public_rules(c), "claims": claim_list, "messages": msgs,
            "claim_types": {k: {"ru": v[0], "kk": v[1]} for k, v in rules.CLAIM_TYPES.items()},
            "can_claim": o["status"] in ("shipped", "delivered", "closed"),
            "ai_notice": AI_NOTICE, "evidence_notice": EVIDENCE_NOTICE,
            "seller_name": db.get_setting(c, "seller_name", ""),
        }


@app.post("/api/public/orders/{token}/claims")
async def public_claim(token: str, request: Request, text: str = Form(""), type: str = Form(""), lang: str = Form("ru"),
                       client_id: str = Form(""), photos: list[UploadFile] = File(default=[]),
                       voice: UploadFile | None = File(default=None)):
    with db.read() as c:
        o = orders.by_token(c, token)
    if len(photos) > 5:
        raise CoreError("Не больше 5 фото")
    stored_photos = []
    for ph in photos:
        if ph.filename:
            content = await ph.read()
            if not (ph.content_type or "").startswith(("image/", "video/")):
                raise CoreError("Приложите фото или видео")
            stored_photos.append(evidence.store_file(content, ph.filename, ph.content_type))
    voice_stored, transcript = None, None
    tr = None
    if voice is not None and voice.filename:
        audio = await voice.read()
        voice_stored = evidence.store_file(audio, voice.filename or "voice.wav", voice.content_type or "audio/wav")
        tr = ai.transcribe(audio, voice.content_type or "audio/wav")
        transcript = tr.get("text") or None
    if not text.strip() and not transcript and voice_stored:
        text = "[голосовое сообщение — прослушать в журнале доказательств]"
    idem = f"claim:{token}:{client_id}" if client_id else None
    with db.tx() as c:
        cid = claims.create(c, o["id"], text, "buyer", buyer_type=type or None, lang=lang if lang in ("ru", "kk") else "ru",
                            voice_transcript=transcript, idem_key=idem)
        already = c.execute("SELECT COUNT(*) FROM evidence WHERE claim_id = ?", (cid,)).fetchone()[0]
        if not already:
            for st in stored_photos:
                evidence.add(c, "buyer_photo", st, "buyer", order_id=o["id"], claim_id=cid)
            if voice_stored:
                evidence.add(c, "buyer_voice", voice_stored, "buyer", order_id=o["id"], claim_id=cid,
                             note=f"Расшифровка: {transcript}" if transcript else None)
        cl = claims.get(c, cid)
    try:
        with db.tx(immediate=False) as c:
            ai.claim_intake(c, cid)
    except Exception as e:  # ИИ не должен ломать приём претензии — счётчик уже запущен
        logging.getLogger("adalzakaz").warning("claim intake failed: %s", e)
    return {"number": cl["number"], "deadline": cl["deadline"], "transcript": transcript,
            "voice_note": (tr or {}).get("note") if voice_stored else None}


@app.post("/api/public/orders/{token}/messages")
def public_message(token: str, body: TextIn):
    if not body.text.strip() or len(body.text) > 2000:
        raise CoreError("Сообщение пустое или слишком длинное")
    with db.read() as c:
        o = orders.by_token(c, token)
        hour_ago = (db.now() - timedelta(hours=1)).isoformat()
        recent = c.execute("SELECT COUNT(*) FROM messages WHERE order_id = ? AND direction = 'in' AND created_at > ?",
                           (o["id"], hour_ago)).fetchone()[0]
    if recent >= 10:
        raise CoreError("Слишком много сообщений, попробуйте позже", status=429)
    with db.tx(immediate=False) as c:
        ai.reply_draft(c, o["id"], body.text)  # черновик ответа для продавца; сам он ничего не отправляет
    return {"ok": True}


@app.get("/api/public/passport/{pid}")
def public_passport(pid: int):
    with db.read() as c:
        p = passport.get_product(c, pid)
        return {"id": pid, "sku": p["sku"], "category": p["category_label"],
                "category_kk": passport.CATEGORIES.get(p["category"], {}).get("kk"),
                "version": p["current_version"], "updated_at": p["passport"]["created_at"],
                "in_stock": p["free"] > 0, "passport": _public_passport(p["passport"]["data"], pid),
                "excluded": p["category"] in rules.active(c)["data"]["excluded_categories"],
                "rules": _public_rules(c), "ai_notice": AI_NOTICE,
                "seller_name": db.get_setting(c, "seller_name", "")}


@app.get("/api/public/passport/{pid}/photo/{idx}")
def public_photo(pid: int, idx: int):
    with db.read() as c:
        photos = passport.get_version(c, pid)["data"].get("photos") or []
    if idx < 0 or idx >= len(photos):
        raise CoreError("Фото не найдено", status=404)
    data, mime = evidence.read_file(photos[idx])
    return Response(data, media_type=mime)


@app.get("/api/health")
def health():
    return {"ok": True, "llm": llm.available, "id": uuid.uuid4().hex[:6]}
