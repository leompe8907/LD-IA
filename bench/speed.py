#!/usr/bin/env python3
"""Benchmark de velocidad de Ollama en este hardware (C8, ADR-010). Dueño: Claude.

Mide, para cada modo de GPU (automático vs solo CPU) y cada tamaño de prompt:
  * lectura del prompt (prefill) en tok/s  -> el cuello de botella del agente
  * generación en tok/s
  * reutilización de la caché KV: mismo prefijo + sufijo corto (ADR-007)

Uso:
  python bench/speed.py --model qwen3:8b
  python bench/speed.py --model qwen3:8b --sizes 2000,6000,12000 --out docs/BENCHMARK.md
"""
from __future__ import annotations

import argparse
import json
import platform
import random
import sys
import time
import urllib.request
from pathlib import Path

NS = 1e9


def post(base: str, path: str, body: dict | None = None, timeout: float = 1800) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method="POST" if data else "GET",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def filler(tokens: int, seed: int) -> str:
    """Código Python sintético (~3 chars/token). El nonce inicial evita reusar caché
    de corridas anteriores."""
    rnd = random.Random(seed)
    out = [f"# nonce {seed}-{time.time_ns()}"]
    while sum(len(s) for s in out) < tokens * 3:
        n = rnd.randint(0, 10**6)
        out.append(f"def funcion_{n}(x, y):\n    total = x * {n % 97} + y\n"
                   f"    if total > {n % 1000}:\n        return total - {n % 13}\n    return total\n")
    return "\n".join(out)


def chat(base: str, model: str, content: str, num_ctx: int, num_gpu: int | None) -> dict:
    opts = {"num_ctx": num_ctx, "num_predict": 64, "temperature": 0}
    if num_gpu is not None:
        opts["num_gpu"] = num_gpu
    return post(base, "/api/chat", {"model": model, "stream": False, "think": False,
                                    "keep_alive": "10m", "options": opts,
                                    "messages": [{"role": "user", "content": content}]})


def rate(count: int | None, dur_ns: int | None) -> float:
    return (count or 0) / (dur_ns / NS) if dur_ns else 0.0


def gpu_share(base: str, model: str) -> str:
    for m in post(base, "/api/ps").get("models", []):
        if m.get("name", "").startswith(model) or m.get("model", "").startswith(model):
            size, vram = m.get("size", 0), m.get("size_vram", 0)
            return f"{100 * vram / size:.0f}% GPU ({vram / 2**30:.1f}/{size / 2**30:.1f} GiB)" if size else "?"
    return "?"


def run(args) -> list[dict]:
    rows = []
    for gpu in args.gpu.split(","):
        num_gpu = None if gpu == "auto" else int(gpu)
        label = "auto" if num_gpu is None else f"num_gpu={num_gpu}"
        t0 = time.time()
        chat(args.base, args.model, "Di OK.", args.num_ctx, num_gpu)        # carga el modelo
        load_s = time.time() - t0
        share = gpu_share(args.base, args.model)
        print(f"\n== {label}: carga {load_s:.1f}s, {share}", flush=True)
        for size in (int(s) for s in args.sizes.split(",")):
            prompt = "Resume en una línea qué hace este código:\n" + filler(size, size)
            r = chat(args.base, args.model, prompt, args.num_ctx, num_gpu)
            # caché KV: mismo prompt + sufijo; solo el sufijo debería leerse
            r2 = chat(args.base, args.model, prompt + "\nY ahora en dos líneas.", args.num_ctx, num_gpu)
            row = {"modo": label, "gpu": share, "prompt_tok": r.get("prompt_eval_count"),
                   "prefill_s": (r.get("prompt_eval_duration") or 0) / NS,
                   "prefill_tps": rate(r.get("prompt_eval_count"), r.get("prompt_eval_duration")),
                   "gen_tps": rate(r.get("eval_count"), r.get("eval_duration")),
                   "cache_prompt_tok": r2.get("prompt_eval_count"),
                   "cache_prefill_s": (r2.get("prompt_eval_duration") or 0) / NS}
            rows.append(row)
            print(f"  {row['prompt_tok']:>6} tok | prefill {row['prefill_s']:7.1f}s "
                  f"({row['prefill_tps']:6.1f} tok/s) | gen {row['gen_tps']:5.1f} tok/s | "
                  f"con caché: {row['cache_prefill_s']:.1f}s",
                  flush=True)
    return rows


def to_markdown(rows: list[dict], args) -> str:
    head = (f"# Benchmark de Ollama\n\n- Modelo: `{args.model}` · num_ctx {args.num_ctx}\n"
            f"- Fecha: {time.strftime('%Y-%m-%d %H:%M')} · {platform.platform()}\n\n"
            "| Modo | Reparto | Prompt (tok) | Prefill (s) | Prefill (tok/s) | Generación (tok/s) "
            "| Con caché: prefill (s) |\n|---|---|---|---|---|---|---|\n")
    body = "".join(f"| {r['modo']} | {r['gpu']} | {r['prompt_tok']} | {r['prefill_s']:.1f} | "
                   f"{r['prefill_tps']:.1f} | {r['gen_tps']:.1f} | "
                   f"{r['cache_prefill_s']:.1f} |\n" for r in rows)
    return head + body


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="qwen3:8b")
    ap.add_argument("--base", default="http://localhost:11434")
    ap.add_argument("--num-ctx", type=int, default=16_384)
    ap.add_argument("--sizes", default="2000,6000,12000", help="tokens aproximados de prompt")
    ap.add_argument("--gpu", default="auto,0", help="modos: auto y/o num_gpu (0 = solo CPU)")
    ap.add_argument("--out", type=Path, help="escribe la tabla en Markdown (p.ej. docs/BENCHMARK.md)")
    args = ap.parse_args()
    try:
        post(args.base, "/api/version", timeout=5)
    except OSError as e:
        print(f"Ollama no responde en {args.base} ({e}). ¿Está instalado y corriendo?", file=sys.stderr)
        return 2
    names = {m.get("name", "") for m in post(args.base, "/api/tags").get("models", [])}
    if not any(n == args.model or n.split(":")[0] == args.model for n in names):
        print(f"El modelo {args.model} no está descargado. Ejecuta: ollama pull {args.model}",
              file=sys.stderr)
        return 2
    rows = run(args)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(to_markdown(rows, args), encoding="utf-8", newline="\n")
        print(f"\nTabla escrita en {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
