"""Stuck detector v2 (ADR-008). Dueño: Claude.

Firma de paso = (acción canónica, observación normalizada, HEAD de git). Con git_head en
la firma, correr el mismo test que falla igual DESPUÉS de editar no cuenta como repetición.
"""
from __future__ import annotations

import hashlib
import re

from ..aci.protocol import canonical

OK, WARN, STUCK = "ok", "warn", "stuck"

_NORMALIZERS = [
    (re.compile(r"\b\d+(?:\.\d+)?\s?(?:s|ms|sec|seconds?)\b"), "<t>"),     # in 0.03s
    (re.compile(r"0x[0-9a-fA-F]+"), "<addr>"),                             # direcciones
    (re.compile(r"(?:/tmp|[A-Za-z]:\\[^\s]*\\Temp)[/\\][^\s'\"]*"), "<tmp>"),  # rutas temporales
    (re.compile(r"\b\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d(?:\.\d+)?\b"), "<ts>"),  # timestamps
]

WARNING = ("[AVISO: estás repitiendo las mismas acciones con el mismo resultado. "
           "Cambia de estrategia: relee el código, revisa la salida o prueba otra hipótesis.]")


def normalize(obs: str) -> str:
    for rx, repl in _NORMALIZERS:
        obs = rx.sub(repl, obs)
    return obs


class StuckDetector:
    def __init__(self, repeats: int = 3, warn_first: bool = True):
        self.repeats = max(2, repeats)
        self.warn_first = warn_first
        self.sigs: list[str] = []
        self.errors_in_row = 0
        self.warned = False

    def update(self, action: dict | None, observation: str, git_head: str | None,
               ok: bool = True) -> str:
        """Devuelve OK, WARN (avisar al modelo) o STUCK (abortar)."""
        act = canonical(action) if action is not None else "INVALID"
        key = f"{act}\x00{normalize(observation)}\x00{git_head}"
        self.sigs.append(hashlib.sha1(key.encode("utf-8", "replace")).hexdigest())
        self.errors_in_row = 0 if ok else self.errors_in_row + 1

        if self._repeating(self.repeats) or self._cycle() or \
                self.errors_in_row >= 2 * self.repeats:
            return STUCK
        # Aviso un paso ANTES del umbral: se aborta igual con <= `repeats` pasos idénticos
        if self.warn_first and not self.warned and self._repeating(self.repeats - 1):
            self.warned = True
            return WARN
        if not self._repeating(2):
            self.warned = False     # si se recuperó, un bucle futuro vuelve a avisar
        return OK

    def _repeating(self, n: int) -> bool:
        return len(self.sigs) >= n and len(set(self.sigs[-n:])) == 1

    def _cycle(self) -> bool:
        """A-B-A-B (orden 2) o A-B-C-A-B-C (orden 3), dos vueltas completas."""
        s = self.sigs
        for p in (2, 3):
            if len(s) >= 2 * p and s[-2 * p:-p] == s[-p:] and len(set(s[-p:])) > 1:
                return True
        return False
