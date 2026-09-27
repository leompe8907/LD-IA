"""C5 — stuck detector v2 (ADR-008)."""
from __future__ import annotations

from swe_agent.loop.stuck import OK, STUCK, WARN, StuckDetector, normalize

A = {"cmd": "bash", "input": "pytest -q"}
B = {"cmd": "view", "path": "calc.py"}


def test_aborta_en_3_identicos_avisando_en_el_2():
    d = StuckDetector(3, warn_first=True)
    assert [d.update(A, "1 failed", "h1") for _ in range(3)] == [OK, WARN, STUCK]


def test_sin_aviso_aborta_igual_en_3():
    d = StuckDetector(3, warn_first=False)
    assert [d.update(A, "1 failed", "h1") for _ in range(3)] == [OK, OK, STUCK]


def test_mismo_test_fallando_tras_editar_no_es_stuck():
    """El falso positivo que señaló Antigravity: git_head distinto = progreso."""
    d = StuckDetector(3)
    assert [d.update(A, "1 failed", f"h{i}") for i in range(5)] == [OK] * 5


def test_tiempos_variables_no_esconden_el_bucle():
    d = StuckDetector(3, warn_first=False)
    res = [d.update(A, f"1 failed in 0.0{i}s", "h") for i in range(3)]
    assert res[-1] == STUCK
    assert normalize("in 1.23s at 0x7ffd") == "in <t> at <addr>"


def test_ciclo_a_b_a_b():
    d = StuckDetector(3)
    res = [d.update(x, o, "h") for x, o in [(A, "f"), (B, "v"), (A, "f"), (B, "v")]]
    assert res == [OK, OK, OK, STUCK]


def test_errores_consecutivos_distintos():
    d = StuckDetector(3)
    res = [d.update(None, f"ERROR distinto {i}", "h", ok=False) for i in range(6)]
    assert res[-1] == STUCK and STUCK not in res[:-1]


def test_recuperarse_rearma_el_aviso():
    d = StuckDetector(3)
    assert d.update(A, "x", "h") == OK
    assert d.update(A, "x", "h") == WARN
    assert d.update(B, "y", "h") == OK
    assert d.update(B, "z", "h2") == OK
    assert d.update(A, "w", "h3") == OK
    assert d.update(A, "w", "h3") == WARN          # vuelve a avisar en un bucle nuevo
