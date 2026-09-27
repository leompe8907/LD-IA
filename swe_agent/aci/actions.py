"""Acciones del ACI: view, create, str_replace, bash, submit. Dueño: Claude.

Archivos y git se manejan en el HOST (ADR-001); solo `bash` pasa por el sandbox.
Cada rechazo es un ActionError con feedback accionable para el modelo.
"""
from __future__ import annotations

import difflib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from .linter import check_syntax
from .protocol import ActionError

if TYPE_CHECKING:
    from ..config import Config
    from ..gitops import EpisodeGit
    from ..sandbox.base import CommandPolicy, Sandbox

DOCKER_ROOT = "/workspace/"      # el modelo copia rutas absolutas que ve en la salida de bash
MAX_LINE_CHARS = 500             # archivos minificados no revientan la observación
MAX_DIR_ENTRIES = 200
_LINE_PREFIX_RE = re.compile(r"^ *\d+ \| ?")


class LintError(ActionError):
    """La edición introduce un error de sintaxis; no se aplicó."""


@dataclass
class ACIContext:
    cfg: Config
    sandbox: Sandbox
    git: EpisodeGit
    policy: CommandPolicy | None = None


@dataclass
class Outcome:
    observation: str
    flags: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- utilidades

def truncate(text: str, limit: int) -> str:
    """Conserva inicio y final: en logs de tests, el resumen suele estar al final."""
    if len(text) <= limit:
        return text
    half = limit // 2
    return f"{text[:half]}\n...[truncado {len(text) - limit} chars]...\n{text[-half:]}"


def resolve_path(rel: str, cfg: Config) -> Path:
    """Ruta del modelo -> ruta del host, dentro del workspace y fuera de .git."""
    rel = rel.strip()
    if not rel or "\x00" in rel:
        raise ActionError("path vacío o inválido")
    if rel == DOCKER_ROOT.rstrip("/") or rel.startswith(DOCKER_ROOT):
        rel = rel[len(DOCKER_ROOT):] or "."
    p = Path(rel)
    if p.is_absolute() or p.drive or PurePosixPath(rel).is_absolute():
        raise ActionError(f"usa rutas relativas a la raíz del repo, no {rel!r}")
    full = (cfg.workspace / p).resolve()          # resuelve symlinks y '..'
    if not full.is_relative_to(cfg.workspace):
        raise ActionError(f"path fuera del workspace: {rel}")
    if ".git" in full.relative_to(cfg.workspace).parts:
        raise ActionError("no se permite tocar .git; usa bash con comandos git de lectura")
    return full


def _rel(full: Path, cfg: Config) -> str:
    return full.relative_to(cfg.workspace).as_posix() or "."


def read_text(path: Path) -> tuple[str, str]:
    """(texto con \\n, salto de línea original). Rechaza binarios y no-UTF-8:
    reescribirlos con errors='replace' los corrompería en silencio."""
    data = path.read_bytes()
    if b"\x00" in data:
        raise ActionError(f"{path.name} parece binario; no se muestra ni se edita")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ActionError(f"{path.name} no es UTF-8; no se edita para no corromperlo")
    newline = "\r\n" if "\r\n" in text else "\n"
    return text.replace("\r\n", "\n"), newline


def write_text(path: Path, text: str, newline: str = "\n") -> None:
    """Escribe bytes exactos: Path.write_text traduciría \\n a \\r\\n en Windows."""
    path.write_bytes(text.replace("\n", newline).encode("utf-8"))


# --------------------------------------------------------------------------- acciones

def ac_view(act: dict, ctx: ACIContext) -> Outcome:
    cfg = ctx.cfg
    full = resolve_path(act["path"], cfg)
    if full.is_dir():
        return Outcome(_list_dir(full, cfg))
    if not full.is_file():
        raise ActionError(f"no existe: {act['path']}." + _missing_hint(act["path"], cfg))
    lines = read_text(full)[0].splitlines()
    off = act.get("offset", 0)
    if lines and off >= len(lines):
        raise ActionError(f"offset {off} fuera de rango: el archivo tiene {len(lines)} líneas")
    page = lines[off:off + cfg.view_page]
    rel = _rel(full, cfg)
    out = [f"{rel} [líneas {off + 1}-{off + len(page)} de {len(lines)}]"]
    for i, line in enumerate(page, start=off + 1):
        if len(line) > MAX_LINE_CHARS:
            line = line[:MAX_LINE_CHARS] + f"…[+{len(line) - MAX_LINE_CHARS} chars]"
        out.append(f"{i:>5} | {line}")
    if off + len(page) < len(lines):
        out.append(f"(sigue: <<view path={rel} offset={off + len(page)}>>)")
    return Outcome(truncate("\n".join(out), cfg.max_obs_chars))


