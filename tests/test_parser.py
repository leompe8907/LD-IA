"""C1 — parser del protocolo de bloques crudos (ADR-002)."""
from __future__ import annotations

import pytest

from swe_agent.aci.protocol import ActionError, parse_action, render_action, strip_thinking


def act(raw: str) -> dict:
    return parse_action(raw).action


def test_view_con_y_sin_offset_y_end_opcional():
    assert act("<<view path=src/app.py>>") == {"cmd": "view", "path": "src/app.py", "offset": 0}
    assert act("<<view path=a.py offset=100>>\n<<END>>")["offset"] == 100
    assert act('<<view path="dir con espacios/a.py">>')["path"] == "dir con espacios/a.py"


def test_str_replace_contenido_crudo_sin_escapar():
    raw = ('Voy a arreglar el bug.\n<<str_replace path=calc.py>>\n<<OLD>>\n'
           '    return a + b  # "BUG"\\n\n<<NEW>>\n    return a * b\n<<END>>')
    a = act(raw)
    assert a["old"] == '    return a + b  # "BUG"\\n'      # comillas y barras literales
    assert a["new"] == "    return a * b"


def test_str_replace_new_vacio_borra():
    a = act("<<str_replace path=a.py>>\n<<OLD>>\nx = 1\n<<NEW>>\n<<END>>")
    assert a["old"] == "x = 1" and a["new"] == ""


def test_create_y_bash():
    a = act("<<create path=pkg/n.py>>\ndef f():\n    return 1\n<<END>>")
    assert a["content"] == "def f():\n    return 1"
    assert act("<<bash>>\npytest -q\n<<END>>") == {"cmd": "bash", "input": "pytest -q"}


def test_crlf_del_modelo_se_normaliza():
    a = act("<<str_replace path=a.py>>\r\n<<OLD>>\r\nx\r\n<<NEW>>\r\ny\r\n<<END>>")
    assert (a["old"], a["new"]) == ("x", "y")


def test_think_se_descarta_incluso_con_acciones_adentro():
    raw = "<think>quizás <<bash>>\nrm -rf x\n<<END>></think>\n<<view path=a.py>>"
    assert act(raw)["cmd"] == "view"
    assert strip_thinking("razonando...</think><<submit>>") == "<<submit>>"   # plantilla abrió <think>
    with pytest.raises(ActionError, match="cortada"):
        parse_action("<think>pensando sin fin")


def test_multiples_acciones_se_usa_la_primera():
    p = parse_action("<<view path=a.py>>\n<<bash>>\nls\n<<END>>")
    assert p.action["cmd"] == "view" and p.multi


def test_formato_legacy_json():
    a = act('<<ACTION>>{"cmd":"bash","input":"pytest -q"}<<END>>')
    assert a == {"cmd": "bash", "input": "pytest -q"}


@pytest.mark.parametrize("raw, msg", [
    ("sin acción", "ninguna acción"),
    ("<<edit path=a.py>>", "desconocida"),
    ("<<view>>", "falta el argumento 'path'"),
    ("<<view path=a.py offset=abc>>", "entero"),
    ("<<view path=a.py offset=-1>>", ">= 0"),
    ("<<bash>>\nls", "<<END>>"),
    ("<<bash>>\n   \n<<END>>", "vacío"),
    ("<<str_replace path=a.py>>\nx\n<<END>>", "<<OLD>>"),
    ("<<view path=a.py basura>>", "atributos inválidos"),
    ('<<ACTION>>{"cmd":"nope"}<<END>>', "desconocida"),
    ('<<ACTION>>{"cmd":"view"}<<END>>', "falta el argumento"),     # antes: KeyError
    ("<<ACTION>>[1, 2]<<END>>", "objeto JSON"),                     # antes: AttributeError
    ("<<ACTION>>{mal json}<<END>>", "JSON inválido"),
])
def test_errores_son_action_error_nunca_otra_excepcion(raw, msg):
    with pytest.raises(ActionError, match=msg):
        parse_action(raw)


@pytest.mark.parametrize("a", [
    {"cmd": "view", "path": "a b/c.py", "offset": 5},
    {"cmd": "view", "path": "c.py", "offset": 0},
    {"cmd": "submit"},
    {"cmd": "bash", "input": "pytest -q\necho fin"},
    {"cmd": "create", "path": "n.py", "content": "x = 1\n"},
    {"cmd": "str_replace", "path": "a.py", "old": "a\n  b", "new": ""},
])
def test_render_y_parse_son_inversos(a):
    assert act(render_action(a)) == a
