"""Tests de los contratos compartidos: si alguno se rompe, se rompió la coordinación."""
from __future__ import annotations

from pathlib import Path

import pytest

from swe_agent.config import Config
from swe_agent.events import SCHEMA_VERSION, Event, EventLog
from swe_agent.llm import Completion
from swe_agent.sandbox import CommandResult, Sandbox, make_sandbox
from swe_agent.telemetry import compute_metrics

from .conftest import git


def test_make_repo_crea_repo_limpio_con_lf(make_repo):
    repo = make_repo()
    assert git(repo, "status", "--porcelain") == ""
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert b"\r\n" not in (repo / "calc.py").read_bytes()


def test_config_rechaza_log_dentro_del_workspace(make_repo):
    repo = make_repo()
    with pytest.raises(ValueError, match="log_dir"):
        Config(workspace=repo, log_dir=repo / "logs")


def test_config_rechaza_sandbox_desconocido(make_repo, log_dir):
    with pytest.raises(ValueError, match="sandbox"):
        Config(workspace=make_repo(), log_dir=log_dir, sandbox="vm")


def test_eventlog_roundtrip_y_observers(log_dir: Path):
    seen: list[dict] = []
    log = EventLog(log_dir / "ep.jsonl", observers=(seen.append,))
    log.header(task="t", baseline_sha="abc")
    ev = Event(0, "raw", {"cmd": "bash", "input": "ls"}, "exit=0\nñandú", True,
               llm=Completion("x", prompt_tokens=10).meta(), flags=["multi_action"])
    log.append(ev)
    log.footer(status="submitted", steps=1, patch="")

    records = EventLog.replay(log.path)
    assert [r["type"] for r in records] == ["header", "step", "footer"]
    assert records[0]["schema"] == SCHEMA_VERSION
    assert seen == records                       # observers ven exactamente lo escrito
    assert EventLog.steps(log.path) == [ev]      # replay reconstruye el Event idéntico
    assert b"\r\n" not in log.path.read_bytes()


def test_completion_meta_no_incluye_texto():
    meta = Completion("respuesta larga", model="m", prompt_tokens=5).meta()
    assert "text" not in meta and meta["prompt_tokens"] == 5


def test_command_result_defaults():
    r = CommandResult(0, "ok")
    assert not r.timed_out and r.duration_s == 0.0


def test_make_sandbox_instancia_local(make_repo, log_dir):
    cfg = Config(workspace=make_repo(), log_dir=log_dir, sandbox="local")
    sb = make_sandbox(cfg)
    try:
        assert isinstance(sb, Sandbox)
    finally:
        sb.close()


def test_compute_metrics_contrato():
    m = compute_metrics([])
    assert m.status == "unknown"
    assert m.steps == 0

