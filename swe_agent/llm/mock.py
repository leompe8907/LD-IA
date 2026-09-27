"""LLM con guion fijo para tests y ensayos sin modelo. Dueño: Claude."""
from __future__ import annotations

from .base import Completion, LLMError


class ScriptedLLM:
    def __init__(self, script: list[str], model: str = "scripted"):
        self.script, self.model = list(script), model
        self.calls: list[list[dict]] = []      # mensajes recibidos, para inspeccionar en tests

    def complete(self, messages: list[dict], *, temperature: float | None = None,
                 think: bool | None = None) -> Completion:
        self.calls.append(messages)
        if not self.script:
            raise LLMError("guion agotado")
        return Completion(self.script.pop(0), model=self.model)
