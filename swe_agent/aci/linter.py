"""Chequeo de sintaxis tras editar (ADR-004). Dueño: Claude.

Fase 1: Python con compile(). Fase 2: nodos ERROR de tree-sitter para el resto.
Sin linter para la extensión -> no se chequea (nunca bloquea).
"""
from __future__ import annotations

import warnings


def check_syntax(rel_path: str, text: str) -> str | None:
    """None si la sintaxis es válida (o no hay linter); si no, el error legible."""
    if rel_path.endswith((".py", ".pyi")):
        return _check_python(rel_path, text)
    return None


def _check_python(rel_path: str, text: str) -> str | None:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")        # SyntaxWarning no es error
            compile(text, rel_path, "exec", dont_inherit=True)
    except SyntaxError as e:
        where = f"línea {e.lineno}" if e.lineno else "posición desconocida"
        line = (e.text or "").rstrip("\n")
        return f"{e.msg} ({where})" + (f":\n    {line}" if line.strip() else "")
    except ValueError as e:                        # p.ej. bytes nulos en el código
        return str(e)
    return None
