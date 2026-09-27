"""Cliente nativo de Ollama (/api/chat). Dueño: Claude.

Frente al endpoint OpenAI-compatible permite, por petición: num_ctx (sin Modelfile),
think on/off (Qwen3), num_gpu (offload a la GTX 1650) y leer los tiempos reales de
lectura del prompt vs generación, que es lo que más importa en este hardware.
"""
from __future__ import annotations

import json
import time
from typing import Iterator

from . import _http
from .base import Completion, LLMError

NS = 1e9


class OllamaClient:
    def __init__(self, model: str, base_url: str = "http://localhost:11434", *,
                 num_ctx: int = 16_384, temperature: float = 0.0, think: bool | None = False,
                 num_predict: int = 2_048, num_gpu: int | None = None,
                 num_thread: int | None = None, keep_alive: str = "30m",
                 timeout: float = 900.0, retries: int = 2, stream: bool = True,
                 options: dict | None = None):
        self.model, self.url = model, base_url.rstrip("/") + "/api/chat"
        self.temperature, self.think = temperature, think
        self.keep_alive, self.timeout, self.retries, self.stream = keep_alive, timeout, retries, stream
        self.options = {"num_ctx": num_ctx, "num_predict": num_predict, **(options or {})}
        if num_gpu is not None:
            self.options["num_gpu"] = num_gpu          # 0 = solo CPU; None = automático
        if num_thread is not None:
            self.options["num_thread"] = num_thread

    def complete(self, messages: list[dict], *, temperature: float | None = None,
                 think: bool | None = None) -> Completion:
        body = {"model": self.model, "messages": messages, "stream": self.stream,
                "keep_alive": self.keep_alive,
                "options": {**self.options,
                            "temperature": self.temperature if temperature is None else temperature}}
        think = self.think if think is None else think
        if think is not None:              # solo si se pide: modelos sin thinking dan 400
            body["think"] = think
        t0 = time.time()
        data = _http.request(self.url, body, {}, timeout=self.timeout, retries=self.retries,
                             stream=self.stream, read=_read_stream)
        return _to_completion(data, self.model, time.time() - t0)


def _read_stream(lines: Iterator[bytes]) -> dict:
    """Junta los trozos NDJSON en una respuesta equivalente a stream=false."""
    content, thinking, last = [], [], {}
    for raw in lines:
        if not raw.strip():
            continue
        chunk = json.loads(raw.decode("utf-8"))
        if "error" in chunk:
            raise LLMError(f"Ollama: {chunk['error']}")
        msg = chunk.get("message") or {}
        content.append(msg.get("content") or "")
        thinking.append(msg.get("thinking") or "")
        last = chunk
        if chunk.get("done"):
            break
    if not last.get("done"):
        raise LLMError("Ollama cortó el stream antes de terminar")
    return {**last, "message": {"role": "assistant", "content": "".join(content),
                                "thinking": "".join(thinking)}}


def _to_completion(data: dict, model: str, elapsed: float) -> Completion:
    if "error" in data:
        raise LLMError(f"Ollama: {data['error']}")
    msg = data.get("message") or {}
    text = msg.get("content") or ""
    if msg.get("thinking"):            # se conserva en el log; el parser lo quita
        text = f"<think>{msg['thinking']}</think>\n{text}"
    ped = data.get("prompt_eval_duration")
    return Completion(text=text, model=data.get("model", model),
                      prompt_tokens=data.get("prompt_eval_count"),
                      completion_tokens=data.get("eval_count"),
                      duration_s=elapsed,
                      prompt_eval_s=ped / NS if ped is not None else None)
