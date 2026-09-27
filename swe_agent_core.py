"""swe_agent_core — fachada del núcleo original (~280 líneas) sobre el paquete swe_agent/.

El código vive ahora en swe_agent/ (ver docs/DECISIONES.md). Este módulo mantiene los
nombres de antes para scripts existentes. Diferencias deliberadas con el núcleo viejo:
  * run_episode devuelve EpisodeResult (antes: dict) y trabaja en una rama agent/<ts>.
  * El formato de acción enseñado es el de bloques crudos; <<ACTION>>{json}<<END>> se
    sigue aceptando.
  * Un LLM cuyo complete() devuelve str (API vieja) se adapta automáticamente.
"""
from __future__ import annotations

from pathlib import Path

from swe_agent.aci.protocol import ActionError, parse_action
from swe_agent.config import Config
from swe_agent.events import Event, EventLog
from swe_agent.llm import Completion, LLMClient, LLMError
from swe_agent.llm.anthropic import AnthropicClient
from swe_agent.llm.ollama import OllamaClient
from swe_agent.llm.openai_compat import OpenAICompatClient
from swe_agent.loop import EpisodeResult
from swe_agent.loop import run_episode as _run_episode
from swe_agent.loop.prompts import SYSTEM_PROMPT

__all__ = ["ActionError", "AnthropicClient", "Completion", "Config", "EpisodeResult", "Event",
           "EventLog", "LLMClient", "LLMError", "OllamaClient", "OpenAICompatClient",
           "SYSTEM_PROMPT", "parse_action", "run_episode"]


class _CompatLLM:
    """Adapta clientes viejos (complete -> str) al contrato nuevo (complete -> Completion)."""

    def __init__(self, inner):
        self.inner = inner
        self.model = getattr(inner, "model", type(inner).__name__)

    def complete(self, messages, **kw):
        out = self.inner.complete(messages)
        return out if isinstance(out, Completion) else Completion(str(out), model=self.model)


def run_episode(task: str, llm, cfg: Config, log_path: Path | None = None, **kw) -> EpisodeResult:
    return _run_episode(task, _CompatLLM(llm), cfg, log_path=log_path, **kw)
