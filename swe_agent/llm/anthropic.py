"""Cliente de la API de Anthropic (Messages), solo stdlib. Dueño: Claude."""
from __future__ import annotations

import os
import time

from . import _http
from .base import Completion, LLMError

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"


class AnthropicClient:
    def __init__(self, model: str = "claude-opus-5-5", api_key: str | None = None, *,
                 base_url: str = API_URL, max_tokens: int = 4_096,
                 temperature: float | None = 0.0, timeout: float = 300.0, retries: int = 3):
        self.model = model
        self.key = api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY", "")
        self.url, self.max_tokens, self.temperature = base_url, max_tokens, temperature
        self.timeout, self.retries = timeout, retries

    def complete(self, messages: list[dict], *, temperature: float | None = None,
                 think: bool | None = None) -> Completion:   # think: no se usa en la Fase 1
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        body: dict = {"model": self.model, "max_tokens": self.max_tokens,
                      "messages": [{"role": m["role"], "content": m["content"]}
                                   for m in messages if m["role"] != "system"]}
        if system:
            body["system"] = system
        temp = self.temperature if temperature is None else temperature
        if temp is not None:
            body["temperature"] = temp
        headers = {"x-api-key": self.key, "anthropic-version": API_VERSION}
        t0 = time.time()
        data = _http.request(self.url, body, headers, timeout=self.timeout, retries=self.retries)
        blocks = data.get("content")
        if not isinstance(blocks, list):
            raise LLMError(f"respuesta inesperada: {str(data)[:300]}")
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        usage = data.get("usage") or {}
        return Completion(text=text, model=data.get("model", self.model),
                          prompt_tokens=usage.get("input_tokens"),
                          completion_tokens=usage.get("output_tokens"),
                          duration_s=time.time() - t0)
