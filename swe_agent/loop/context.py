"""Construcción del prompt con presupuesto de tokens y prefijo estable (ADR-007). Dueño: Claude.

Layout: [system, tarea + resumen de los pasos condensados, pasos completos...].
Los pasos se condensan DE A BLOQUES de K: entre condensaciones el prompt solo crece al
final, así Ollama reutiliza la caché KV en vez de releer todo el prompt en cada paso.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..aci.protocol import render_action, strip_thinking
from .prompts import SYSTEM_PROMPT, task_message

if TYPE_CHECKING:
    from ..config import Config
    from ..events import Event

RAW_INVALID_CHARS = 1_500     # respuesta malformada: se muestra recortada para que vea su error


class TokenEstimator:
    """chars -> tokens. Arranca conservador y se calibra con los tokens reales que
    devuelve el proveedor (Completion.prompt_tokens)."""

    def __init__(self, chars_per_token: float = 2.5):   # código medido: ~2.5 (BENCHMARK.md)
        self.cpt = chars_per_token

    def estimate(self, chars: int) -> int:
        return int(chars / self.cpt) + 1

    def observe(self, chars: int, tokens: int | None) -> None:
        if tokens and tokens > 100:
            measured = chars / tokens
            # EMA hacia lo medido, con piso: sobrestimar es seguro, subestimar no
            self.cpt = max(2.0, 0.7 * self.cpt + 0.3 * measured)


def messages_chars(msgs: list[dict]) -> int:
    return sum(len(m["content"]) + 16 for m in msgs)   # +16 ~ tokens de rol/plantilla


# --------------------------------------------------------------------------- resúmenes

def summarize(ev: Event) -> str:
    """Resumen determinista de una línea de un paso (sin LLM)."""
    obs_lines = [ln for ln in ev.observation.splitlines() if ln.strip()]
    first = obs_lines[0][:120] if obs_lines else ""
    a = ev.action
    if a is None:
        return f"paso {ev.step}: formato inválido → {first}"
    cmd = a["cmd"]
    if not ev.ok:
        target = a.get("path") or a.get("input", "").split("\n")[0][:60]
        return f"paso {ev.step}: {cmd} {target} → {first}"
    if cmd == "bash":
        last = obs_lines[-1][:120] if len(obs_lines) > 1 else ""
        return f"paso {ev.step}: bash `{a['input'].splitlines()[0][:80]}` → {first}" + (
            f" | {last}" if last else "")
    if cmd == "str_replace":
        diff = ev.observation.splitlines()
        plus = sum(1 for ln in diff if ln.startswith("+") and not ln.startswith("+++"))
        minus = sum(1 for ln in diff if ln.startswith("-") and not ln.startswith("---"))
        return f"paso {ev.step}: str_replace {a['path']} → editado (+{plus} -{minus})"
    if cmd == "view":
        return f"paso {ev.step}: view {a['path']} → {first}"
    return f"paso {ev.step}: {cmd} {a.get('path', '')} → {first}"


def _assistant_text(ev: Event) -> str:
    if ev.action is not None:
        return render_action(ev.action)
    return strip_thinking(ev.action_raw, strict=False).strip()[:RAW_INVALID_CHARS] or "(vacío)"


# --------------------------------------------------------------------------- mensajes

def condensed_count(n: int, block: int) -> int:
    """Pasos condensados con n pasos en el historial: salta de a `block`, y siempre
    quedan entre block y 2*block-1 pasos completos."""
    return 0 if n < 2 * block else (n // block - 1) * block


def build_messages(task: str, history: list[Event], cfg: Config,
                   est: TokenEstimator | None = None) -> list[dict]:
    est = est or TokenEstimator()
    k = max(1, cfg.condense_block)
    c = condensed_count(len(history), k)
    while True:
        msgs = _render(task, history, cfg, c)
        if est.estimate(messages_chars(msgs)) <= cfg.ctx_budget_tokens or c >= len(history) - 1:
            break
        c = min(len(history) - 1, c + k)   # más presión: se condensa otro bloque entero
    # Último recurso: los resúmenes más viejos se descartan (el último paso nunca)
    dropped = 0
    while est.estimate(messages_chars(msgs)) > cfg.ctx_budget_tokens and dropped < c:
        dropped = min(c, dropped + k)
        msgs = _render(task, history, cfg, c, dropped)
    return msgs


def _render(task: str, history: list[Event], cfg: Config, c: int, dropped: int = 0) -> list[dict]:
    head = task_message(task, cfg.test_cmd)
    if c:
        lines = [summarize(ev) for ev in history[dropped:c]]
        if dropped:
            lines.insert(0, f"[{dropped} pasos más antiguos omitidos]")
        head += "\n\nRESUMEN DE PASOS ANTERIORES:\n" + "\n".join(lines)
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": head}]
    for ev in history[c:]:
        msgs.append({"role": "assistant", "content": _assistant_text(ev)})
        msgs.append({"role": "user", "content": ev.observation})
    return msgs
