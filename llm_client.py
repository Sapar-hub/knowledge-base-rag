"""
llm_client.py — доступ к языковой модели за общим интерфейсом.

ПРАКТИКА 4. Что делает студент:
  1. GigaChatClient.generate() — вызов Сбер GigaChat через официальный SDK
  2. OllamaClient.generate()   — вызов локальной модели через HTTP API Ollama
  3. count_tokens()            — оценка длины промпта до отправки

Готово и трогать не нужно: build_client() и выбор бэкенда по LLM_BACKEND.

Критерии приёмки:
  - смена LLM_BACKEND с gigachat на ollama не требует правки orchestrator.py
  - длина промпта считается до отправки и логируется
  - оба клиента принимают один и тот же список messages и возвращают строку

НЕ МЕНЯТЬ: сигнатуру generate(messages, temperature) — она объявлена в contracts.py
и на неё опирается оркестратор.

Зачем абстракция. Учебные квоты кончаются, сеть в аудитории падает, а занятие
должно продолжаться. Ollama — фолбэк, который работает офлайн. Заодно это
материал для сравнения авторегрессионной модели и reasoning-модели из Л4:
у DeepSeek-R1 в ответе виден блок рассуждений, у GigaChat — нет.

Третий бэкенд — fake_llm.FakeClient, офлайн-заглушка без сети и ключей. Она
нужна для отладки, когда недоступны оба клиента, и ваши реализации не заменяет:
работа, сданная на LLM_BACKEND=fake, не принимается.
"""

import logging
import math
import os
import re

import requests

import config
from contracts import LLMClient

log = logging.getLogger(__name__)

# Тайм-аут HTTP-запроса к Ollama. На CPU reasoning-модель отвечает десятки
# секунд, поэтому значение берётся из окружения, а по умолчанию щедрое.
OLLAMA_TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT", "300"))

# Окно контекста и предел длины ответа. По умолчанию Ollama даёт окно 4096:
# промпт с пятью-десятью чанками занимает 2–4 тыс. токенов, и на deepseek-r1
# рассуждения доедали окно, после чего генерация не останавливалась до
# тайм-аута. Предел ответа гарантирует, что вызов закончится.
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "8192"))
OLLAMA_NUM_PREDICT = int(os.getenv("OLLAMA_NUM_PREDICT", "1024"))

# Рассуждения reasoning-моделей (DeepSeek-R1) пользователю не показываются:
# это черновик модели, а не ответ, в нём нет ссылок на источники, и судья
# на П6 разбирал бы JSON вперемешку с рассуждениями. Новые версии Ollama
# отдают их отдельным полем message.thinking — его просто не берём; старые
# кладут внутрь текста тегами <think>…</think> — их вырезаем.
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def strip_thinking(text: str) -> str:
    """Убрать блок рассуждений из ответа модели."""
    text = _THINK_RE.sub("", text)
    # Незакрытый тег: модель оборвалась посреди рассуждения — ответа нет.
    if "<think>" in text:
        text = text.split("<think>", 1)[0]
    # Иногда открывающий тег съедает шаблон чата и остаётся только закрывающий.
    if "</think>" in text:
        text = text.split("</think>", 1)[1]
    return text.strip()


def estimate_tokens(text: str) -> int:
    """Грубая оценка: примерно один токен на три символа русского текста."""
    return math.ceil(len(text or "") / 3)


class GigaChatClient:
    """Клиент Сбер GigaChat API (необязательная часть П4)."""

    def __init__(self):
        self.model = config.GIGACHAT_MODEL
        from gigachat import GigaChat

        self._client = GigaChat(credentials=config.GIGACHAT_CREDENTIALS,
                                model=self.model,
                                verify_ssl_certs=config.GIGACHAT_VERIFY_SSL,
                                timeout=OLLAMA_TIMEOUT)

    def generate(self, messages: list[dict], temperature: float = 0.3) -> str:
        """Отправить диалог в GigaChat и вернуть текст ответа."""
        try:
            response = self._client.chat({
                "messages": messages,
                # GigaChat не принимает temperature=0: берём минимально допустимое
                "temperature": max(temperature, 0.01),
            })
            return response.choices[0].message.content.strip()
        except Exception as exc:  # SDK бросает свои типы на сеть, авторизацию, квоты
            log.error("GigaChat: %s", exc)
            return f"Ошибка LLM (GigaChat): {type(exc).__name__}: {exc}"

    def count_tokens(self, text: str) -> int:
        """Подсчёт через tokens_count, при недоступности — оценка по длине."""
        try:
            return int(self._client.tokens_count([text], model=self.model)[0].tokens)
        except Exception as exc:
            log.warning("GigaChat tokens_count недоступен (%s), оценка по длине", exc)
            return estimate_tokens(text)


class OllamaClient:
    """Клиент локального инференса через Ollama."""

    def __init__(self):
        self.model = config.OLLAMA_MODEL
        self.host = config.OLLAMA_HOST.rstrip("/")

    def generate(self, messages: list[dict], temperature: float = 0.3) -> str:
        """POST на {host}/api/chat, непотоковый ответ, температура роли.

        Возвращает message.content без рассуждений. На любом сбое — короткая
        строка с причиной, а не трассировка: занятие не должно падать.
        """
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature,
                        "num_ctx": OLLAMA_NUM_CTX,
                        "num_predict": OLLAMA_NUM_PREDICT},
        }
        try:
            response = requests.post(f"{self.host}/api/chat", json=payload,
                                     timeout=OLLAMA_TIMEOUT)
        except requests.Timeout:
            log.error("Ollama: нет ответа за %.0f с", OLLAMA_TIMEOUT)
            return f"Ошибка LLM (Ollama): нет ответа за {OLLAMA_TIMEOUT:.0f} с."
        except requests.ConnectionError:
            log.error("Ollama: сервис недоступен по адресу %s", self.host)
            return f"Ошибка LLM (Ollama): сервис недоступен по адресу {self.host}."
        except requests.RequestException as exc:
            log.error("Ollama: %s", exc)
            return f"Ошибка LLM (Ollama): {type(exc).__name__}."

        if response.status_code != 200:
            # Ollama кладёт причину в {"error": "..."}: например, модель не скачана
            try:
                reason = response.json().get("error", "")
            except ValueError:
                reason = response.text[:200]
            log.error("Ollama: HTTP %s %s", response.status_code, reason)
            return f"Ошибка LLM (Ollama): HTTP {response.status_code} {reason}".strip()

        try:
            content = response.json()["message"]["content"]
        except (ValueError, KeyError, TypeError):
            log.error("Ollama: некорректный ответ %r", response.text[:200])
            return "Ошибка LLM (Ollama): некорректный ответ сервиса."

        answer = strip_thinking(content or "")
        if not answer:
            return "Ошибка LLM (Ollama): модель вернула пустой ответ."
        return answer

    def count_tokens(self, text: str) -> int:
        """Оценка по длине строки: сетевой запрос ради оценки не нужен."""
        return estimate_tokens(text)


def build_client() -> LLMClient:
    """Выбрать бэкенд по конфигурации. Готово, менять не нужно."""
    if config.LLM_BACKEND == "ollama":
        return OllamaClient()
    if config.LLM_BACKEND == "gigachat":
        return GigaChatClient()
    if config.LLM_BACKEND == "fake":
        # Импорт внутри ветки: без него fake_llm тянулся бы при любом бэкенде
        from fake_llm import FakeClient

        return FakeClient()
    raise ValueError(
        f"Неизвестный LLM_BACKEND: {config.LLM_BACKEND!r}. "
        f"Допустимо: gigachat | ollama | fake"
    )
