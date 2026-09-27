"""Tests de LocalSandbox (ADR-001, A1)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from swe_agent.config import Config
from swe_agent.sandbox.base import CommandResult, SandboxError
from swe_agent.sandbox.local import LocalSandbox, find_bash_executable


def test_find_bash_executable():
    bash = find_bash_executable()
    assert os.path.isfile(bash)
    assert "bash" in bash.lower()
    # En Windows no debe ser WindowsApps (WSL launcher)
    if os.name == "nt":
        assert "windowsapps" not in bash.lower()


def test_localsandbox_ejecuta_comando_exitoso(make_repo, log_dir):
    repo = make_repo()
    cfg = Config(workspace=repo, log_dir=log_dir, sandbox="local")
    sb = LocalSandbox(cfg)
    try:
        res = sb.run("echo 'hola mundo'", timeout=10.0)
        assert isinstance(res, CommandResult)
        assert res.exit_code == 0
        assert "hola mundo" in res.output
        assert not res.timed_out
        assert res.duration_s > 0
    finally:
        sb.close()


def test_localsandbox_captura_stderr_y_exit_code_no_cero(make_repo, log_dir):
    repo = make_repo()
    cfg = Config(workspace=repo, log_dir=log_dir, sandbox="local")
    sb = LocalSandbox(cfg)
    try:
        res = sb.run("echo 'error test' >&2 && exit 42", timeout=10.0)
        assert res.exit_code == 42
        assert "error test" in res.output
        assert not res.timed_out
    finally:
        sb.close()


def test_localsandbox_entorno_minimo_forzado(make_repo, log_dir):
    repo = make_repo()
    cfg = Config(workspace=repo, log_dir=log_dir, sandbox="local")
    sb = LocalSandbox(cfg)
    try:
        res = sb.run("echo PAGER=$PAGER NO_COLOR=$NO_COLOR PYTHONIOENCODING=$PYTHONIOENCODING", timeout=10.0)
        assert res.exit_code == 0
        assert "PAGER=cat" in res.output
        assert "NO_COLOR=1" in res.output
        assert "PYTHONIOENCODING=utf-8" in res.output
    finally:
        sb.close()


def test_localsandbox_cwd_es_workspace(make_repo, log_dir):
    repo = make_repo()
    cfg = Config(workspace=repo, log_dir=log_dir, sandbox="local")
    sb = LocalSandbox(cfg)
    try:
        res = sb.run("pwd", timeout=10.0)
        assert res.exit_code == 0
        # Normalizado: el path de bash debe apuntar al repo
        # en Git Bash /c/Users/... o similar
        output_lower = res.output.lower().replace("\\", "/")
        repo_name = repo.name.lower()
        assert repo_name in output_lower
    finally:
        sb.close()


def test_localsandbox_normaliza_crlf_a_lf(make_repo, log_dir):
    repo = make_repo()
    cfg = Config(workspace=repo, log_dir=log_dir, sandbox="local")
    sb = LocalSandbox(cfg)
    try:
        res = sb.run("printf 'linea1\\r\\nlinea2\\r\\n'", timeout=10.0)
        assert "\r\n" not in res.output
        assert "linea1\nlinea2\n" in res.output
    finally:
        sb.close()


def test_localsandbox_timeout_mata_arbol_y_devuelve_124(make_repo, log_dir):
    repo = make_repo()
    cfg = Config(workspace=repo, log_dir=log_dir, sandbox="local")
    sb = LocalSandbox(cfg)
    try:
        # sleep 10 con timeout 0.5
        res = sb.run("sleep 10", timeout=0.5)
        assert res.timed_out is True
        assert res.exit_code == 124
    finally:
        sb.close()


def test_localsandbox_close_es_idempotente(make_repo, log_dir):
    repo = make_repo()
    cfg = Config(workspace=repo, log_dir=log_dir, sandbox="local")
    sb = LocalSandbox(cfg)
    sb.close()
    sb.close()  # no debe lanzar error
    with pytest.raises(SandboxError, match="cerrado"):
        sb.run("echo 1", timeout=5.0)
