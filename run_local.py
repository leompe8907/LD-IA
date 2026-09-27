#!/usr/bin/env python3
"""run_local.py — episodio del agente con Ollama local (u otro endpoint OpenAI-compatible)."""
import argparse, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from swe_agent_core import OpenAICompatClient, Config, run_episode

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="ruta al repo git con tests")
    ap.add_argument("--task", required=True, help="descripcion de la tarea")
    ap.add_argument("--model", default="swe-qwen")
    ap.add_argument("--base-url", default="http://localhost:11434/v1")
    ap.add_argument("--api-key", default="ollama")
    ap.add_argument("--max-steps", type=int, default=40)
    a = ap.parse_args()

    llm = OpenAICompatClient(model=a.model, api_key=a.api_key, base_url=a.base_url)
    cfg = Config(workspace=Path(a.repo), max_steps=a.max_steps)
    log = Path("logs") / f"{int(time.time())}.jsonl"

    print(f"repo={a.repo} model={a.model} -> log={log}")
    res = run_episode(a.task, llm, cfg, log)
    print("RESULTADO:", res)
    print(f"Revisa el episodio paso a paso en: {log}")

if __name__ == "__main__":
    main()
