
"""
swe_core.py — Núcleo mínimo de agente SWE (estilo Mini-SWE-Agent + ACI de SWE-agent)

Decisiones de diseño extraídas de la comunidad:
  * SWE-agent    -> interfaz agente-computadora RESTRINGIDA: pocas acciones, con validación
                    (editor que rechaza old_str ambiguo, linter/indentación implícito,
                    view paginado, submit explícito).
  * Mini-SWE-Agent -> loop minúsculo LLM<->bash; el estado real vive en el workspace + git,
                    no en memoria del agente; feedback vía diff/git, no parseo frágil.
  * OpenHands V1  -> EventLog append-only como única fuente de verdad (replay/auditoría)
                    y agent como función pura: historial -> próxima acción.
  * Anti-loop     -> stuck detector (observaciones repetidas / acciones idénticas).

Sin dependencias externas (solo stdlib). El cliente LLM es un Protocol: enchufa
OpenAI-compatible API, Anthropic, o un mock para tests.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import subprocess
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

# --------------------------------------------------------------------------- config

@dataclass
class Config:
    workspace: Path
    max_steps: int = 60
    max_obs_chars: int = 10_000      # truncar salidas largas (lectura acotada)
    view_page: int = 100             # SWE-agent: paginar lectura simplifica atención
    bash_timeout: int = 120
    stuck_window: int = 3            # N observaciones iguales seguidas => stuck
    identical_actions: int = 3       # N acciones crudas iguales seguidas => stuck
    commit_each_edit: bool = True    # Aider: git como memoria, reversible por paso
    condense_after: int = 8          # pasos recientes completos; los anteriores compactados

# --------------------------------------------------------------------------- event log

@dataclass
class Event:
    step: int
    action_raw: str          # respuesta cruda del modelo (auditable)
    action: dict | None      # acción parseada (None si el parseo falló)
    observation: str         # feedback del entorno (truncado)
    ok: bool
    ts: float = field(default_factory=time.time)

class EventLog:
    """Append-only JSONL: replayable, auditable, única fuente de verdad (OpenHands V1)."""
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, ev: Event) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ev.__dict__, ensure_ascii=False) + "\n")

    @staticmethod
    def replay(path: Path):
        return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]

# --------------------------------------------------------------------------- LLM interface

class LLMClient(Protocol):
    def complete(self, messages: list[dict]) -> str: ...

class OpenAICompatClient:
    """Cliente mínimo para API tipo OpenAI (usa solo stdlib)."""
    def __init__(self, model: str, api_key: str | None = None,
                 base_url: str = "https://api.openai.com/v1"):
        import urllib.request
        self._req = urllib.request
        self.model, self.key = model, api_key or os.environ.get("OPENAI_API_KEY", "")
        self.url = base_url.rstrip("/") + "/chat/completions"

    def complete(self, messages: list[dict]) -> str:
        body = json.dumps({"model": self.model, "messages": messages,
                           "temperature": 0}).encode()
        r = self._req.Request(self.url, data=body, headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.key}"})
        with self._req.urlopen(r, timeout=180) as resp:
            data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"]

# --------------------------------------------------------------------------- ACI (herramientas restringidas)

class ActionError(Exception):
    """El ACI rechaza acciones inválidas con feedback en vez de ejecutarlas a ciegas."""

def _trunc(text: str, cfg: Config) -> str:
    if len(text) <= cfg.max_obs_chars:
        return text
    half = cfg.max_obs_chars // 2
    return (text[:half] + f"\n...[truncado {len(text)-cfg.max_obs_chars} chars]...\n"
            + text[-half:])

def _git(ws: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(ws), *args],
                          capture_output=True, text=True, timeout=30).stdout

def ac_view(args: dict, cfg: Config) -> str:
    path = (cfg.workspace / args["path"]).resolve()
    if not str(path).startswith(str(cfg.workspace.resolve())):
        raise ActionError("path fuera del workspace")
    if not path.is_file():
        raise ActionError(f"no existe: {args['path']}")
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    off = int(args.get("offset", 0))
    page = lines[off:off + cfg.view_page]
    return f"{args['path']} [{off}-{off+len(page)}/{len(lines)} líneas]\n" + "\n".join(
        f"{off+i+1:>5} | {l}" for i, l in enumerate(page))

def ac_create(args: dict, cfg: Config) -> str:
    path = (cfg.workspace / args["path"]).resolve()
    if not str(path).startswith(str(cfg.workspace.resolve())):
        raise ActionError("path fuera del workspace")
    if path.exists():
        raise ActionError("ya existe; usa str_replace")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(args["content"], encoding="utf-8")
    return f"creado {args['path']} ({len(args['content'])} chars)"

def ac_str_replace(args: dict, cfg: Config) -> str:
    """Editor validado (SWE-agent): old debe existir, ser ÚNICO, y new != old."""
    path = (cfg.workspace / args["path"]).resolve()
    if not str(path).startswith(str(cfg.workspace.resolve())):
        raise ActionError("path fuera del workspace")
    old, new = args["old"], args["new"]
    if old == new:
        raise ActionError("old == new: nada que cambiar")
    if not path.is_file():
        raise ActionError(f"no existe: {args['path']}")
    text = path.read_text(encoding="utf-8", errors="replace")
    n = text.count(old)
    if n == 0:
        raise ActionError("old_str NO encontrado. Relee el archivo con view.")
    if n > 1:
        raise ActionError(f"old_str aparece {n} veces; debe ser único. Amplía el contexto.")
    updated = text.replace(old, new, 1)
    path.write_text(updated, encoding="utf-8")
    diff = "\n".join(difflib.unified_diff(
        text.splitlines(), updated.splitlines(), lineterm="", n=2))
    return f"editado {args['path']}\n{diff}"

def ac_bash(args: dict, cfg: Config) -> str:
    cmd = args["input"].strip()
    banned = ["rm -rf /", "git push", "curl", "wget", "sudo"]
    if any(b in cmd for b in banned):
        raise ActionError(f"comando no permitido por política: {cmd}")
    p = subprocess.run(cmd, shell=True, cwd=cfg.workspace,
                       capture_output=True, text=True, timeout=cfg.bash_timeout)
    out = (p.stdout + p.stderr).strip() or "(sin salida)"
    return f"exit={p.returncode}\n{_trunc(out, cfg)}"

def ac_submit(args: dict, cfg: Config) -> str:
    return "SUBMIT\n" + _trunc(_git(cfg.workspace, "diff", "HEAD~1", "HEAD"), cfg)

ACTIONS = {
    "view": ac_view,
    "create": ac_create,
    "str_replace": ac_str_replace,
    "bash": ac_bash,
    "submit": ac_submit,
}

# --------------------------------------------------------------------------- parsing del protocolo

ACTION_RE = re.compile(r"<<ACTION>>(.*?)<<END>>", re.DOTALL)

def parse_action(raw: str) -> dict:
    m = ACTION_RE.search(raw)
    if not m:
        raise ActionError("formato inválido: emite <<ACTION>>{json}<<END>>")
    try:
        act = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        raise ActionError(f"JSON inválido: {e}")
    if act.get("cmd") not in ACTIONS:
        raise ActionError(f"cmd desconocido: {act.get('cmd')}")
    return act

SYSTEM_PROMPT = """Eres un agente de ingeniería de software en este repositorio. Resuelve la tarea
usando SOLO estas acciones, en bloques <<ACTION>>{json}<<END>>:

