"""Tests de telemetría: ConsoleLogger y compute_metrics (ADR-006, A6)."""
from __future__ import annotations

import io

import pytest

from swe_agent.events import Event, EventLog
from swe_agent.telemetry.logger import ConsoleLogger
from swe_agent.telemetry.metrics import EpisodeMetrics, compute_metrics


def test_console_logger_imprime_header_step_footer():
    stream = io.StringIO()
    logger = ConsoleLogger(stream=stream)

    logger({"type": "header", "task": "Arreglar calc.py", "model": "qwen3:8b", "baseline_sha": "abc1234"})
    logger({
        "type": "step",
        "step": 0,
        "duration_s": 1.25,
        "action": {"cmd": "bash", "input": "pytest -q"},
        "ok": True,
        "observation": "exit=0\n1 passed in 0.05s",
        "git_head": "abc1234",
    })
    logger({
        "type": "step",
        "step": 1,
        "duration_s": 0.8,
        "action": {"cmd": "submit"},
        "ok": False,
        "observation": "ERROR: submit falló",
        "flags": ["stuck_warning"],
    })
    logger({"type": "footer", "status": "submitted", "steps": 2})

    out = stream.getvalue()
    assert "Arreglar calc.py" in out
    assert "bash pytest -q" in out
    assert "submit" in out
    assert "stuck_warning" in out
    assert "Status: submitted" in out


def test_compute_metrics_con_tokens_reales():
    records = [
        {"type": "header", "task": "tarea", "baseline_sha": "base000", "ts": 100.0},
        {
            "type": "step",
            "step": 0,
            "duration_s": 2.0,
            "action_raw": "raw1",
            "action": {"cmd": "view", "path": "calc.py"},
            "observation": "obs1",
            "ok": True,
            "git_head": "base000",
            "llm": {"prompt_tokens": 100, "completion_tokens": 20, "prompt_eval_s": 0.5},
        },
        {
            "type": "step",
            "step": 1,
            "duration_s": 3.0,
            "action_raw": "raw2",
            "action": {"cmd": "str_replace", "path": "calc.py"},
            "observation": "editado",
            "ok": True,
            "git_head": "commit1",
            "flags": ["stuck_warning"],
            "llm": {"prompt_tokens": 150, "completion_tokens": 30, "prompt_eval_s": 0.7},
        },
        {
            "type": "footer",
            "status": "submitted",
            "steps": 2,
            "ts": 106.0,
            "patch": "diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@ -1 +1 @@\n-def add\n+def mul\n",
        },
    ]

    m = compute_metrics(records)
    assert isinstance(m, EpisodeMetrics)
    assert m.status == "submitted"
    assert m.steps == 2
    assert m.duration_s == 6.0
    assert m.mean_step_s == 2.5
    assert m.prompt_tokens == 250
    assert m.completion_tokens == 50
    assert m.tokens_estimated is False
    assert m.prompt_eval_s == 1.2
    assert m.commits == 1
    assert m.action_errors == 0
    assert m.stuck_warnings == 1
    assert m.diff_files == 1
    assert m.diff_added == 1
    assert m.diff_removed == 1


def test_compute_metrics_fallback_estimacion_tokens():
    records = [
        {"type": "header", "task": "t", "ts": 10.0},
        {
            "type": "step",
            "step": 0,
            "duration_s": 1.0,
            "action_raw": "A" * 40,
            "observation": "B" * 80,
            "ok": False,
            "llm": None,  # sin llm meta
        },
        {"type": "footer", "status": "stuck", "steps": 1, "ts": 12.0},
    ]

    m = compute_metrics(records)
    assert m.status == "stuck"
    assert m.steps == 1
    assert m.tokens_estimated is True
    assert m.prompt_tokens == 20  # 80 // 4
    assert m.completion_tokens == 10  # 40 // 4
    assert m.action_errors == 1
