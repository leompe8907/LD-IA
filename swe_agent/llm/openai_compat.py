"""Cliente OpenAI-compatible (OpenAI, OpenRouter, vLLM, Ollama /v1…). Dueño: Claude."""
from __future__ import annotations

import os
import time

from . import _http
from .base import Completion, LLMError


class OpenAICompatClient:
    def __init__(self, model: str, api_key: str | None = None,
                 base_url: str = "https://api.openai.com/v1", *, temperature: float = 0.0,
                 max_tokens: int | None = None, timeout: float = 300.0, retries: int = 3,
                 api_key_env: str = "OPENAI_API_KEY"):
        self.model = model
        self.key = api_key if api_key is not None else os.environ.get(api_key_env, "")
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.temperature, self.max_tokens = temperature, max_tokens
        self.timeout, self.retries = timeout, retries

    def complete(self, messages: list[dict], *, temperature: float | None = None,
                 think: bool | None = None) -> Completion:   # think: no aplica, se ignora
        body = {"model": self.model, "messages": messages,
                "temperature": self.temperature if temperature is None else temperature}
        if self.max_tokens:
            body["max_tokens"] = self.max_tokens
        headers = {"Authorization": f"Bearer {self.key}"} if self.key else {}
        t0 = time.time()
        data = _http.request(self.url, body, headers, timeout=self.timeout, retries=self.retries)
        try:
            msg = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            raise LLMError(f"respuesta inesperada: {str(data)[:300]}")
        text = msg.get("content") or ""
        # vLLM / DeepSeek / Ollama devuelven el razonamiento aparte
        reasoning = msg.get("reasoning_content") or msg.get("reasoning")
        if reasoning:
            text = f"<think>{reasoning}</think>\n{text}"
        usage = data.get("usage") or {}
        return Completion(text=text, model=data.get("model", self.model),
                          prompt_tokens=usage.get("prompt_tokens"),
                          completion_tokens=usage.get("completion_tokens"),
                          duration_s=time.time() - t0)
