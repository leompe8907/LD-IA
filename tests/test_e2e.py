"""C9 — integración real de las dos mitades: loop de Claude + sandbox/telemetría de Antigravity.

Los tests xfail(strict) documentan pedidos abiertos a Antigravity (docs/DECISIONES.md):
cuando se arreglen, pasan a fallar como XPASS y hay que quitarles la marca.
"""
from __future__ import annotations

import io
import os
import sys
from pathlib import Path

import pytest

import swe_agent_core
from swe_agent.config import Config
from swe_agent.events import EventLog
from swe_agent.llm.base import Completion
from swe_agent.llm.mock import ScriptedLLM
from swe_agent.loop import run_episode
from swe_agent.sandbox import make_sandbox
from swe_agent.sandbox.policy import DefaultPolicy
from swe_agent.telemetry import ConsoleLogger, compute_metrics

from .fakes import find_bash

FIX = "<<str_replace path=calc.py>>\n<<OLD>>\n    return a + b  # BUG\n<<NEW>>\n    return a * b\n<<END>>"
PYTEST = "<<bash>>\npython -m pytest -q\n<<END>>"
needs_bash = pytest.mark.skipif(find_bash() is None, reason="se necesita Git Bash")


class ConTokens(ScriptedLLM):
    """Como Ollama: informa tokens y tiempos reales."""
    def complete(self, messages, **kw):
        c = super().complete(messages)
        return Completion(c.text, model="fake", prompt_tokens=800, completion_tokens=20,
                          duration_s=0.1, prompt_eval_s=0.05)


@needs_bash
def test_episodio_real_con_local_sandbox_policy_logger_y_metricas(make_repo, log_dir, monkeypatch):
    # LocalSandbox hereda el PATH del host: que `python` sea el del .venv (tiene pytest)
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"])
    cfg = Config(workspace=make_repo(), log_dir=log_dir, sandbox="local", max_steps=10)
    out = io.StringIO()
    llm = ConTokens(["<<view path=calc.py>>", PYTEST, FIX, PYTEST, "<<submit>>"])
    res = run_episode("Arregla calc.mul", llm, cfg, policy=DefaultPolicy(),
                      observers=(ConsoleLogger(stream=out),))     # sandbox vía make_sandbox
    assert res.status == "submitted", res
    steps = EventLog.steps(res.log_path)
    assert "1 failed" in steps[1].observation and "1 passed" in steps[3].observation
    m = compute_metrics(EventLog.replay(res.log_path))
    assert (m.status, m.steps, m.prompt_tokens, m.diff_files) == ("submitted", 5, 4_000, 1)
    assert "Status: submitted" in out.getvalue()


@needs_bash
def test_fachada_acepta_llm_viejo_que_devuelve_str(make_repo, log_dir):
    class Viejo:
        def __init__(self):
            self.s = ['<<ACTION>>{"cmd":"view","path":"calc.py"}<<END>>', FIX, "<<submit>>"]
        def complete(self, messages):
            return self.s.pop(0)
    cfg = swe_agent_core.Config(workspace=make_repo(), log_dir=log_dir, sandbox="local")
    assert swe_agent_core.run_episode("x", Viejo(), cfg).status == "submitted"


@needs_bash
def test_timeout_real_de_local_sandbox_llega_como_observacion(make_repo, log_dir):
    cfg = Config(workspace=make_repo(), log_dir=log_dir, sandbox="local", bash_timeout=1)
    sb = make_sandbox(cfg)
    try:
        r = sb.run("sleep 5", timeout=1)
    finally:
        sb.close()
    assert r.timed_out and r.exit_code == 124


# --------------------------------------------------------------------------- pedidos abiertos

@pytest.mark.xfail(strict=True, reason="pedido a Antigravity: compute_metrics con tokens None")
def test_metricas_toleran_proveedores_sin_tokens(make_repo, log_dir):
    cfg = Config(workspace=make_repo(), log_dir=log_dir, sandbox="local")
    from .fakes import ScriptedSandbox
    res = run_episode("x", ScriptedLLM([FIX, "<<submit>>"]), cfg, sandbox=ScriptedSandbox())
    m = compute_metrics(EventLog.replay(res.log_path))    # Completion.meta() trae None
    assert m.tokens_estimated


@pytest.mark.xfail(strict=True, reason="pedido a Antigravity: policy separa operadores entre comillas")
@pytest.mark.parametrize("cmd", ['grep -nE "foo|bar" calc.py',
                                 'python -c "import sys; print(sys.version)"',
                                 "echo 'a && b'"])
def test_policy_no_bloquea_operadores_entre_comillas(cmd):
    assert DefaultPolicy().check(cmd) is None