{"cmd":"view","path":"src/app.py","offset":0}                       # leer archivo (100 líneas/página)
{"cmd":"create","path":"...","content":"..."}                      # crear archivo nuevo
{"cmd":"str_replace","path":"...","old":"...","new":"..."}         # reemplazo EXACTO y ÚNICO
{"cmd":"bash","input":"pytest -q"}                                 # ejecutar comando (120s max)
{"cmd":"submit"}                                                   # terminar cuando tests pasen

Reglas:
- Lee antes de editar; "old" debe aparecer EXACTAMENTE UNA VEZ (amplía contexto si no).
- Tras cada edición el sistema commitea automáticamente en git.
- Observa resultados, corrige, itera. Sé preciso con los comandos (grep -n, git diff).
"""

# --------------------------------------------------------------------------- stuck detection

class StuckDetector:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._obs: deque[str] = deque(maxlen=cfg.stuck_window)
        self._last_raw: str | None = None
        self._same_raw = 0

    def update(self, raw: str, observation: str) -> bool:
        self._obs.append(hashlib.sha1(observation.encode()).hexdigest())
        if raw.strip() == (self._last_raw or "").strip():
            self._same_raw += 1
        else:
            self._same_raw = 1
        self._last_raw = raw
        obs_stuck = len(self._obs) == self.cfg.stuck_window and len(set(self._obs)) == 1
        return obs_stuck or self._same_raw >= self.cfg.identical_actions

# --------------------------------------------------------------------------- condensación de contexto

def build_messages(task: str, history: list[Event], cfg: Config) -> list[dict]:
    msgs: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": f"TAREA:\n{task}"}]
    recent = history[-cfg.condense_after:]
    old = history[:-cfg.condense_after]
    for ev in old:  # eventos antiguos compactados a una línea (condenser mínimo)
        a = ev.action["cmd"] if ev.action else "?"
        msgs.append({"role": "assistant", "content": ev.action_raw[:200]})
        msgs.append({"role": "user",
                     "content": f"[paso {ev.step} {a}: observación omitida por brevedad]"})
    for ev in recent:
        msgs.append({"role": "assistant", "content": ev.action_raw})
        msgs.append({"role": "user", "content": ev.observation})
    return msgs

# --------------------------------------------------------------------------- el loop (agente como función pura)

def run_episode(task: str, llm: LLMClient, cfg: Config, log_path: Path) -> dict:
    log = EventLog(log_path)
    history: list[Event] = []
    stuck = StuckDetector(cfg)
    _git(cfg.workspace, "add", "-A"); _git(cfg.workspace, "commit", "-qm", "baseline")

    for step in range(cfg.max_steps):
        raw = llm.complete(build_messages(task, history, cfg))
        try:
            act = parse_action(raw)
            if act["cmd"] == "bash" and cfg.commit_each_edit and _dirty(cfg):
                _git(cfg.workspace, "add", "-A")
                _git(cfg.workspace, "commit", "-qm", f"auto step {step}")
            obs = ACTIONS[act["cmd"]](act, cfg)
            ok = True
        except ActionError as e:
            act, obs, ok = None, f"ERROR: {e}", False
        except subprocess.TimeoutExpired:
            act, obs, ok = act, "ERROR: comando excedió timeout", False

        ev = Event(step, raw, act, obs, ok)
        log.append(ev); history.append(ev)

        if act and act["cmd"] == "submit" and ok:
            return {"status": "submitted", "steps": step + 1,
                    "patch": _git(cfg.workspace, "diff", "--stat", "main..HEAD") if _branch(cfg) else ""}
        if stuck.update(raw, obs):
            return {"status": "stuck", "steps": step + 1}
    return {"status": "max_steps", "steps": cfg.max_steps}

def _dirty(cfg: Config) -> bool:
    return bool(_git(cfg.workspace, "status", "--porcelain").strip())

def _branch(cfg: Config) -> bool:
    return bool(_git(cfg.workspace, "rev-parse", "--abbrev-ref", "HEAD").strip())
