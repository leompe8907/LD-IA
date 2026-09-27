"""El loop del agente: historial -> mensajes -> acción -> observación. Dueño: Claude.

Ninguna acción del modelo corta el episodio: todo error vuelve como observación. Solo
terminan el episodio: submit aceptado, stuck, max_steps, o fallas de infraestructura
(LLMError, SandboxError, GitError) que quedan registradas como status.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from pathlib import Path

from ..aci.actions import ACIContext, LintError, execute
from ..aci.protocol import ActionError, parse_action
from ..config import Config
from ..events import Event, EventLog, Observer
from ..gitops import EpisodeGit, GitError
from ..llm.base import LLMClient, LLMError
from ..sandbox.base import CommandPolicy, Sandbox, SandboxError
from .context import TokenEstimator, build_messages, messages_chars
from .stuck import STUCK, WARN, WARNING, StuckDetector

MULTI_ACTION_NOTE = "\n[aviso: emitiste varias acciones; solo se ejecutó la primera]"


@dataclass
class EpisodeResult:
    status: str          # submitted | stuck | max_steps | llm_error | sandbox_error | git_error
    steps: int
    patch: str
    baseline_sha: str
    branch: str
    log_path: Path
    error: str | None = None


def run_episode(task: str, llm: LLMClient, cfg: Config, *, sandbox: Sandbox | None = None,
                policy: CommandPolicy | None = None, observers: tuple[Observer, ...] = (),
                log_path: Path | None = None) -> EpisodeResult:
    log_path = Path(log_path or cfg.log_dir / f"{time.strftime('%Y%m%d-%H%M%S')}.jsonl")
    git = EpisodeGit(cfg.workspace)
    branch, baseline = git.start(cfg.agent_branch_prefix)   # GitError aquí: no hay episodio
    log = EventLog(log_path, observers)
    log.header(task=task, model=getattr(llm, "model", type(llm).__name__),
               baseline_sha=baseline, branch=branch, original_branch=git.original_branch,
               config={k: str(v) for k, v in asdict(cfg).items()})

    status, error, steps, crash = "max_steps", None, 0, None
    own_sandbox = sandbox is None
    try:
        if sandbox is None:
            from ..sandbox.base import make_sandbox
            sandbox = make_sandbox(cfg)
        ctx = ACIContext(cfg, sandbox, git, policy)
        history: list[Event] = []
        stuck = StuckDetector(cfg.stuck_repeats, cfg.stuck_warn_first)
        est = TokenEstimator()

        for step in range(cfg.max_steps):
            steps = step + 1
            t0 = time.time()
            msgs = build_messages(task, history, cfg, est)
            comp = llm.complete(msgs)
            est.observe(messages_chars(msgs), comp.prompt_tokens)

            act, ok, flags = None, False, []
            try:
                parsed = parse_action(comp.text)
                act = parsed.action
                out = execute(act, ctx)
                obs, ok, flags = out.observation, True, list(out.flags)
                if parsed.multi:
                    obs += MULTI_ACTION_NOTE
                    flags.append("multi_action")
            except LintError as e:
                obs = f"ERROR: {e}"
                flags.append("lint_rejected")
            except ActionError as e:
                obs = f"ERROR: {e}"
            except (SandboxError, GitError, LLMError):
                raise
            except Exception as e:   # red de seguridad: un bug del ACI no mata el episodio
                obs = f"ERROR interno del ACI: {type(e).__name__}: {e}"
                flags.append("internal_error")

            head = git.commit_if_dirty(_commit_msg(step, act)) or git.head()
            verdict = stuck.update(act, obs, head, ok)
            if verdict == WARN:
                obs += "\n" + WARNING
                flags.append("stuck_warning")

            ev = Event(step, comp.text, act, obs, ok, duration_s=time.time() - t0,
                       llm=comp.meta(), git_head=head, flags=flags)
            log.append(ev)
            history.append(ev)

            if ok and act["cmd"] == "submit":
                status = "submitted"
                break
            if verdict == STUCK:
                status = "stuck"
                break
    except LLMError as e:
        status, error = "llm_error", str(e)
    except SandboxError as e:
        status, error = "sandbox_error", str(e)
    except GitError as e:
        status, error = "git_error", str(e)
    except Exception as e:           # bug nuestro: se cierra el log y se re-lanza
        status, error, crash = "internal_error", f"{type(e).__name__}: {e}", e
    finally:
        if own_sandbox and sandbox is not None:
            sandbox.close()

    try:
        patch = git.patch()
    except GitError as e:
        patch, error = "", error or str(e)
    log.footer(status=status, steps=steps, baseline_sha=baseline, branch=branch,
               patch=patch, error=error)
    if crash is not None:
        raise crash
    return EpisodeResult(status, steps, patch, baseline, branch, log_path, error)


def _commit_msg(step: int, act: dict | None) -> str:
    if act is None:
        return f"paso {step}"
    target = act.get("path") or act.get("input", "").split("\n")[0][:60]
    return f"paso {step}: {act['cmd']} {target}".rstrip()
