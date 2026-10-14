"""Меняемый LLM-модуль. Сейчас — модели Qwen у провайдера xKiro (OpenAI-совместимый API,
модель указывается как vendor/model). Чтобы сменить модель или провайдера, достаточно .env.
Без ключа работает демо-режим: оркестратор подставляет детерминированный эвристический ответ."""
import base64
import json
import logging
import re

from .. import config

log = logging.getLogger("adalzakaz.llm")


class LLMUnavailable(Exception):
    pass


class QwenLLM:
    name = "qwen"

    def __init__(self):
        self._client = None
        self._json_mode = True  # выключается, если модель не поддерживает response_format

    @property
    def available(self) -> bool:
        return bool(config.QWEN_API_KEY)

    def _cli(self):
        if not self.available:
            raise LLMUnavailable("LLM_API_KEY не задан")
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(api_key=config.QWEN_API_KEY, base_url=config.QWEN_BASE_URL,
                                  timeout=config.LLM_TIMEOUT, max_retries=1,
                                  default_headers={"x-api-key": config.QWEN_API_KEY})
        return self._client

    def json(self, system: str, user: str, images: list[tuple[bytes, str]] | None = None) -> tuple[dict, str]:
        """Запрос с ответом в JSON. images — список (байты, mime) для мультимодальной модели."""
        from openai import BadRequestError
        model = config.QWEN_VL_MODEL if images else config.QWEN_TEXT_MODEL
        if images:
            content = [{"type": "image_url", "image_url": {"url": _data_url(b, m)}} for b, m in images]
            content.append({"type": "text", "text": user})
        else:
            content = user
        kwargs = dict(
            model=model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": content}],
            temperature=0.2,
        )
        if not images and self._json_mode:
            try:
                resp = self._cli().chat.completions.create(**kwargs, response_format={"type": "json_object"})
                return parse_json(resp.choices[0].message.content or ""), model
            except BadRequestError as e:
                log.warning("response_format не принят (%s) — работаем без него", e)
                self._json_mode = False
        resp = self._cli().chat.completions.create(**kwargs)
        return parse_json(resp.choices[0].message.content or ""), model

    def transcribe(self, audio: bytes, mime: str) -> tuple[str, str]:
        """Распознавание речи (ru/kk/смешанная) omni-моделью. Аудио — WAV 16 кГц из браузера.
        Формат input_audio — стандартный OpenAI: base64 без префикса data: и поле format."""
        model = config.QWEN_ASR_MODEL
        fmt = "wav" if "wav" in mime else (mime.split("/")[-1].split(";")[0] or "wav")
        content = [
            {"type": "input_audio", "input_audio": {"data": base64.b64encode(audio).decode(), "format": fmt}},
            {"type": "text", "text": "Транскрибируй эту речь дословно на языке оригинала (казахский, русский или смешанный). "
                                     "Выведи только текст расшифровки, без комментариев."},
        ]
        stream = self._cli().chat.completions.create(
            model=model, messages=[{"role": "user", "content": content}], stream=True, modalities=["text"],
        )
        parts = []
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                parts.append(chunk.choices[0].delta.content)
        return "".join(parts).strip(), model


def _data_url(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def parse_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise


llm = QwenLLM()
