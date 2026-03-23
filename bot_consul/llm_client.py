"""Клиент OpenRouter (OpenAI-совместимый API) для экстракции и генерации."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, List

import requests
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from requests.exceptions import RequestException, Timeout

from bot_consul.config import orchestrator_settings

logger = logging.getLogger(__name__)


def _parse_fallback_models(raw: str) -> List[str]:
    if not (raw or "").strip():
        return []
    return [m.strip() for m in raw.split(",") if m.strip()]


def _models_chain(primary: str, fallbacks: List[str]) -> List[str]:
    """Основная модель + резервы без дубликатов."""
    seen: set[str] = set()
    out: List[str] = []
    for m in [primary] + fallbacks:
        if not m or m in seen:
            continue
        seen.add(m)
        out.append(m)
    return out


def _openrouter_model_supports_reasoning_effort(model: str) -> bool:
    """
    У Gemini / gpt-4o-mini без «o» не шлём — иначе возможен 400.
    """
    m = (model or "").lower()
    if not m.startswith("openai/"):
        return False
    if "o4-mini" in m or "o3-mini" in m or "o1" in m:
        return True
    if "/o3" in m or "/o4" in m:
        return True

    return False


def _merge_reasoning_into_payload(payload: dict, model: str) -> None:
    effort = (orchestrator_settings.OPENROUTER_REASONING_EFFORT or "").strip()
    if not effort:
        return
    if not _openrouter_model_supports_reasoning_effort(model):
        return
    payload["reasoning"] = {"effort": effort}


def _should_try_fallback_http(status: int) -> bool:
    """Повторить на другой модели при перегрузке / лимитах / временных сбоях / недоступной модели."""
    if status == 429:
        return True
    if status == 404:
        return True
    if status in (502, 503, 504, 529):
        return True
    if status >= 500:
        return True
    return False


def _messages_to_api(messages: List[Any]) -> List[dict]:
    out = []
    for m in messages:
        if isinstance(m, SystemMessage):
            out.append({"role": "system", "content": m.content or ""})
        elif isinstance(m, HumanMessage):
            out.append({"role": "user", "content": m.content or ""})
        elif isinstance(m, AIMessage):
            out.append({"role": "assistant", "content": m.content or ""})
        else:
            out.append({"role": "user", "content": str(getattr(m, "content", m)) or ""})
    return out


class OpenRouterChat:
    def __init__(
        self,
        url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        timeout: int | None = None,
    ):
        self.url = url or orchestrator_settings.OPENROUTER_CHAT_URL
        self.api_key = api_key or orchestrator_settings.OPENROUTER_API_KEY or os.environ.get(
            "OPENROUTER_API_KEY", ""
        )
        self.model = model or orchestrator_settings.OPENROUTER_MODEL
        self._fallback_models = _parse_fallback_models(
            orchestrator_settings.OPENROUTER_FALLBACK_MODELS or ""
        )
        self.temperature = (
            temperature if temperature is not None else orchestrator_settings.LLM_TEMPERATURE
        )
        self.timeout = timeout or orchestrator_settings.LLM_TIMEOUT_SEC

        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY не задан (env или OrchestratorSettings)")

    def invoke(self, messages: List[Any]) -> AIMessage:
        """Вызов API; при ошибке / пустом ответе перебирает OPENROUTER_FALLBACK_MODELS."""
        models = _models_chain(self.model, self._fallback_models)
        last_err: Exception | None = None
        api_messages = _messages_to_api(messages)

        for idx, model in enumerate(models):
            payload = {
                "model": model,
                "messages": api_messages,
                "temperature": self.temperature,
            }
            _merge_reasoning_into_payload(payload, model)
            try:
                r = requests.post(
                    self.url,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                        "HTTP-Referer": "https://github.com",
                    },
                    timeout=self.timeout,
                )
            except Timeout as e:
                last_err = e
                logger.warning(
                    "OpenRouter таймаут (%ss) для модели %s: %s",
                    self.timeout,
                    model,
                    e,
                )
                if idx < len(models) - 1:
                    continue
                raise RuntimeError(f"OpenRouter: таймаут для всех моделей: {e}") from e
            except RequestException as e:
                last_err = e
                logger.warning("OpenRouter сеть для модели %s: %s", model, e)
                if idx < len(models) - 1:
                    continue
                raise RuntimeError(f"OpenRouter: ошибка запроса: {e}") from e

            if not r.ok:
                err_text = (r.text or "")[:800]
                if _should_try_fallback_http(r.status_code) and idx < len(models) - 1:
                    logger.warning(
                        "OpenRouter HTTP %s для %s — пробуем fallback: %s",
                        r.status_code,
                        model,
                        err_text[:200],
                    )
                    last_err = RuntimeError(f"OpenRouter HTTP {r.status_code}: {err_text}")
                    continue
                raise RuntimeError(f"OpenRouter HTTP {r.status_code}: {err_text}")

            try:
                data = r.json()
            except json.JSONDecodeError as e:
                last_err = e
                if idx < len(models) - 1:
                    logger.warning("OpenRouter: невалидный JSON от %s: %s", model, e)
                    continue
                raise RuntimeError(f"OpenRouter: невалидный JSON: {e}") from e

            choices = data.get("choices") or []
            if not choices:
                err_tail = json.dumps(data)[:500]
                if idx < len(models) - 1:
                    logger.warning("OpenRouter: нет choices у %s: %s", model, err_tail)
                    last_err = RuntimeError(f"OpenRouter: нет choices: {err_tail}")
                    continue
                raise RuntimeError(f"OpenRouter: нет choices: {err_tail}")

            msg = choices[0].get("message") or {}
            content = msg.get("content") or ""
            if not (content or "").strip():
                if idx < len(models) - 1:
                    logger.warning("OpenRouter: пустой content у модели %s — fallback", model)
                    last_err = RuntimeError("OpenRouter: пустой content")
                    continue
                raise RuntimeError("OpenRouter: пустой ответ модели")

            if idx > 0:
                logger.info("OpenRouter: ответ получен с fallback-модели %s", model)
            return AIMessage(content=content)

        if last_err:
            raise RuntimeError(f"OpenRouter: все модели исчерпаны: {last_err}") from last_err
        raise RuntimeError("OpenRouter: не удалось получить ответ")


_llm_singleton: OpenRouterChat | None = None


def get_llm() -> OpenRouterChat:
    """Один экземпляр OpenRouter на процесс (оркестратор + travel_web_agent)."""
    global _llm_singleton
    if _llm_singleton is None:
        _llm_singleton = OpenRouterChat()
    return _llm_singleton
