"""Separate speech recognition from text generation: some gateways accept only text/vision."""
import importlib.util
import io
import re
import threading

from .. import config
from ..core import CoreError
from .llm import llm

_model = None
_lock = threading.Lock()


def available() -> bool:
    if config.ASR_PROVIDER == "local":
        return importlib.util.find_spec("faster_whisper") is not None
    return config.ASR_PROVIDER == "llm" and llm.available


def local_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        local_path = config.DATA_DIR / "speech_models" / config.ASR_LOCAL_MODEL
        model_name = str(local_path) if (local_path / "model.bin").is_file() else config.ASR_LOCAL_MODEL
        _model = WhisperModel(model_name, device="cpu", compute_type="int8", cpu_threads=4,
                              download_root=str(config.DATA_DIR / "speech_models"))
    return _model


def transcribe(audio: bytes, mime: str = "audio/wav") -> dict:
    if not available():
        raise CoreError("Распознавание голоса не настроено. Установите модуль речи на сервере или вставьте текст.", status=503)
    if config.ASR_PROVIDER == "local":
        if not _lock.acquire(blocking=False):
            raise CoreError("Сейчас распознаётся другая запись. Попробуйте через несколько секунд.", status=429)
        try:
            segments, info = local_model().transcribe(io.BytesIO(audio), beam_size=5, vad_filter=True,
                                                       condition_on_previous_text=False)
            text = " ".join(segment.text.strip() for segment in segments).strip()
            return {"text": text, "source": "local", "model": f"whisper-{config.ASR_LOCAL_MODEL}", "lang": info.language}
        finally:
            _lock.release()
    text, model = llm.transcribe(audio, mime)
    # A gateway may silently discard audio and return a textual refusal with HTTP 200.
    if re.search(r"(?:не могу|не удаётся|не удается|нет возможности).{0,100}(?:аудио|звук)|(?:cannot|can't|unable to).{0,80}(?:audio|sound)", text, re.I | re.S):
        raise CoreError("Подключённая модель не смогла принять аудио. Выберите локальное распознавание или вставьте текст.", status=502)
    return {"text": text, "source": "llm", "model": model}


if __name__ == "__main__":
    with _lock:
        local_model()
    print(f"Speech model ready: whisper-{config.ASR_LOCAL_MODEL}")
