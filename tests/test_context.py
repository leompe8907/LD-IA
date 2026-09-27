"""C4 — contexto: invariantes de presupuesto y de prefijo estable (ADR-007)."""
from __future__ import annotations

import pytest

from swe_agent.config import Config
from swe_agent.events import Event
from swe_agent.loop.context import (TokenEstimator, build_messages, condensed_count,
                                    messages_chars, summarize)


@pytest.fixture
def cfg(make_repo, log_dir):
    return Config(workspace=make_repo(), log_dir=log_dir, sandbox="local",
                  condense_block=4, ctx_budget_tokens=100_000)


def ev(i: int, obs_len: int = 50) -> Event:
    return Event(i, f"<think>pienso {i}</think>\n<<bash>>\necho {i}\n<<END>>",
                 {"cmd": "bash", "input": f"echo {i}"},
                 f"exit=0\n{'x' * obs_len}\nlinea final {i}", True)


def prefix_equal(a: list[dict], b: list[dict]) -> bool:
    return a == b[:len(a)]


def test_condensed_count_salta_de_a_bloques():
    assert [condensed_count(n, 4) for n in (0, 7, 8, 11, 12, 15, 16)] == [0, 0, 4, 4, 8, 8, 12]
    for n in range(40):                       # siempre quedan entre K y 2K-1 completos
        full = n - condensed_count(n, 4)
        assert full == n or 4 <= full <= 7


def test_prefijo_estable_dentro_de_un_bloque(cfg):
    hist = [ev(i) for i in range(20)]
    prev = build_messages("tarea", hist[:8], cfg)
    same_block, boundary = 0, 0
    for n in range(9, 21):
        cur = build_messages("tarea", hist[:n], cfg)
        if condensed_count(n, 4) == condensed_count(n - 1, 4):
            assert prefix_equal(prev, cur), f"el prefijo cambió en n={n} sin cambio de bloque"
            same_block += 1
        else:
            boundary += 1
        prev = cur
    assert same_block and boundary


def test_presupuesto_nunca_se_excede(cfg):
    cfg.ctx_budget_tokens = 3_000
    est = TokenEstimator()
    hist = []
    for i in range(60):
        hist.append(ev(i, obs_len=1_500))
        msgs = build_messages("tarea", hist, cfg, est)
        assert est.estimate(messages_chars(msgs)) <= cfg.ctx_budget_tokens
        assert msgs[-1]["content"] == hist[-1].observation     # el último paso, siempre completo


def test_historial_sin_think_y_con_resumen_determinista(cfg):
    hist = [ev(i) for i in range(10)]
    msgs = build_messages("tarea", hist, cfg)
    text = "\n".join(m["content"] for m in msgs)
    assert "<think>" not in text
    assert "RESUMEN DE PASOS ANTERIORES" in msgs[1]["content"]
    assert "paso 0: bash `echo 0` → exit=0 | linea final 0" in msgs[1]["content"]
    assert msgs[2] == {"role": "assistant", "content": "<<bash>>\necho 4\n<<END>>"}


def test_respuesta_invalida_se_muestra_recortada():
    bad = Event(3, "<think>x</think>" + "basura " * 1_000, None, "ERROR: no encontré…", False)
    assert summarize(bad).startswith("paso 3: formato inválido → ERROR")


def test_estimador_se_calibra_con_tokens_reales():
    est = TokenEstimator(3.0)
    est.observe(40_000, 10_000)               # 4 chars/token medido
    assert 3.0 < est.cpt < 4.0
    est.observe(10, 5)                        # muestras chicas se ignoran
    assert 3.0 < est.cpt < 4.0