def _list_dir(full: Path, cfg: Config) -> str:
    entries = sorted(p for p in full.iterdir() if p.name != ".git")
    shown = [p.name + ("/" if p.is_dir() else "") for p in entries[:MAX_DIR_ENTRIES]]
    extra = len(entries) - len(shown)
    body = "\n".join(shown) or "(vacío)"
    return f"{_rel(full, cfg)}/ [{len(entries)} entradas]\n{body}" + (
        f"\n...[+{extra} entradas]" if extra > 0 else "")


_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache",
              ".mypy_cache", "dist", "build", "target"}


def repo_files(cfg: Config, limit: int = 2_000) -> list[str]:
    """Archivos del repo (rutas posix relativas), sin carpetas de dependencias ni caches."""
    out: list[str] = []
    for root, dirs, files in os.walk(cfg.workspace):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
        base = Path(root).relative_to(cfg.workspace)
        out.extend((base / f).as_posix() for f in sorted(files))
        if len(out) >= limit:
            break
    return out[:limit]


def repo_overview(cfg: Config, max_entries: int = 60) -> str:
    """Listado de hasta 2 niveles para el mensaje inicial (anticipo mínimo del repo map de F2).
    Evita el fallo observado con Qwen3 8B: adivinar rutas (src/calc.py) en vez de mirar."""
    files = [f for f in repo_files(cfg) if f.count("/") <= 1]
    shown = files[:max_entries]
    more = f"\n...[+{len(files) - len(shown)} archivos; usa <<view path=.>>]" if len(files) > len(shown) else ""
    return "\n".join(shown) + more if shown else "(repo vacío)"


def _missing_hint(rel: str, cfg: Config) -> str:
    files = repo_files(cfg)
    name = PurePosixPath(rel.replace("\\", "/")).name
    same_name = [f for f in files if PurePosixPath(f).name == name]
    close = same_name or difflib.get_close_matches(rel.replace("\\", "/"), files, n=3, cutoff=0.5)
    hint = f" ¿Quisiste decir: {', '.join(close[:3])}?" if close else ""
    return hint + " Usa <<view path=.>> para ver qué archivos existen."


def ac_create(act: dict, ctx: ACIContext) -> Outcome:
    full = resolve_path(act["path"], ctx.cfg)
    if full.exists():
        raise ActionError("ya existe; usa str_replace para modificarlo")
    content = act["content"].replace("\r\n", "\n")
    rel = _rel(full, ctx.cfg)
    if ctx.cfg.lint_on_edit and (err := check_syntax(rel, content)):
        raise LintError(f"el archivo tendría un error de sintaxis, no se creó: {err}")
    twins = [f for f in repo_files(ctx.cfg) if PurePosixPath(f).name == full.name]
    full.parent.mkdir(parents=True, exist_ok=True)
    write_text(full, content)
    note = (f"\n(ojo: ya existe {', '.join(twins[:3])} con el mismo nombre; si querías "
            "modificarlo, edita ese con str_replace)") if twins else ""
    return Outcome(f"creado {rel} ({len(content.splitlines())} líneas){note}")


