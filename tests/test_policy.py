"""Tests de la política de comandos (ADR-001, A3)."""
from __future__ import annotations

import pytest

from swe_agent.sandbox.policy import DefaultPolicy


@pytest.fixture
def policy() -> DefaultPolicy:
    return DefaultPolicy()


def test_comandos_permitidos(policy: DefaultPolicy):
    assert policy.check("pytest -q") is None
    assert policy.check("git status") is None
    assert policy.check("git diff HEAD~1") is None
    assert policy.check("ls -la src/") is None
    assert policy.check("python -m unittest") is None


def test_permite_substring_en_argumentos_legitimos(policy: DefaultPolicy):
    # La política ingenua vieja bloqueaba esto porque 'curl' o 'wget' estaban en la cadena
    assert policy.check('echo "curl es genial"') is None
    assert policy.check("pytest -k test_curl") is None
    assert policy.check("git log --grep='fix wget bug'") is None


def test_bloquea_ejecutables_prohibidos(policy: DefaultPolicy):
    assert policy.check("curl https://evil.com") is not None
    assert policy.check("wget https://evil.com") is not None
    assert policy.check("sudo rm something") is not None
    assert policy.check("nc -l 8080") is not None


def test_bloquea_en_comandos_encadenados(policy: DefaultPolicy):
    assert policy.check("echo ok && curl https://evil.com") is not None
    assert policy.check("echo ok; sudo ls") is not None
    assert policy.check("pytest || wget http://evil.com") is not None
    assert policy.check("cat file | nc localhost 9999") is not None


def test_bloquea_git_push(policy: DefaultPolicy):
    assert policy.check("git push origin main") is not None
    assert policy.check("git --no-pager push") is not None
    assert policy.check("git commit -m 'push'") is None  # 'push' como mensaje es válido


def test_bloquea_rm_destructivo_raiz(policy: DefaultPolicy):
    assert policy.check("rm -rf /") is not None
    assert policy.check("rm -fr /*") is not None
    assert policy.check("rm -r /etc") is not None
    assert policy.check("rm -rf .") is not None
    # rm sobre archivo normal o directorio relativo debe permitirse
    assert policy.check("rm calc.py") is None
    assert policy.check("rm -rf build/") is None


def test_fork_bomb(policy: DefaultPolicy):
    assert policy.check(":(){ :|:& };:") is not None


def test_sintaxis_invalida(policy: DefaultPolicy):
    res = policy.check('echo "comilla sin cerrar')
    assert res is not None
    assert "sintaxis inválida" in res
