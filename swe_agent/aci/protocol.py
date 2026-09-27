"""Protocolo de acciones: bloques con contenido crudo (ADR-002). Dueño: Claude.

El código viaja sin escapar; el parser nunca lanza otra cosa que ActionError, que el
loop devuelve al modelo como observación.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass


class ActionError(Exception):
    """El ACI rechaza la acción con feedback en vez de ejecutarla a ciegas."""


# argumentos obligatorios por acción; "offset" es el único opcional
REQUIRED: dict[str, tuple[str, ...]] = {
    "view": ("path",),
    "create": ("path", "content"),
    "str_replace": ("path", "old", "new"),
    "bash": ("input",),
    "submit": (),
}
_SIN_CUERPO = {"view", "submit"}
_MARCADORES = {"END", "OLD", "NEW"}

_TAG_RE = re.compile(r"<<([A-Za-z_]+)([^<>\n]*)>>")
_ATTR_RE = re.compile(r"""\s*(\w+)=(?:"([^"]*)"|'([^']*)'|(\S+))""")
_LEGACY_RE = re.compile(r"<<ACTION>>(.*?)<<END>>", re.DOTALL)

FORMATO = """<<view path=src/app.py offset=0>>
<<create path=src/nuevo.py>>
contenido
<<END>>
<<str_replace path=src/app.py>>
<<OLD>>
texto exacto
<<NEW>>
texto nuevo
<<END>>
<<bash>>
pytest -q
<<END>>
<<submit>>"""


@dataclass
class ParsedAction:
    action: dict          # {"cmd": nombre, **args}
    multi: bool = False   # la respuesta traía más de una acción (solo se usa la primera)


# --------------------------------------------------------------------------- <think>

def strip_thinking(raw: str, strict: bool = True) -> str:
    """Quita el razonamiento de Qwen3 & co. antes de parsear.

    Casos: <think>…</think> completo; solo </think> (la plantilla abrió el bloque en el
    prompt); <think> sin cerrar = respuesta cortada (ActionError si strict).
    """
    text = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1]
    if "<think>" in text:
        if strict:
            raise ActionError("respuesta cortada dentro de <think>: piensa menos y emite la acción")
        text = text.split("<think>", 1)[0]
    return text


# --------------------------------------------------------------------------- parseo

def _strip_nl(s: str) -> str:
    """Descarta exactamente un salto de línea al inicio y otro al final (ADR-002)."""
    if s.startswith("\n"):
        s = s[1:]
    if s.endswith("\n"):
        s = s[:-1]
    return s


def _parse_attrs(name: str, text: str) -> dict:
    attrs, pos = {}, 0
    for m in _ATTR_RE.finditer(text):
        if text[pos:m.start()].strip():
            break
        attrs[m.group(1)] = next(g for g in m.groups()[1:] if g is not None)
        pos = m.end()
    if text[pos:].strip():
        raise ActionError(f"atributos inválidos en <<{name}{text}>>: usa clave=valor "
                          f'(o clave="valor con espacios")')
    return attrs


def _validate(act: dict) -> dict:
    cmd = act.get("cmd")
    if cmd not in REQUIRED:
        raise ActionError(f"acción desconocida: {cmd!r}. Válidas: {', '.join(REQUIRED)}")
    for key in REQUIRED[cmd]:
        if not isinstance(act.get(key), str):
            raise ActionError(f"{cmd}: falta el argumento '{key}'")
    if cmd == "view":
        try:
            act["offset"] = int(act.get("offset", 0))
        except (TypeError, ValueError):
            raise ActionError(f"view: offset debe ser un entero, no {act.get('offset')!r}")
        if act["offset"] < 0:
            raise ActionError("view: offset debe ser >= 0")
    if cmd == "bash" and not act["input"].strip():
        raise ActionError("bash: comando vacío")
    return act


def _parse_legacy(body: str) -> dict:
    try:
        act = json.loads(body)
    except json.JSONDecodeError as e:
        raise ActionError(f"JSON inválido: {e}")
    if not isinstance(act, dict):
        raise ActionError("la acción debe ser un objeto JSON")
    return act


def parse_action(raw: str) -> ParsedAction:
    text = strip_thinking(raw).replace("\r\n", "\n")
    tags = [m for m in _TAG_RE.finditer(text) if m.group(1) not in _MARCADORES]
    if not tags:
        raise ActionError("no encontré ninguna acción. Formato:\n" + FORMATO)
    first = tags[0]
    name = first.group(1)

    if name == "ACTION":  # compatibilidad con el núcleo original (no se enseña)
        m = _LEGACY_RE.search(text, first.start())
        if not m:
            raise ActionError("falta <<END>> tras <<ACTION>>")
        act, end = _parse_legacy(m.group(1)), m.end()
    else:
        if name not in REQUIRED:
            raise ActionError(f"acción desconocida: <<{name}>>. Válidas: {', '.join(REQUIRED)}")
        act = {"cmd": name, **_parse_attrs(name, first.group(2))}
        end = first.end()
        if name in _SIN_CUERPO:
            m = re.match(r"\s*<<END>>", text[end:])   # <<END>> opcional
            if m:
                end += m.end()
        else:
            close = text.find("<<END>>", end)
            if close < 0:
                raise ActionError(f"<<{name}>> necesita cerrar con <<END>> en su propia línea")
            body = _strip_nl(text[end:close])
            end = close + len("<<END>>")
            if name == "bash":
                act["input"] = body.strip()
            elif name == "create":
                act["content"] = body
            else:
                act.update(_split_old_new(body))

    multi = any(m.start() >= end for m in tags[1:])
    return ParsedAction(_validate(act), multi)


def _split_old_new(body: str) -> dict:
    i_old = body.find("<<OLD>>")
    i_new = body.find("<<NEW>>", i_old + 1)
    if i_old < 0 or i_new < 0 or body[:i_old].strip():
        raise ActionError("str_replace necesita <<OLD>> y luego <<NEW>>, cada uno en su línea")
    old = _strip_nl(body[i_old + len("<<OLD>>"):i_new])
    new = body[i_new + len("<<NEW>>"):]
    return {"old": old, "new": new[1:] if new.startswith("\n") else new}


# --------------------------------------------------------------------------- serialización

def canonical(action: dict) -> str:
    """Forma canónica para comparar acciones (stuck detector)."""
    return json.dumps(action, sort_keys=True, ensure_ascii=False)


def render_action(action: dict) -> str:
    """Acción -> bloque canónico. El historial usa esto en vez de la respuesta cruda:
    sin <think> y con prefijo estable para la caché KV (ADR-007)."""
    cmd = action["cmd"]

    def attr(v: str) -> str:
        return f'"{v}"' if (not v or any(c.isspace() for c in v)) else v

    if cmd == "view":
        off = action.get("offset", 0)
        return f"<<view path={attr(action['path'])}" + (f" offset={off}" if off else "") + ">>"
    if cmd == "submit":
        return "<<submit>>"
    if cmd == "bash":
        return f"<<bash>>\n{action['input']}\n<<END>>"
    if cmd == "create":
        return f"<<create path={attr(action['path'])}>>\n{action['content']}\n<<END>>"
    return (f"<<str_replace path={attr(action['path'])}>>\n<<OLD>>\n{action['old']}\n"
            f"<<NEW>>\n{action['new']}\n<<END>>")
