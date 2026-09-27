#!/usr/bin/env python3
"""run_local.py — corre un episodio del agente (Ollama local por defecto, o una API cloud).

Ejemplos:
  python run_local.py --repo ../mi_repo --task "Arregla calc.mul" --sandbox local
  python run_local.py --repo ../mi_repo --task "..." --provider anthropic --model claude-opus-5-5
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from swe_agent.config import Config
from swe_agent.events import EventLog
from swe_agent.gitops import GitError
from swe_agent.llm.anthropic import AnthropicClient
from swe_agent.llm.ollama import OllamaClient
from swe_agent.llm.openai_compat import OpenAICompatClient
from swe_agent.loop import run_episode
from swe_agent.sandbox.policy import DefaultPolicy
from swe_agent.telemetry import ConsoleLogger, compute_metrics


def make_llm(a: argparse.Namespace):
    if a.provider == "ollama":
        return OllamaClient(a.model or "qwen3:8b", a.base_url or "http://localhost:11434",
                            num_ctx=a.num_ctx, think=a.think, num_gpu=a.num_gpu)
    if a.provider == "openai":   # OpenAI, OpenRouter, vLLM, Ollama /v1… (clave en OPENAI_API_KEY)
        return OpenAICompatClient(a.model or "gpt-4o-mini",
                                  base_url=a.base_url or "https://api.openai.com/v1")
    return AnthropicClient(a.model or "claude-opus-5-5")   # clave en ANTHROPIC_API_KEY


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True, type=Path, help="repo git (limpio) con tests")
    ap.add_argument("--task", required=True, help="descripción de la tarea")
    ap.add_argument("--provider", choices=["ollama", "openai", "anthropic"], default="ollama")
    ap.add_argument("--model", help="default: qwen3:8b | gpt-4o-mini | claude-opus-5-5")
    ap.add_argument("--base-url")
    ap.add_argument("--sandbox", choices=["docker", "local"], default="docker")
    ap.add_argument("--max-steps", type=int, default=40)
    ap.add_argument("--num-ctx", type=int, default=12_288)
    ap.add_argument("--num-gpu", type=int, help="capas en GPU (0 = solo CPU; default automático)")
    ap.add_argument("--think", action="store_true", help="activa el razonamiento de Qwen3 (lento)")
    ap.add_argument("--test-cmd", help="pista para el agente, p.ej. 'python -m pytest -q'")
    ap.add_argument("--setup-cmd", help="instalación de deps (único momento con red en Docker)")
    ap.add_argument("--log-dir", type=Path, default=Path(__file__).parent / "logs")
    a = ap.parse_args()

    cfg = Config(workspace=a.repo, max_steps=a.max_steps, sandbox=a.sandbox, test_cmd=a.test_cmd,
                 setup_cmd=a.setup_cmd, log_dir=a.log_dir,
                 ctx_budget_tokens=max(2_048, a.num_ctx - 4_096))   # margen para la respuesta
    try:
        res = run_episode(a.task, make_llm(a), cfg, policy=DefaultPolicy(),
                          observers=(ConsoleLogger(),))
    except GitError as e:
        print(f"No se pudo iniciar el episodio: {e}", file=sys.stderr)
        return 2

    print(f"status={res.status} pasos={res.steps} rama={res.branch}")
    if res.error:
        print(f"error: {res.error}")
    try:
        m = compute_metrics(EventLog.replay(res.log_path))
        print(f"duración {m.duration_s:.0f}s ({m.mean_step_s:.1f}s/paso) | tokens prompt "
              f"{m.prompt_tokens} (lectura {m.prompt_eval_s:.0f}s) | errores {m.action_errors}")
    except Exception as e:     # las métricas nunca deben esconder el resultado
        print(f"(no se pudieron calcular métricas: {type(e).__name__}: {e})")
    print(f"log: {res.log_path}")
    return 0 if res.status == "submitted" else 1


if __name__ == "__main__":
    sys.exit(main())
