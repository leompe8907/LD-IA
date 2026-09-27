"""LocalSandbox: ejecución de comandos en el host con Git Bash / bash (ADR-001, A1). Dueño: Antigravity.

Reglas cumplidas:
  * cwd = cfg.workspace
  * Shell: bash -c (En Windows: Git Bash, NUNCA WindowsApps\\bash.exe)
  * stdin cerrado (DEVNULL)
  * Entorno mínimo forzado: GIT_PAGER=cat PAGER=cat TERM=dumb NO_COLOR=1 PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1
  * output = stdout+stderr intercalados, decodificado UTF-8 (errors="replace"), \\r\\n normalizado a \\n, SIN truncar
  * Timeout: mata el ÁRBOL de procesos; exit_code=124, timed_out=True, output parcial
  * run() no lanza por exit != 0 ni por timeout. Solo SandboxError ante fallas de infraestructura
  * close() es idempotente
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from .base import CommandResult, Sandbox, SandboxError

if TYPE_CHECKING:
    from ..config import Config


def find_bash_executable() -> str:
    """Localiza el ejecutable de bash adecuado.

    En Windows, busca explícitamente Git Bash y rechaza WindowsApps\\bash.exe
    (que iniciaría WSL).
    """
    if sys.platform != "win32":
        bash = shutil.which("bash")
        if bash:
            return bash
        raise SandboxError("No se encontró 'bash' en el PATH del sistema")

    # 1. Rutas canónicas de Git for Windows
    candidates = [
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
        r"C:\Program Files (x86)\Git\usr\bin\bash.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Git\bin\bash.exe"),
        os.path.expandvars(r"%PROGRAMFILES%\Git\bin\bash.exe"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c

    # 2. Deducir desde git.exe en PATH
    git_cmd = shutil.which("git")
    if git_cmd:
        p = Path(git_cmd).resolve()
        for parent in (p.parent, p.parent.parent):
            b1 = parent / "bin" / "bash.exe"
            b2 = parent / "usr" / "bin" / "bash.exe"
            if b1.is_file():
                return str(b1)
            if b2.is_file():
                return str(b2)

    # 3. Fallback a shutil.which('bash') SOLO si no apunta a WindowsApps (WSL launcher)
    which_bash = shutil.which("bash")
    if which_bash and "WindowsApps" not in which_bash:
        return which_bash

    raise SandboxError(
        "No se encontró Git Bash en Windows. Se requiere Git for Windows (C:\\Program Files\\Git\\bin\\bash.exe)."
    )


class LocalSandbox(Sandbox):
    """Sandbox local para desarrollo rápido en host."""

    def __init__(self, cfg: Config):
        self.workspace = Path(cfg.workspace).resolve()
        if not self.workspace.is_dir():
            raise SandboxError(f"El workspace no existe o no es un directorio: {self.workspace}")

        self.bash_path = find_bash_executable()
        self._closed = False

    def run(self, cmd: str, timeout: float) -> CommandResult:
        if self._closed:
            raise SandboxError("El sandbox ya ha sido cerrado")

        start_t = time.perf_counter()

        env = os.environ.copy()
        env.update(
            {
                "GIT_PAGER": "cat",
                "PAGER": "cat",
                "TERM": "dumb",
                "NO_COLOR": "1",
                "PYTHONIOENCODING": "utf-8",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )

        # En POSIX creamos un process group nuevo para matar a los hijos
        kwargs: dict = {
            "cwd": self.workspace,
            "env": env,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
        }
        if sys.platform != "win32":
            kwargs["preexec_fn"] = os.setsid

        try:
            p = subprocess.Popen([self.bash_path, "-c", cmd], **kwargs)
        except OSError as e:
            raise SandboxError(f"Error al iniciar el proceso bash: {e}") from e

        try:
            raw_out, _ = p.communicate(timeout=timeout)
            duration_s = time.perf_counter() - start_t
            out_str = (raw_out or b"").decode("utf-8", errors="replace").replace("\r\n", "\n")
            return CommandResult(
                exit_code=p.returncode,
                output=out_str,
                timed_out=False,
                duration_s=duration_s,
            )
        except subprocess.TimeoutExpired:
            self._kill_process_tree(p)
            try:
                raw_out, _ = p.communicate(timeout=2.0)
            except Exception:
                raw_out = b""
            duration_s = time.perf_counter() - start_t
            out_str = (raw_out or b"").decode("utf-8", errors="replace").replace("\r\n", "\n")
            return CommandResult(
                exit_code=124,
                output=out_str,
                timed_out=True,
                duration_s=duration_s,
            )

    def _kill_process_tree(self, proc: subprocess.Popen) -> None:
        """Termina forzosamente el proceso y todos sus procesos hijos."""
        if proc.poll() is not None:
            return

        if sys.platform == "win32":
            # taskkill /F /T termina el PID y todo su árbol de procesos
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                check=False,
            )
        else:
            try:
                pgid = os.getpgid(proc.pid)
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        try:
            proc.kill()
        except Exception:
            pass

    def close(self) -> None:
        """Cierre idempotente del sandbox."""
        self._closed = True
