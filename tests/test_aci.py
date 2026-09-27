"""C2 — ACI: editor validado, seguridad de rutas, linter, bash y submit."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from swe_agent.aci.actions import ACIContext, LintError, execute, resolve_path, truncate
from swe_agent.aci.protocol import ActionError
from swe_agent.config import Config
from swe_agent.gitops import EpisodeGit
from swe_agent.sandbox.base import CommandResult

from .fakes import ScriptedSandbox


@pytest.fixture
def ctx(make_repo, log_dir):
    repo = make_repo()
    cfg = Config(workspace=repo, log_dir=log_dir, sandbox="local")
    git = EpisodeGit(repo)
    git.start()
    return ACIContext(cfg, ScriptedSandbox(), git)


def run(ctx, **act):
    return execute(act, ctx).observation


# --------------------------------------------------------------------------- str_replace

def test_old_ausente_rechazado_con_sugerencia(ctx):
    with pytest.raises(ActionError, match="NO encontrado") as e:
        run(ctx, cmd="str_replace", path="calc.py", old="return a - b", new="x")
    assert "return a + b" in str(e.value)          # sugiere la línea más parecida


def test_old_ambiguo_rechazado_con_lineas(ctx):
    with pytest.raises(ActionError, match=r"aparece 2 veces \(líneas 1, 2\)"):
        run(ctx, cmd="str_replace", path="calc.py", old="a", new="x")


def test_edita_y_muestra_diff(ctx):
    obs = run(ctx, cmd="str_replace", path="calc.py", old="return a + b  # BUG", new="return a * b")
    assert "-    return a + b  # BUG" in obs and "+    return a * b" in obs
    assert (ctx.cfg.workspace / "calc.py").read_text() == "def mul(a, b):\n    return a * b\n"


@pytest.mark.parametrize("old, new", [("x", "x"), ("", "y")])
def test_old_igual_a_new_o_vacio(ctx, old, new):
    with pytest.raises(ActionError):
        run(ctx, cmd="str_replace", path="calc.py", old=old, new=new)


def test_conserva_crlf_del_archivo(ctx):
    f = ctx.cfg.workspace / "win.py"
    f.write_bytes(b"a = 1\r\nb = 2\r\n")
    run(ctx, cmd="str_replace", path="win.py", old="b = 2", new="b = 3")
    assert f.read_bytes() == b"a = 1\r\nb = 3\r\n"


def test_conserva_lf_en_windows(ctx):
    run(ctx, cmd="str_replace", path="calc.py", old="# BUG", new="# ok")
    assert b"\r\n" not in (ctx.cfg.workspace / "calc.py").read_bytes()


def test_rechaza_no_utf8_y_binarios(ctx):
    (ctx.cfg.workspace / "latin.txt").write_bytes("canción".encode("latin-1"))
    (ctx.cfg.workspace / "bin.dat").write_bytes(b"\x00\x01")
    with pytest.raises(ActionError, match="UTF-8"):
        run(ctx, cmd="str_replace", path="latin.txt", old="canci", new="x")
    with pytest.raises(ActionError, match="binario"):
        run(ctx, cmd="view", path="bin.dat")


def test_quita_numeros_de_linea_copiados_de_view(ctx):
    execute({"cmd": "view", "path": "calc.py"}, ctx)
    out = execute({"cmd": "str_replace", "path": "calc.py",
                   "old": "    2 |     return a + b  # BUG", "new": "    2 |     return a * b"}, ctx)
    assert "line_prefix_fixed" in out.flags
    assert "return a * b\n" in (ctx.cfg.workspace / "calc.py").read_text()


# --------------------------------------------------------------------------- linter

def test_linter_rechaza_edicion_que_rompe_sintaxis(ctx):
    with pytest.raises(LintError, match="sintaxis"):
        run(ctx, cmd="str_replace", path="calc.py", old="return a + b  # BUG", new="return (a * b")
    assert "a + b" in (ctx.cfg.workspace / "calc.py").read_text()     # no se aplicó


def test_linter_no_bloquea_si_el_archivo_ya_venia_roto(ctx):
    f = ctx.cfg.workspace / "roto.py"
    f.write_text("def f(:\n    x = 1\n", newline="\n")
    run(ctx, cmd="str_replace", path="roto.py", old="x = 1", new="x = 2")
    assert "x = 2" in f.read_text()


def test_create_valida_sintaxis_y_no_pisa(ctx):
    with pytest.raises(LintError):
        run(ctx, cmd="create", path="n.py", content="def (")
    with pytest.raises(ActionError, match="ya existe"):
        run(ctx, cmd="create", path="calc.py", content="x")
    assert "creado sub/n.py" in run(ctx, cmd="create", path="sub/n.py", content="x = 1\n")


def test_linter_ignora_lenguajes_sin_linter(ctx):
    assert "creado" in run(ctx, cmd="create", path="a.js", content="function (")


# --------------------------------------------------------------------------- rutas

@pytest.mark.parametrize("bad", ["../fuera.py", "/etc/passwd", "C:\\Windows\\win.ini",
                                 "sub/../../fuera.py", ".git/config", ""])
def test_path_traversal_bloqueado(ctx, bad):
    with pytest.raises(ActionError):
        resolve_path(bad, ctx.cfg)


def test_directorio_hermano_con_mismo_prefijo(ctx, tmp_path):
    """El bug de startswith: /tmp/repo_malo pasaba por estar 'dentro' de /tmp/repo."""
    evil = Path(str(ctx.cfg.workspace) + "_malo")
    evil.mkdir()
    with pytest.raises(ActionError, match="fuera del workspace"):
        resolve_path(f"../{evil.name}/x.py", ctx.cfg)


def test_symlink_hacia_afuera_bloqueado(ctx, tmp_path):
    target = tmp_path / "secreto.txt"
    target.write_text("s")
    try:
        os.symlink(target, ctx.cfg.workspace / "link.txt")
    except OSError:
        pytest.skip("crear symlinks requiere modo desarrollador en Windows")
    with pytest.raises(ActionError, match="fuera del workspace"):
        run(ctx, cmd="view", path="link.txt")


def test_ruta_absoluta_de_docker_se_traduce(ctx):
    assert resolve_path("/workspace/calc.py", ctx.cfg) == ctx.cfg.workspace / "calc.py"


# --------------------------------------------------------------------------- view

def test_view_paginado_con_pista_de_continuacion(ctx):
    (ctx.cfg.workspace / "largo.py").write_text("".join(f"x{i} = {i}\n" for i in range(250)))
    obs = run(ctx, cmd="view", path="largo.py", offset=100)
    assert obs.startswith("largo.py [líneas 101-200 de 250]")
    assert "  101 | x100 = 100" in obs and "offset=200" in obs
    with pytest.raises(ActionError, match="fuera de rango"):
        run(ctx, cmd="view", path="largo.py", offset=250)


def test_view_de_directorio_lista_sin_git(ctx):
    (ctx.cfg.workspace / "pkg").mkdir()
    obs = run(ctx, cmd="view", path=".")
    assert "calc.py" in obs and "pkg/" in obs and ".git" not in obs


# --------------------------------------------------------------------------- bash / submit

def test_bash_usa_sandbox_y_trunca(ctx):
    ctx.sandbox.results = [CommandResult(1, "x" * 20_000)]
    obs = run(ctx, cmd="bash", input="pytest -q")
    assert ctx.sandbox.commands == ["pytest -q"]
    assert obs.startswith("exit=1") and "[truncado" in obs and len(obs) < 7_000


def test_bash_timeout_se_informa(ctx):
    ctx.sandbox.results = [CommandResult(124, "parcial", timed_out=True)]
    out = execute({"cmd": "bash", "input": "sleep 999"}, ctx)
    assert "TIMEOUT" in out.observation and "timeout" in out.flags


def test_bash_respeta_policy(ctx):
    class NoCurl:
        def check(self, cmd):
            return "sin red" if "curl" in cmd else None
    ctx.policy = NoCurl()
    with pytest.raises(ActionError, match="política: sin red"):
        run(ctx, cmd="bash", input="curl x")
    assert ctx.sandbox.commands == []


def test_submit_sin_cambios_rechazado_y_con_cambios_muestra_parche(ctx):
    with pytest.raises(ActionError, match="nada que enviar"):
        run(ctx, cmd="submit")
    run(ctx, cmd="str_replace", path="calc.py", old="a + b", new="a * b")
    obs = run(ctx, cmd="submit")         # sin commit previo: el parche igual lo incluye
    assert obs.startswith("SUBMIT") and "+    return a * b" in obs


def test_truncate_conserva_inicio_y_final():
    t = truncate("A" * 100 + "Z" * 100, 50)
    assert t.startswith("A" * 25) and t.endswith("Z" * 25) and "truncado 150" in t
