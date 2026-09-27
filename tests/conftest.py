"""Fixtures compartidas (cambios solo aditivos; ver docs/DECISIONES.md).

tmp_path lo limpia pytest, que ya maneja los objetos read-only de .git en Windows
(el shutil.rmtree del test_core.py original fallaba justo ahí).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

BUGGY_CALC = {
    "calc.py": "def mul(a, b):\n    return a + b  # BUG\n",
    "test_calc.py": "from calc import mul\n\ndef test_mul():\n    assert mul(3, 4) == 12\n",
}


def git(repo: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", check=True)
    return p.stdout


@pytest.fixture
def make_repo(tmp_path: Path):
    """make_repo(files) -> Path de un repo git con un commit inicial en 'main'.
    Escribe con LF explícito para que los tests sean iguales en Windows y Linux."""
    def _make(files: dict[str, str] | None = None, name: str = "repo") -> Path:
        repo = tmp_path / name
        repo.mkdir()
        for rel, content in (files if files is not None else BUGGY_CALC).items():
            p = repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8", newline="\n")
        git(repo, "init", "-q", "-b", "main")
        git(repo, "config", "user.email", "test@example.com")
        git(repo, "config", "user.name", "test")
        git(repo, "config", "core.autocrlf", "false")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "init")
        return repo
    return _make


@pytest.fixture
def log_dir(tmp_path: Path) -> Path:
    """Directorio de logs FUERA del repo (ADR-006)."""
    return tmp_path / "logs"


def docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


def pytest_collection_modifyitems(config, items):
    """Los tests marcados @pytest.mark.docker se saltan si el daemon no responde."""
    if any("docker" in it.keywords for it in items) and not docker_available():
        skip = pytest.mark.skip(reason="daemon de Docker no disponible")
        for it in items:
            if "docker" in it.keywords:
                it.add_marker(skip)
