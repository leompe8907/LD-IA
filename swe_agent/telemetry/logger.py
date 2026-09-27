"""Logger en vivo para consola (ADR-006, A6). Dueño: Antigravity.

Recibe eventos del EventLog en tiempo real (Observer) y produce una salida compacta,
legible y enriquecida para el operador humano.
"""
from __future__ import annotations

import sys
from typing import TextIO


class ConsoleLogger:
    """Observer para EventLog que imprime el progreso paso a paso en consola."""

    def __init__(self, stream: TextIO | None = None, verbose: bool = False):
        self.stream = stream if stream is not None else sys.stderr
        self.verbose = verbose

    def __call__(self, record: dict) -> None:
        rec_type = record.get("type")
        if rec_type == "header":
            self._log_header(record)
        elif rec_type == "step":
            self._log_step(record)
        elif rec_type == "footer":
            self._log_footer(record)
        self.stream.flush()

    def _log_header(self, r: dict) -> None:
        task = r.get("task", "(sin tarea)")
        if len(task) > 80:
            task = task[:77] + "..."
        model = r.get("model", "local")
        base = str(r.get("baseline_sha", "unknown"))[:7]
        self.stream.write(f"\n🚀 [INICIO] Tarea: {task}\n")
        self.stream.write(f"   Modelo: {model} | Baseline: {base}\n")

    def _log_step(self, r: dict) -> None:
        step = r.get("step", 0)
        dur = float(r.get("duration_s", 0.0))
        act = r.get("action")
        ok = r.get("ok", True)
        git_head = r.get("git_head")
        flags = r.get("flags", [])

        # Identificar acción
        if act and isinstance(act, dict):
            cmd = act.get("cmd", "?")
            if cmd == "bash":
                inp = act.get("input", "")
                detail = inp[:35] + ("..." if len(inp) > 35 else "")
            elif cmd in ("view", "create", "str_replace"):
                detail = act.get("path", "")
            else:
                detail = ""
            action_desc = f"{cmd} {detail}".strip()
        else:
            action_desc = "formato inválido"

        # Resumen de observación (primera línea relevante)
        obs = r.get("observation", "").strip()
        if obs:
            first_line = obs.splitlines()[0]
            obs_preview = first_line[:50] + ("..." if len(first_line) > 50 else "")
        else:
            obs_preview = "(sin salida)"

        status_tag = "✓" if ok else "✗"
        git_tag = f" | git:{str(git_head)[:7]}" if git_head else ""
        flag_tag = f" ⚠️ [{', '.join(flags)}]" if flags else ""

        self.stream.write(
            f"[{step:02d} | {dur:4.1f}s] {action_desc} {status_tag} ➜ {obs_preview}{git_tag}{flag_tag}\n"
        )

    def _log_footer(self, r: dict) -> None:
        status = r.get("status", "unknown")
        steps = r.get("steps", 0)
        err = r.get("error")
        err_str = f" | error: {err}" if err else ""
        self.stream.write(f"🏁 [FIN] Status: {status} | Pasos: {steps}{err_str}\n\n")
