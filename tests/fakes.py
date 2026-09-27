"""Dobles de prueba de Claude (sandbox) hasta integrar los reales de Antigravity."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from swe_agent.sandbox.base import CommandResult


class ScriptedSandbox:
    """Devuelve resultados predefinidos y registra los comandos recibidos."""

    def __init__(self, results: list[CommandResult] | None = None):
        self.results = list(results or [])
        self.commands: list[str] = []
        self.closed = False

    def run(self, cmd: str, timeout: float) -> CommandResult:
        self.commands.append(cmd)
        return self.results.pop(0) if self.results else CommandResult(0, "")

    def close(self) -> None:
        self.closed = True


def find_bash() -> str | None:
    """Git Bash en Windows (NO el bash.exe de WindowsApps, que lanza WSL)."""
    if os.name != "nt":
        return shutil.which("bash")
    git = shutil.which("git")
    if git:
        for cand in (Path(git).parents[1] / "bin" / "bash.exe",
                     Path(git).parents[1] / "usr" / "bin" / "bash.exe"):
            if cand.is_file():
                return str(cand)
    return None


class HostSandbox:
    """Ejecuta bash de verdad en el host (tests del loop con pytest real)."""

    def __init__(self, workdir: Path):
        self.workdir, self.bash = Path(workdir), find_bash()
        self.env = {**os.environ, "GIT_PAGER": "cat", "PAGER": "cat", "TERM": "dumb",
                    "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8",
                    # que `python` y `pytest` resuelvan al .venv que corre los tests
                    "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]}

    def run(self, cmd: str, timeout: float) -> CommandResult:
        t0 = time.time()
        try:
            p = subprocess.run([self.bash, "-c", cmd], cwd=self.workdir, env=self.env,
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            out = (e.output or b"").decode("utf-8", "replace")
            return CommandResult(124, out, True, time.time() - t0)
        out = p.stdout.decode("utf-8", "replace").replace("\r\n", "\n")
        return CommandResult(p.returncode, out, False, time.time() - t0)

    def close(self) -> None:
        pass
