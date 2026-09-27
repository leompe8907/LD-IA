"""Contrato del cliente LLM (ADR-003). Dueño: Claude.

Cualquier proveedor (Ollama nativo, OpenAI-compatible, Anthropic, mock) implementa
LLMClient. El loop nunca sabe cuál está usando.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Protocol


class LLMError(Exception):
    """El proveedor falló tras agotar reintentos. El loop lo registra como status 'llm_error'."""


@dataclass
class Completion:
    text: str                             # respuesta completa; puede incluir <think>...</think>
    model: str = ""
    prompt_tokens: int | None = None      # reales si el proveedor los informa
    completion_tokens: int | None = None
    duration_s: float = 0.0               # latencia total de la llamada
    prompt_eval_s: float | None = None    # tiempo de lectura del prompt (solo Ollama nativo)

    def meta(self) -> dict:
        """Lo que va al EventLog: todo menos el texto (ese ya está en action_raw)."""
        d = asdict(self)
        d.pop("text")
        return d


class LLMClient(Protocol):
    def complete(self, messages: list[dict], *,
                 temperature: float | None = None,
                 think: bool | None = None) -> Completion:
        """messages en formato OpenAI [{"role", "content"}].

        temperature/think: None = default del cliente. Un cliente que no soporte
        una opción la ignora. Debe lanzar LLMError (nunca otra excepción) si falla.
        """
        ...
