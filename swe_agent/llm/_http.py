"""HTTP JSON con reintentos (backoff exponencial + jitter), solo stdlib. Dueño: Claude."""
from __future__ import annotations

import json
import random
import socket
import time
import urllib.error
import urllib.request
from typing import Callable, Iterator

from .base import LLMError

RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}   # 529 = Anthropic sobrecargado


def _backoff(attempt: int, base: float, retry_after: str | None) -> float:
    if retry_after:
        try:
            return min(60.0, float(retry_after))
        except ValueError:
            pass
    return min(30.0, base * 2 ** attempt) + random.uniform(0, base)


def request(url: str, body: dict, headers: dict, *, timeout: float, retries: int = 3,
            backoff: float = 1.0, stream: bool = False,
            read: Callable[[Iterator[bytes]], dict] | None = None) -> dict:
    """POST JSON. Con stream=True, `read` consume las líneas NDJSON; el timeout es
    entonces de INACTIVIDAD (tiempo máximo entre trozos), no de la llamada entera: en CPU
    una respuesta puede tardar minutos mientras sigan llegando tokens."""
    data = json.dumps(body).encode("utf-8")
    hdrs = {"Content-Type": "application/json", **headers}
    last = "sin intentos"
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
        retry_after = None
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if stream and read is not None:
                    return read(iter(resp.readline, b""))
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            last = f"HTTP {e.code}: {detail}"
            if e.code not in RETRY_STATUS:
                raise LLMError(last) from e             # 4xx: reintentar no sirve
            retry_after = e.headers.get("Retry-After")
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
            last = f"{type(e).__name__}: {getattr(e, 'reason', e)}"
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            last = f"respuesta no es JSON válido: {e}"
        if attempt < retries:
            time.sleep(_backoff(attempt, backoff, retry_after))
    raise LLMError(f"{url}: falló tras {retries + 1} intentos; último error: {last}")
