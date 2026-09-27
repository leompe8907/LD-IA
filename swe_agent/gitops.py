"""Git del episodio (ADR-005). Dueño: Claude.

El episodio trabaja en su propia rama agent/<timestamp>: la rama del usuario nunca
recibe commits. Cada acción que cambia archivos deja un commit (reversible por paso) y
el parche final es siempre `git diff <baseline>`. Los errores de git no se tragan.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

# Commits del agente con identidad propia: se distinguen de los del usuario y no
# dependen de que user.email esté configurado.
_IDENTITY = ("-c", "user.name=swe-agent", "-c", "user.email=swe-agent@localhost")
# Basura de ejecución que no debe entrar al parche aunque el repo no la ignore
_EXCLUDES = (":(exclude,glob)**/__pycache__/**", ":(exclude,glob)**/*.pyc",
             ":(exclude,glob)**/.pytest_cache/**")


class GitError(Exception):
    """Falla de git; el loop la registra como status 'git_error'."""


class EpisodeGit:
    def __init__(self, repo: Path):
        self.repo = Path(repo)
        self.baseline: str | None = None
        self.branch: str | None = None
        self.original_branch: str | None = None

    def run(self, *args: str, check: bool = True) -> str:
        env = {**os.environ, "GIT_PAGER": "cat", "GIT_TERMINAL_PROMPT": "0"}
        try:
            p = subprocess.run(["git", "-C", str(self.repo), "-c", "core.quotepath=false", *args],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", stdin=subprocess.DEVNULL, env=env, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise GitError(f"git {' '.join(args)}: {e}") from e
        if check and p.returncode != 0:
            raise GitError(f"git {' '.join(args)} (exit {p.returncode}): {p.stderr.strip()}")
        return p.stdout

    # ------------------------------------------------------------------ ciclo de vida

    def start(self, prefix: str = "agent/") -> tuple[str, str]:
        """Valida el repo, fija el baseline y crea la rama del episodio."""
        if self.run("rev-parse", "--is-inside-work-tree", check=False).strip() != "true":
            raise GitError(f"{self.repo} no es un repositorio git")
        if not self.run("rev-parse", "--verify", "-q", "HEAD", check=False).strip():
            raise GitError("el repo no tiene commits: haz un commit inicial primero")
        if self.dirty():
            raise GitError("el repo tiene cambios sin commitear (o archivos sin trackear): "
                           "commitea o haz stash antes de correr el agente")
        self.original_branch = self.run("rev-parse", "--abbrev-ref", "HEAD").strip()
        self.baseline = self.head()
        base = prefix + time.strftime("%Y%m%d-%H%M%S")
        name, i = base, 1
        while self.run("rev-parse", "--verify", "-q", f"refs/heads/{name}", check=False).strip():
            i += 1
            name = f"{base}-{i}"
        self.run("switch", "-q", "-c", name)
        self.branch = name
        return name, self.baseline

    def head(self) -> str:
        return self.run("rev-parse", "HEAD").strip()

    def dirty(self) -> bool:
        return bool(self.run("status", "--porcelain", "--", ".", *_EXCLUDES).strip())

    def commit_if_dirty(self, message: str) -> str | None:
        """Commit de checkpoint; devuelve el sha nuevo o None si no había cambios.
        --no-verify: son checkpoints internos en la rama del agente; los hooks del
        usuario (lint, tests) no deben poder bloquear la reversibilidad por paso."""
        if not self.dirty():
            return None
        self.run("add", "-A", "--", ".", *_EXCLUDES)
        self.run(*_IDENTITY, "commit", "-q", "--no-verify", "-m", message)
        return self.head()

    def patch(self) -> str:
        """Parche completo del episodio respecto al baseline (incluye lo no commiteado)."""
        if self.baseline is None:
            raise GitError("patch() antes de start()")
        self.commit_if_dirty("checkpoint antes de calcular el parche")
        return self.run("diff", self.baseline, "HEAD")
