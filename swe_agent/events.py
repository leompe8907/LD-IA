"""EventLog append-only (JSONL) — formato compartido por loop, telemetría y replay (ADR-006).

Cada línea es un registro con "type":
  header  -> una vez al inicio: tarea, config, modelo, baseline_sha, branch
  step    -> un Event por paso
  footer  -> una vez al final: status, steps, patch, error opcional
Los observers reciben cada registro justo después de escribirse (logger en vivo, métricas).
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

SCHEMA_VERSION = 1

Observer = Callable[[dict], None]


@dataclass
class Event:
    step: int
    action_raw: str               # respuesta cruda del modelo (con <think>, auditable)
    action: dict | None           # {"cmd": nombre, **args}; None si el parseo falló
    observation: str              # feedback del entorno, ya truncado
    ok: bool
    ts: float = field(default_factory=time.time)
    duration_s: float = 0.0       # paso completo: LLM + ejecución
    llm: dict | None = None       # Completion.meta(): tokens y tiempos, sin el texto
    git_head: str | None = None   # HEAD del repo objetivo tras el paso
    flags: list[str] = field(default_factory=list)  # p.ej. stuck_warning, lint_rejected, multi_action


class EventLog:
    """Vive en el host, fuera del workspace: sobrevive a la muerte del contenedor."""

    def __init__(self, path: Path, observers: tuple[Observer, ...] = ()):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.observers = observers

    def _write(self, record: dict) -> None:
        with self.path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        for obs in self.observers:
            obs(record)

    def header(self, **info) -> None:
        self._write({"type": "header", "schema": SCHEMA_VERSION, "ts": time.time(), **info})

    def append(self, ev: Event) -> None:
        self._write({"type": "step", **asdict(ev)})

    def footer(self, **info) -> None:
        self._write({"type": "footer", "ts": time.time(), **info})

    @staticmethod
    def replay(path: Path) -> list[dict]:
        text = Path(path).read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    @staticmethod
    def steps(path: Path) -> list[Event]:
        return [Event(**{k: v for k, v in r.items() if k != "type"})
                for r in EventLog.replay(path) if r.get("type") == "step"]
