"""Contrato de métricas de episodio (ADR-006). Dueño: Antigravity.

Las métricas se derivan SOLO del EventLog (función pura sobre los registros): el loop
no calcula nada, y cualquier log viejo se puede re-analizar.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EpisodeMetrics:
    status: str                  # submitted | stuck | max_steps | llm_error | sandbox_error
    steps: int
    duration_s: float
    mean_step_s: float
    prompt_tokens: int           # suma de Completion.prompt_tokens (o estimación)
    completion_tokens: int
    tokens_estimated: bool       # True si algún paso no traía tokens reales
    prompt_eval_s: float         # tiempo total leyendo prompts (cuello de botella en CPU)
    commits: int
    action_errors: int           # pasos con ok=False
    stuck_warnings: int          # pasos con flag "stuck_warning"
    diff_files: int
    diff_added: int
    diff_removed: int


def compute_metrics(records: list[dict]) -> EpisodeMetrics:
    """records = EventLog.replay(path)."""
    raise NotImplementedError("Antigravity: fase1/antigravity-sandbox")
