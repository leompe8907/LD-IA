"""Stuck detector v2 (ADR-008). Dueño: Claude.

Firma de paso = (acción canónica, observación normalizada, árbol de git). Con el estado
en la firma, correr el mismo test que falla igual DESPUÉS de editar no cuenta como
repetición; volver al MISMO contenido y correrlo otra vez, sí (revisita).
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
        self.revisits = 0

    def update(self, action: dict | None, observation: str, state: str | None,
               ok: bool = True) -> str:
        """Devuelve OK, WARN (avisar al modelo) o STUCK (abortar).

        `state` = hash del ÁRBOL de git (contenido), no del commit: así volver a un
        contenido ya visto (a*b -> a*b*2 -> a*b) cuenta como el mismo estado.
        """
        act = canonical(action) if action is not None else "INVALID"
        key = f"{act}\x00{normalize(observation)}\x00{state}"
        sig = hashlib.sha1(key.encode("utf-8", "replace")).hexdigest()
        # Revisita: mismo comando, mismo código, mismo resultado que un paso NO inmediato.
        # Es la oscilación que el ciclo de orden 2/3 no ve (observada con Qwen3 8B).
        if action is not None and action["cmd"] == "bash" and sig in self.sigs[:-1]:
            self.revisits += 1
        self.sigs.append(sig)
        self.errors_in_row = 0 if ok else self.errors_in_row + 1

        if self._repeating(self.repeats) or self._cycle() or \
                self.errors_in_row >= 2 * self.repeats or self.revisits >= self.repeats:
            return STUCK
        if self.warn_first and self.revisits == 1 and not self.warned:
            self.warned = True
            return WARN
        # Aviso un paso ANTES del umbral: se aborta igual con <= `repeats` pasos idénticos
        if self.warn_first and not self.warned and self._repeating(self.repeats - 1):
            self.warned = True
            return WARN
        if not self._repeating(2) and self.revisits == 0:
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
