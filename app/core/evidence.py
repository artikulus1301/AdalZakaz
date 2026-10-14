"""Журнал доказательств: видео упаковки, фото состояния, голос, снимок паспорта.
Файл хранится под своим SHA-256 — так видно, что его не подменили."""
import hashlib
import mimetypes
import sqlite3
from pathlib import Path

from .. import config, db
from . import CoreError

KINDS = {
    "packing_video": "Видео упаковки",
    "packing_photo": "Фото упаковки",
    "condition_photo": "Фото состояния товара",
    "buyer_photo": "Фото от покупателя",
    "buyer_voice": "Голосовое сообщение покупателя",
    "return_video": "Видео вскрытия возврата",
    "chat": "Переписка",
    "product_photo": "Фото товара",
    "other": "Другое",
}

ALLOWED_PREFIXES = ("image/", "video/", "audio/", "application/pdf", "text/plain")


def store_file(content: bytes, original_name: str, mime: str | None) -> dict:
    if len(content) > config.MAX_UPLOAD_MB * 1024 * 1024:
        raise CoreError(f"Файл больше {config.MAX_UPLOAD_MB} МБ")
    if not content:
        raise CoreError("Пустой файл")
    mime = mime or mimetypes.guess_type(original_name)[0] or "application/octet-stream"
    if not mime.startswith(ALLOWED_PREFIXES):
        raise CoreError("Недопустимый тип файла")
    sha = hashlib.sha256(content).hexdigest()
    ext = Path(original_name).suffix.lower()[:8] or mimetypes.guess_extension(mime) or ""
    fname = f"{sha}{ext}"
    path = config.UPLOAD_DIR / fname
    if not path.exists():
        path.write_bytes(content)
    return {"filename": fname, "sha256": sha, "mime": mime, "size": len(content), "original_name": original_name}


def add(c: sqlite3.Connection, kind: str, stored: dict, actor: str, order_id: int | None = None,
        claim_id: int | None = None, product_id: int | None = None, note: str | None = None) -> int:
    if kind not in KINDS:
        raise CoreError("Неизвестный вид доказательства")
    cur = c.execute(
        "INSERT INTO evidence(order_id, claim_id, product_id, kind, filename, original_name, sha256, mime, size, note, "
        "actor, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (order_id, claim_id, product_id, kind, stored.get("filename"), stored.get("original_name"), stored.get("sha256"),
         stored.get("mime"), stored.get("size"), note, actor, db.now_iso()),
    )
    from . import events
    events.log(c, "evidence_added", actor, order_id=order_id, claim_id=claim_id,
               payload={"kind": kind, "sha256": stored.get("sha256"), "evidence_id": cur.lastrowid})
    return cur.lastrowid


def for_order(c: sqlite3.Connection, order_id: int) -> list[dict]:
    out = db.rows(c.execute(
        "SELECT * FROM evidence WHERE order_id = ? OR claim_id IN (SELECT id FROM claims WHERE order_id = ?) ORDER BY id",
        (order_id, order_id)))
    for e in out:
        e["kind_label"] = KINDS.get(e["kind"], e["kind"])
    return out


def read_file(filename: str) -> tuple[bytes, str]:
    path = (config.UPLOAD_DIR / filename).resolve()
    if path.parent != config.UPLOAD_DIR.resolve() or not path.exists():
        raise CoreError("Файл не найден", status=404)
    return path.read_bytes(), mimetypes.guess_type(path.name)[0] or "application/octet-stream"