def ac_str_replace(act: dict, ctx: ACIContext) -> Outcome:
    """Editor validado (SWE-agent): old debe existir, ser ÚNICO, y new != old."""
    cfg = ctx.cfg
    full = resolve_path(act["path"], cfg)
    old, new = act["old"].replace("\r\n", "\n"), act["new"].replace("\r\n", "\n")
    if not old:
        raise ActionError("old está vacío: copia el fragmento exacto que quieres reemplazar")
    if old == new:
        raise ActionError("old == new: nada que cambiar")
    if not full.is_file():
        raise ActionError(f"no existe: {act['path']}." + _missing_hint(act["path"], cfg))
    text, newline = read_text(full)
    flags: list[str] = []

    n = text.count(old)
    if n == 0:
        # Error típico de modelos chicos: copiar los "   12 | " que muestra view
        fixed_old, fixed_new = _strip_line_prefixes(old), _strip_line_prefixes(new)
        if fixed_old != old and text.count(fixed_old) == 1:
            old, new = fixed_old, fixed_new
            flags.append("line_prefix_fixed")
            n = 1
        else:
            raise ActionError("old NO encontrado." + _closest_hint(old, text)
                              + " Relee el archivo con view y copia el texto exacto.")
    if n > 1:
        where = ", ".join(str(ln) for ln in _occurrence_lines(text, old)[:10])
        raise ActionError(f"old aparece {n} veces (líneas {where}); debe ser único. "
                          "Agrega líneas vecinas para desambiguar.")

    updated = text.replace(old, new, 1)
    rel = _rel(full, cfg)
    if cfg.lint_on_edit:
        # Solo se bloquea si la edición INTRODUCE el error (el archivo podía venir roto)
        err = check_syntax(rel, updated)
        if err and not check_syntax(rel, text):
            raise LintError(f"la edición introduce un error de sintaxis, no se aplicó: {err}")
    write_text(full, updated, newline)

    diff = "\n".join(difflib.unified_diff(text.splitlines(), updated.splitlines(),
                                          f"a/{rel}", f"b/{rel}", lineterm="", n=2))
    note = "\n(nota: quité de old/new los números de línea de view)" if flags else ""
    return Outcome(truncate(f"editado {rel}{note}\n{diff}", cfg.max_obs_chars), flags)


def _strip_line_prefixes(s: str) -> str:
    lines = s.split("\n")
    if not any(_LINE_PREFIX_RE.match(ln) for ln in lines):
        return s
    return "\n".join(_LINE_PREFIX_RE.sub("", ln, count=1) for ln in lines)


def _occurrence_lines(text: str, old: str) -> list[int]:
    out, start = [], 0
    while (i := text.find(old, start)) >= 0:
        out.append(text.count("\n", 0, i) + 1)
        start = i + 1
    return out


def _closest_hint(old: str, text: str) -> str:
    probe = next((ln.strip() for ln in old.split("\n") if ln.strip()), "")
    if not probe:
        return ""
    lines = text.split("\n")
    scored = sorted(((difflib.SequenceMatcher(None, probe, ln.strip()).ratio(), i)
                     for i, ln in enumerate(lines) if ln.strip()), reverse=True)[:3]
    hits = [f"{i + 1:>5} | {lines[i]}" for r, i in scored if r >= 0.6]
    return (" Líneas más parecidas:\n" + "\n".join(hits) + "\n") if hits else ""


def ac_bash(act: dict, ctx: ACIContext) -> Outcome:
    cmd = act["input"].strip()
    if ctx.policy and (reason := ctx.policy.check(cmd)):
        raise ActionError(f"comando bloqueado por política: {reason}")
    res = ctx.sandbox.run(cmd, ctx.cfg.bash_timeout)
    head = f"exit={res.exit_code}"
    flags = []
    if res.timed_out:
        head += f" (TIMEOUT: cortado a los {ctx.cfg.bash_timeout:g}s; evita comandos interactivos o largos)"
        flags.append("timeout")
    body = res.output.strip() or "(sin salida)"
    return Outcome(f"{head}\n{truncate(body, ctx.cfg.max_obs_chars)}", flags)


def ac_submit(act: dict, ctx: ACIContext) -> Outcome:
    patch = ctx.git.patch()
    if not patch.strip():
        raise ActionError("no hay cambios respecto al inicio del episodio: nada que enviar")
    return Outcome("SUBMIT\n" + truncate(patch, ctx.cfg.max_obs_chars))


ACTIONS = {
    "view": ac_view,
    "create": ac_create,
    "str_replace": ac_str_replace,
    "bash": ac_bash,
    "submit": ac_submit,
}


def execute(act: dict, ctx: ACIContext) -> Outcome:
    return ACTIONS[act["cmd"]](act, ctx)
