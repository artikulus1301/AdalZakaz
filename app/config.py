"""Настройки приложения. Всё, что зависит от окружения, читается из .env."""
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DATA_DIR = Path(os.getenv("ADAL_DATA_DIR", BASE_DIR / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = Path(os.getenv("ADAL_DB", DATA_DIR / "adalzakaz.db"))
STATIC_DIR = BASE_DIR / "static"

TZ = ZoneInfo(os.getenv("ADAL_TZ", "Asia/Almaty"))
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
SEED_DEMO = os.getenv("ADAL_SEED_DEMO", "1") == "1"

# --- LLM (меняемый модуль). Провайдер xKiro, OpenAI-совместимый API, модели Qwen ---
# Старые имена QWEN_* поддерживаются как запасные.
def _env(*names: str, default: str = "") -> str:
    return next((os.getenv(n) for n in names if os.getenv(n)), default)


LLM_PROVIDER = _env("LLM_PROVIDER", default="xKiro")
QWEN_API_KEY = _env("LLM_API_KEY", "XKIRO_API_KEY", "QWEN_API_KEY", "DASHSCOPE_API_KEY")
QWEN_BASE_URL = _env("LLM_BASE_URL", "QWEN_BASE_URL", default="https://api.xkiro.com/v1")
QWEN_TEXT_MODEL = _env("LLM_TEXT_MODEL", "QWEN_TEXT_MODEL", default="qwen/qwen3.7-plus:free")
QWEN_VL_MODEL = _env("LLM_VISION_MODEL", "QWEN_VL_MODEL", default="qwen/qwen3-vl-plus:free")
QWEN_ASR_MODEL = _env("LLM_SPEECH_MODEL", "QWEN_ASR_MODEL", default="qwen/qwen3.8-omni-flash:free")
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "60"))
ASR_PROVIDER = os.getenv("ASR_PROVIDER", "local").lower()
ASR_LOCAL_MODEL = os.getenv("ASR_LOCAL_MODEL", "base")

# --- Kaspi Магазин ---
KASPI_TOKEN = os.getenv("KASPI_TOKEN", "")
KASPI_BASE_URL = os.getenv("KASPI_BASE_URL", "https://kaspi.kz/shop/api/v2")

MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "50"))

for _d in (DATA_DIR, UPLOAD_DIR):
    _d.mkdir(parents=True, exist_ok=True)
