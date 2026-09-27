"""C6 — loop completo: los 4 criterios del núcleo original y los nuevos."""
from __future__ import annotations

import pytest

from swe_agent.config import Config
from swe_agent.events import EventLog
from swe_agent.gitops import GitError
from swe_agent.llm.base import Completion, LLMError
from swe_agent.llm.mock import ScriptedLLM
from swe_agent.loop import run_episode
from swe_agent.sandbox.base import CommandResult, SandboxError

from .conftest import git
from .fakes import HostSandbox, ScriptedSandbox, find_bash

FIX = "<<str_replace path=calc.py>>\n<<OLD>>\n    return a + b  # BUG\n<<NEW>>\n    return a * b\n<<END>>"
PYTEST = "<<bash>>\npython -m pytest -q\n<<END>>"


@pytest.fixture
def cfg(make_repo, log_dir):
    return Config(workspace=make_repo(), log_dir=log_dir, sandbox="local", max_steps=15)


@pytest.mark.skipif(find_bash() is None, reason="se necesita bash (Git Bash en Windows)")
def test_episodio_completo_con_pytest_real(cfg):
    """Criterio 2: view -> test falla -> fix -> test pasa -> submit, commits y replay."""
    llm = ScriptedLLM(["<think>veamos</think>\n<<view path=calc.py>>", PYTEST, FIX, PYTEST,
                       "<<submit>>"])
    res = run_episode("Arregla calc.mul", llm, cfg, sandbox=HostSandbox(cfg.workspace))

    assert res.status == "submitted" and res.steps == 5, res
    steps = EventLog.steps(res.log_path)
    assert "1 failed" in steps[1].observation and "1 passed" in steps[3].observation
    assert "+    return a * b" in res.patch
    assert "return a * b" in (cfg.workspace / "calc.py").read_text()
    # un commit por la edición, en la rama del agente; main intacta
    log = git(cfg.workspace, "log", "--format=%s", f"{res.baseline_sha}..HEAD").split("\n")
    assert "paso 2: str_replace calc.py" in log
    assert git(cfg.workspace, "rev-parse", "main").strip() == res.baseline_sha
    # replay: header, 5 pasos, footer; el log vive fuera del repo
    types = [r["type"] for r in EventLog.replay(res.log_path)]
    assert types == ["header"] + ["step"] * 5 + ["footer"]
    assert not res.log_path.is_relative_to(cfg.workspace)
    assert git(cfg.workspace, "status", "--porcelain") == ""


def test_stuck_aborta_en_3_pasos_identicos(cfg):
    """Criterio 3 (mismo guion del test original, formato legacy incluido)."""
    llm = ScriptedLLM(['r<<ACTION>>{"cmd":"bash","input":"false"}<<END>>'] * 10)
    res = run_episode("x", llm, cfg, sandbox=ScriptedSandbox([CommandResult(1, "")] * 10))
    assert res.status == "stuck" and res.steps == 3
    flags = [e.flags for e in EventLog.steps(res.log_path)]
    assert "stuck_warning" in flags[1]


def test_formatos_invalidos_se_recuperan(cfg):
    """Criterio 4 + los casos que antes crasheaban el episodio."""
    llm = ScriptedLLM(["sin accion", '<<ACTION>>{"cmd":"nope"}<<END>>',
                       '<<ACTION>>{"cmd":"view"}<<END>>', "<<view path=calc.py offset=abc>>",
                       "<think>no termina", FIX, "<<submit>>"])
    res = run_episode("x", llm, cfg, sandbox=ScriptedSandbox())
    assert res.status == "submitted"
    evs = EventLog.steps(res.log_path)
    assert [e.ok for e in evs] == [False] * 5 + [True, True]
    assert all(e.observation.startswith("ERROR") for e in evs[:5])


def test_error_interno_del_aci_no_mata_el_episodio(cfg):
    class Explota(ScriptedSandbox):
        def run(self, cmd, timeout):
            raise RuntimeError("bug")
    llm = ScriptedLLM(["<<bash>>\nls\n<<END>>", FIX, "<<submit>>"])
    res = run_episode("x", llm, cfg, sandbox=Explota())
    evs = EventLog.steps(res.log_path)
    assert res.status == "submitted" and "internal_error" in evs[0].flags


def test_varias_acciones_y_lint_quedan_marcadas(cfg):
    bad_fix = "<<str_replace path=calc.py>>\n<<OLD>>\n    return a * b\n<<NEW>>\n    return (a * b\n<<END>>"
    llm = ScriptedLLM([f"{FIX}\n<<submit>>", bad_fix, "<<submit>>"])
    res = run_episode("x", llm, cfg, sandbox=ScriptedSandbox())
    evs = EventLog.steps(res.log_path)
    assert "multi_action" in evs[0].flags and "solo se ejecutó la primera" in evs[0].observation
    assert "lint_rejected" in evs[1].flags and res.status == "submitted"


def test_llm_y_sandbox_caidos_son_status_no_excepciones(cfg, make_repo, log_dir):
    class Caido:
        model = "x"
        def complete(self, messages, **kw):
            raise LLMError("Ollama no responde")
    res = run_episode("x", Caido(), cfg, sandbox=ScriptedSandbox())
    assert res.status == "llm_error" and "Ollama" in res.error

    class SinDocker(ScriptedSandbox):
        def run(self, cmd, timeout):
            raise SandboxError("daemon caído")
    cfg2 = Config(workspace=make_repo(name="r2"), log_dir=log_dir, sandbox="local")
    sb = SinDocker()
    res2 = run_episode("x", ScriptedLLM(["<<bash>>\nls\n<<END>>"]), cfg2, sandbox=sb)
    assert res2.status == "sandbox_error"
    assert EventLog.replay(res2.log_path)[-1]["type"] == "footer"


def test_max_steps_y_tokens_reales_en_el_log(cfg):
    class ConTokens(ScriptedLLM):
        def complete(self, messages, **kw):
            c = super().complete(messages)
            return Completion(c.text, prompt_tokens=1234, completion_tokens=7, prompt_eval_s=2.5)
    cfg.max_steps = 2
    res = run_episode("x", ConTokens(["<<view path=calc.py>>", "<<view path=.>>"]), cfg,
                      sandbox=ScriptedSandbox())
    assert res.status == "max_steps"
    ev = EventLog.steps(res.log_path)[0]
    assert ev.llm["prompt_tokens"] == 1234 and ev.llm["prompt_eval_s"] == 2.5


def test_repo_sucio_no_arranca(cfg):
    (cfg.workspace / "wip.txt").write_text("trabajo del usuario")
    with pytest.raises(GitError):
        run_episode("x", ScriptedLLM([]), cfg, sandbox=ScriptedSandbox())


def test_historial_enviado_al_modelo_no_incluye_think(cfg):
    llm = ScriptedLLM(["<think>secreto</think>\n<<view path=calc.py>>", "<<view path=.>>"])
    cfg.max_steps = 2
    run_episode("x", llm, cfg, sandbox=ScriptedSandbox())
    assert all("secreto" not in m["content"] for m in llm.calls[1])
