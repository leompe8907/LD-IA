"""Contrato de métricas de episodio (ADR-006, A6). Dueño: Antigravity.

Las métricas se derivan SOLO del EventLog (función pura sobre los registros): el loop
no calcula nada, y cualquier log viejo se puede re-analizar.
"""
from __future__ import annotations

import re
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


def _parse_diff_stats(patch: str) -> tuple[int, int, int]:
    """Extrae (diff_files, diff_added, diff_removed) de un parche unificado o diffstat."""
    if not patch or not patch.strip():
        return 0, 0, 0

    files = set()
    added = 0
    removed = 0

    lines = patch.splitlines()
    for line in lines:
        if line.startswith("+++ b/"):
            files.add(line[6:].strip())
        elif line.startswith("--- a/") and not files:
            files.add(line[6:].strip())
        elif line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
        else:
            # Soporte complementario para formato diffstat: "file.py | 2 +-"
            m = re.match(r"^\s*([^\s|]+)\s+\|\s+(\d+)\s+([+-]+)?", line)
            if m:
                files.add(m.group(1).strip())
                bar = m.group(3) or ""
                added += bar.count("+")
                removed += bar.count("-")

    return len(files), added, removed


def compute_metrics(records: list[dict]) -> EpisodeMetrics:
    """Calcula las métricas de un episodio a partir de la lista de registros del EventLog."""
    header = next((r for r in records if r.get("type") == "header"), None)
    footer = next((r for r in records if r.get("type") == "footer"), None)
    step_records = [r for r in records if r.get("type") == "step"]

    status = footer.get("status", "unknown") if footer else "unknown"
    steps = len(step_records)

    # Duración total: delta ts de header a footer si existen, o suma de duraciones
    if header and footer and "ts" in header and "ts" in footer:
        duration_s = max(0.0, float(footer["ts"]) - float(header["ts"]))
    else:
        duration_s = sum(float(r.get("duration_s", 0.0)) for r in step_records)

    total_step_s = sum(float(r.get("duration_s", 0.0)) for r in step_records)
    mean_step_s = total_step_s / steps if steps > 0 else 0.0

    prompt_tokens = 0
    completion_tokens = 0
    tokens_estimated = False
    prompt_eval_s = 0.0
    action_errors = 0
    stuck_warnings = 0

    curr_git_head = header.get("baseline_sha") if header else None
    commits = 0

    for r in step_records:
        llm = r.get("llm")
        if isinstance(llm, dict) and "prompt_tokens" in llm and "completion_tokens" in llm:
            prompt_tokens += int(llm.get("prompt_tokens", 0))
            completion_tokens += int(llm.get("completion_tokens", 0))
            prompt_eval_s += float(llm.get("prompt_eval_s", 0.0))
        else:
            tokens_estimated = True
            # Estimación fallback: chars / 4
            raw_act = str(r.get("action_raw", ""))
            obs = str(r.get("observation", ""))
            completion_tokens += max(1, len(raw_act) // 4)
            prompt_tokens += max(1, len(obs) // 4)

        if not r.get("ok", True):
            action_errors += 1

        flags = r.get("flags") or []
        if "stuck_warning" in flags:
            stuck_warnings += 1

        # Rastrear commits por cambios en git_head
        gh = r.get("git_head")
        if gh and gh != curr_git_head:
            commits += 1
            curr_git_head = gh

    # Parche y diff
    patch_text = footer.get("patch", "") if footer else ""
    diff_files, diff_added, diff_removed = _parse_diff_stats(patch_text)

    return EpisodeMetrics(
        status=status,
        steps=steps,
        duration_s=round(duration_s, 3),
        mean_step_s=round(mean_step_s, 3),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        tokens_estimated=tokens_estimated,
        prompt_eval_s=round(prompt_eval_s, 3),
        commits=commits,
        action_errors=action_errors,
        stuck_warnings=stuck_warnings,
        diff_files=diff_files,
        diff_added=diff_added,
        diff_removed=diff_removed,
    )
