"""Tests de DockerSandbox (ADR-001, ADR-009, A2).

Marcado con @pytest.mark.docker: se ejecutan si el daemon de Docker está activo,
o se saltan limpiamente si no lo está (ADR-011).
"""
from __future__ import annotations

import pytest

from swe_agent.config import Config
from swe_agent.sandbox.base import CommandResult, SandboxError
from swe_agent.sandbox.docker import DockerSandbox, is_docker_available


def test_docker_daemon_no_disponible_levanta_sandbox_error(make_repo, log_dir, monkeypatch):
    """Si Docker no responde, debe levantar un SandboxError claro (ADR-009)."""
    # Forzar is_docker_available a False
    monkeypatch.setattr("swe_agent.sandbox.docker.is_docker_available", lambda: False)
    cfg = Config(workspace=make_repo(), log_dir=log_dir, sandbox="docker")
    with pytest.raises(SandboxError, match="Docker no está disponible o el daemon está detenido"):
        DockerSandbox(cfg)


@pytest.mark.docker
def test_docker_sandbox_ejecucion_real(make_repo, log_dir):
    """Test real contra Docker si el daemon está corriendo."""
    if not is_docker_available():
        pytest.skip("Docker daemon no disponible en este entorno")

    repo = make_repo()
    cfg = Config(workspace=repo, log_dir=log_dir, sandbox="docker")
    sb = DockerSandbox(cfg)
    try:
        res = sb.run("echo 'hola docker'", timeout=10.0)
        assert isinstance(res, CommandResult)
        assert res.exit_code == 0
        assert "hola docker" in res.output
        assert not res.timed_out
    finally:
        sb.close()


@pytest.mark.docker
def test_docker_sandbox_timeout_en_contenedor(make_repo, log_dir):
    """Valida que timeout -s KILL termine el comando en el contenedor y retorne 124."""
    if not is_docker_available():
        pytest.skip("Docker daemon no disponible en este entorno")

    repo = make_repo()
    cfg = Config(workspace=repo, log_dir=log_dir, sandbox="docker")
    sb = DockerSandbox(cfg)
    try:
        res = sb.run("sleep 15", timeout=1.0)
        assert res.timed_out is True
        assert res.exit_code == 124
    finally:
        sb.close()
