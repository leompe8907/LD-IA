#!/usr/bin/env python3
"""test_core.py — valida el núcleo SIN modelo (LLM simulado con guion fijo)."""
import shutil, subprocess, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import swe_agent_core as sc

class MockLLM:
    def __init__(s, scripts): s.scripts = list(scripts)
    def complete(s, messages): return s.scripts.pop(0)

ws = Path(tempfile.mkdtemp())
(ws / "calc.py").write_text("def mul(a, b):\n    return a + b  # BUG\n")
(ws / "test_calc.py").write_text("from calc import mul\ndef test_mul():\n    assert mul(3, 4) == 12\n")
for args in [["init","-q"],["config","user.email","t@t.co"],["config","user.name","t"],
             ["add","-A"],["commit","-qm","init"],["branch","-m","main"]]:
    subprocess.run(["git","-C",str(ws)]+args, capture_output=True)

cfg = sc.Config(workspace=ws, max_steps=15)

# 1. ACI validado
try: sc.ac_str_replace({"path":"calc.py","old":"nope","new":"x"}, cfg); assert False
except sc.ActionError: print("PASS 1a old ausente rechazado")
try: sc.ac_str_replace({"path":"calc.py","old":"a","new":"x"}, cfg); assert False
except sc.ActionError: print("PASS 1b old ambiguo rechazado")
sc.ac_str_replace({"path":"calc.py","old":"return a + b","new":"return a * b"}, cfg)
print("PASS 1c edit + diff")
sc.ac_str_replace({"path":"calc.py","old":"return a * b","new":"return a + b  # BUG"}, cfg)

# 2. Episodio completo
ep = MockLLM([
    '<<ACTION>>{"cmd":"view","path":"calc.py"}<<END>>',
    '<<ACTION>>{"cmd":"bash","input":"pytest -q"}<<END>>',
    '<<ACTION>>{"cmd":"str_replace","path":"calc.py","old":"return a + b  # BUG","new":"return a * b"}<<END>>',
    '<<ACTION>>{"cmd":"bash","input":"pytest -q"}<<END>>',
    '<<ACTION>>{"cmd":"submit"}<<END>>',
])
res = sc.run_episode("Arregla calc.mul", ep, cfg, ws/"run.jsonl")
assert res["status"] == "submitted" and "a * b" in (ws/"calc.py").read_text()
print("PASS 2 episodio completo:", res)

# 3. Stuck
ep2 = MockLLM(['r<<ACTION>>{"cmd":"bash","input":"false"}<<END>>']*10)
res2 = sc.run_episode("x", ep2, cfg, ws/"run2.jsonl")
assert res2["status"] == "stuck"
print("PASS 3 stuck detection:", res2)

# 4. Recuperación de formato inválido
ep3 = MockLLM(['sin accion','<<ACTION>>{"cmd":"nope"}<<END>>',
               '<<ACTION>>{"cmd":"bash","input":"echo ok"}<<END>>','<<ACTION>>{"cmd":"submit"}<<END>>'])
res3 = sc.run_episode("x", ep3, cfg, ws/"run3.jsonl")
assert res3["status"] == "submitted"
print("PASS 4 recuperación de errores")
shutil.rmtree(ws)
print("\n✅ Núcleo OK. Ahora: ollama serve + python run_local.py --repo ... --task ...")
