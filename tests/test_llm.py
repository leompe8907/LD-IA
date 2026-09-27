"""C7 — clientes LLM contra un servidor HTTP falso local (sin red)."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from swe_agent.llm import _http
from swe_agent.llm.anthropic import AnthropicClient
from swe_agent.llm.base import LLMError
from swe_agent.llm.ollama import OllamaClient
from swe_agent.llm.openai_compat import OpenAICompatClient

MSGS = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hola"}]


@pytest.fixture(autouse=True)
def sin_esperas(monkeypatch):
    monkeypatch.setattr(_http.time, "sleep", lambda s: None)


@pytest.fixture
def server():
    """Servidor que responde según una cola de (status, body|lista de líneas NDJSON)."""
    state = {"queue": [], "requests": []}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["requests"].append({"path": self.path, "body": body,
                                      "headers": {k.lower(): v for k, v in self.headers.items()}})
            status, payload = state["queue"].pop(0)
            self.send_response(status)
            self.end_headers()
            if isinstance(payload, list):
                for chunk in payload:
                    self.wfile.write((json.dumps(chunk) + "\n").encode())
            else:
                self.wfile.write(json.dumps(payload).encode())

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    state["url"] = f"http://127.0.0.1:{srv.server_address[1]}"
    yield state
    srv.shutdown()


def test_ollama_stream_junta_trozos_think_y_metricas(server):
    server["queue"].append((200, [
        {"message": {"content": "", "thinking": "mmm"}, "done": False},
        {"message": {"content": "<<submit"}, "done": False},
        {"message": {"content": ">>"}, "done": True, "model": "qwen3:8b",
         "prompt_eval_count": 900, "eval_count": 12, "prompt_eval_duration": 3_000_000_000},
    ]))
    c = OllamaClient("qwen3:8b", server["url"], num_gpu=0).complete(MSGS)
    assert c.text == "<think>mmm</think>\n<<submit>>"
    assert (c.prompt_tokens, c.completion_tokens, c.prompt_eval_s) == (900, 12, 3.0)
    req = server["requests"][0]
    assert req["path"] == "/api/chat" and req["body"]["think"] is False
    assert req["body"]["options"]["num_ctx"] == 16_384 and req["body"]["options"]["num_gpu"] == 0


def test_ollama_think_none_no_se_envia(server):
    server["queue"].append((200, {"message": {"content": "ok"}, "done": True}))
    OllamaClient("m", server["url"], think=None, stream=False).complete(MSGS)
    assert "think" not in server["requests"][0]["body"]


def test_reintenta_5xx_y_despues_responde(server):
    server["queue"] += [(503, {"error": "ocupado"}), (500, {"error": "x"}),
                        (200, {"choices": [{"message": {"content": "hola"}}],
                               "usage": {"prompt_tokens": 3, "completion_tokens": 1}})]
    c = OpenAICompatClient("gpt", "k", server["url"], retries=3).complete(MSGS)
    assert c.text == "hola" and c.prompt_tokens == 3 and len(server["requests"]) == 3
    assert server["requests"][0]["headers"]["authorization"] == "Bearer k"


def test_4xx_no_se_reintenta_y_agotado_es_llm_error(server):
    server["queue"].append((400, {"error": "modelo inválido"}))
    with pytest.raises(LLMError, match="HTTP 400"):
        OpenAICompatClient("x", "k", server["url"]).complete(MSGS)
    assert len(server["requests"]) == 1
    server["queue"] += [(503, {})] * 3
    with pytest.raises(LLMError, match="3 intentos"):
        OpenAICompatClient("x", "k", server["url"], retries=2).complete(MSGS)


def test_servidor_apagado_es_llm_error():
    with pytest.raises(LLMError):
        OllamaClient("m", "http://127.0.0.1:9", retries=0, timeout=2).complete(MSGS)


def test_openai_razonamiento_separado_se_envuelve(server):
    server["queue"].append((200, {"choices": [{"message": {"content": "<<submit>>",
                                                           "reasoning_content": "pensé"}}]}))
    assert OpenAICompatClient("x", "", server["url"]).complete(MSGS).text.startswith("<think>pensé")


def test_anthropic_separa_system_y_lee_usage(server):
    server["queue"].append((200, {"content": [{"type": "text", "text": "<<submit>>"}],
                                  "usage": {"input_tokens": 50, "output_tokens": 4},
                                  "model": "claude-opus-5-5"}))
    c = AnthropicClient(api_key="k", base_url=server["url"] + "/v1/messages").complete(MSGS)
    body = server["requests"][0]["body"]
    assert body["system"] == "sys" and body["messages"] == [{"role": "user", "content": "hola"}]
    assert server["requests"][0]["headers"]["x-api-key"] == "k"
    assert (c.text, c.prompt_tokens, c.completion_tokens) == ("<<submit>>", 50, 4)
