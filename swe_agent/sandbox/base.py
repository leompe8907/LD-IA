"""Contrato del sandbox (ADR-001, ADR-009). Dueño: Antigravity.

El sandbox SOLO ejecuta comandos. Leer, escribir y hacer git sobre el repo lo hace
el ACI en el host; en Docker el repo del host se monta en /workspace (rw).

Reglas que toda implementación cumple (las verifican los tests de Antigravity):
  * cwd = raíz del repo (Local: cfg.workspace; Docker: /workspace).
  * Shell: bash -c (Local en Windows: Git Bash). El modelo solo conoce bash.
  * stdin cerrado. Entorno mínimo forzado: GIT_PAGER=cat PAGER=cat TERM=dumb NO_COLOR=1
    PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1.
  * output = stdout+stderr intercalados, decodificado UTF-8 (errors="replace"),
    \\r\\n normalizado a \\n, SIN truncar (truncar es trabajo del ACI).
  * Timeout: mata el ÁRBOL de procesos; exit_code=124, timed_out=True, output parcial.
  * run() no lanza por exit != 0 ni por timeout. Solo SandboxError ante fallas de
    infraestructura (daemon caído, imagen ausente, contenedor muerto).
  * close() es idempotente; el loop lo llama siempre en un finally.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ..config import Config


class SandboxError(Exception):
    """Falla de infraestructura del sandbox. El loop la registra como status 'sandbox_error'."""


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    output: str
    timed_out: bool = False
    duration_s: float = 0.0


class Sandbox(Protocol):
    def run(self, cmd: str, timeout: float) -> CommandResult: ...
    def close(self) -> None: ...


class CommandPolicy(Protocol):
    def check(self, cmd: str) -> str | None:
        """None si el comando está permitido; si no, el motivo (se devuelve al modelo).
        Es un freno de primera línea, no la frontera de seguridad: esa es el sandbox."""
        ...


def make_sandbox(cfg: Config) -> Sandbox:
    """Fábrica según cfg.sandbox ("docker" | "local"). Docker: un contenedor por
    episodio, cfg.setup_cmd con red y luego red cortada (ADR-009)."""
    raise NotImplementedError("Antigravity: fase1/antigravity-sandbox")
