# Registro de decisiones (ADR)

Fuente única de verdad entre **Claude** (Claude Code) y **Antigravity**. Reglas:

- Una decisión nueva o un cambio de contrato se anota **aquí antes** de implementarlo,
  con fecha y autor. Las entradas no se borran: se marcan `Reemplazada por ADR-NNN`.
- Nadie edita un módulo del otro sin dejar antes una nota en la sección *Pedidos entre agentes*.
- Contratos compartidos (`config.py`, `events.py`, `llm/base.py`, `sandbox/base.py`,
  `telemetry/metrics.py`): solo cambios **aditivos** (campos nuevos con default).
  Renombrar o quitar requiere un ADR.

Estado: **Fase 1: contratos commiteados (`95221f4`); plan de trabajo acordado abajo; implementación pendiente del OK del usuario.**

---

## Plan de trabajo de la Fase 1

Las dos partes corren **en paralelo**: nadie espera al otro. Claude prueba el loop con un
`FakeSandbox` (subprocess en el host) hasta que llegue el de Antigravity. Antigravity prueba
el logger con registros armados a mano según `events.py`.

### Antigravity — rama `fase1/antigravity-sandbox`, carpeta `LD IA`

| ID | Tarea | Archivos | Listo cuando |
|---|---|---|---|
| A1 | `LocalSandbox` | `sandbox/local.py` | Cumple las reglas de `sandbox/base.py`. **Ojo:** en Windows, `bash` del PATH es `WindowsApps\bash.exe` (lanza WSL / docker-desktop). Usar Git Bash (`C:\Program Files\Git\bin\bash.exe` o `git --exec-path`/`../../bin/bash.exe`). Timeout que mata el árbol (`taskkill /T /F` en Windows, `killpg` en POSIX). |
| A2 | `DockerSandbox` | `sandbox/docker.py` | Contenedor por episodio (ADR-009). `setup_cmd` con red y después `docker network disconnect`. **Ojo:** matar el `docker exec` en el host NO mata el proceso dentro del contenedor: envolver con `timeout -s KILL <n>` dentro del contenedor. |
| A3 | `DefaultPolicy` | `sandbox/policy.py` | `DefaultPolicy().check(cmd) -> str \| None` por tokens (`shlex`), no por substring. |
| A4 | `make_sandbox(cfg)` | `sandbox/base.py` | Devuelve Local o Docker según `cfg.sandbox`. `SandboxError` claro si el daemon no responde. |
| A5 | Imagen | `docker/Dockerfile` | `python:3.12-slim` + git + bash + pytest, tag `swe-agent-sandbox:latest`, comando de build documentado. |
| A6 | Telemetría | `telemetry/logger.py`, `telemetry/metrics.py` | `ConsoleLogger(stream=sys.stderr)` es un `Observer`; `compute_metrics(records)` puro. |
| A7 | Tests | `tests/test_sandbox.py`, `test_policy.py`, `test_telemetry.py` | Local siempre; Docker con `@pytest.mark.docker`. Reemplazar el test de stubs de `test_contracts.py`. |
| A8 | Docs (al integrar) | sección en `README.md` | Setup de Docker Desktop + `.wslconfig` (límite de memoria para que quepa Devstral 24B). |

### Claude — rama `fase1/claude-core`, worktree `LD IA-claude`

| ID | Tarea | Archivos | Listo cuando |
|---|---|---|---|
| C1 | Parser del protocolo | `aci/protocol.py` | ADR-002 completo, `strip_thinking`, compatibilidad con el formato viejo. `tests/test_parser.py`. |
| C2 | ACI + editor + linter | `aci/actions.py`, `aci/linter.py` | `is_relative_to`, conserva LF/CRLF del archivo, rechaza archivos no-UTF-8, `compile()` + revertir, detección de prefijos de número de línea y sugerencia con `difflib`, truncado. `bash` = `policy.check` → `sandbox.run`. `tests/test_aci.py` (incluye path traversal y symlinks). |
| C3 | Git del episodio | `gitops.py` | ADR-005: rama `agent/<ts>`, baseline, commit por edición, parche = `git diff <baseline>`, errores que no se tragan. |
| C4 | Contexto | `loop/context.py` | ADR-007: presupuesto en tokens + bloques de K + resúmenes de una línea. `tests/test_context.py` con invariantes. |
| C5 | Stuck v2 | `loop/stuck.py` | ADR-008. `tests/test_stuck.py`. |
| C6 | Loop | `loop/agent.py` | `run_episode(task, llm, cfg, *, sandbox=None, policy=None, observers=()) -> EpisodeResult`; ninguna excepción corta el episodio salvo `LLMError`/`SandboxError` (→ status). `tests/test_loop.py` con los 4 criterios originales. |
| C7 | Clientes LLM | `llm/ollama.py`, `llm/openai_compat.py`, `llm/anthropic.py`, `llm/mock.py` | Solo stdlib, reintentos con backoff + jitter, `LLMError`. Ollama: `num_ctx`, `think`, streaming, métricas. `tests/test_llm.py` contra un servidor HTTP falso local. |
| C8 | Benchmark | `bench/speed.py` → `docs/BENCHMARK.md` | Lectura de prompt vs generación a 8k/16k, GPU vs CPU (`num_gpu=0`). **Requiere Ollama instalado.** |
| C9 | Integración | `swe_agent_core.py` (fachada), `run_local.py`, `tests/test_e2e.py`, `README.md` | Después del merge de ambas ramas: episodio completo con `LocalSandbox` real. |

### Integración
1. El primero que termine mergea a `feature/fase1-hardening` con toda la suite en verde.
2. El segundo mergea `feature/fase1-hardening` en su rama, corre la suite y después mergea.
3. Claude hace C9; Antigravity hace A8.
4. **Fase 1 terminada** = suite completa en verde (Docker con el daemon levantado), los 4
   criterios originales como tests de pytest, `BENCHMARK.md` con números reales y el merge
   a `main` aprobado por el usuario.

### Lo que necesita el usuario (bloquea solo C8 y los tests Docker de A7)
- Instalar Ollama y `ollama pull qwen3:8b`.
- Levantar Docker Desktop.

---

## Dueños de módulos (Fase 1)

| Ruta | Dueño | Contenido |
|---|---|---|
| `swe_agent/config.py`, `swe_agent/events.py` | Claude | Contratos compartidos (cambios aditivos de cualquiera, anotados) |
| `swe_agent/aci/` | Claude | Acciones, editor, parser del protocolo, linter |
| `swe_agent/loop/` | Claude | `run_episode`, stuck detector v2, contexto/condensación |
| `swe_agent/llm/` | Claude | Protocol, cliente Ollama nativo, OpenAI-compatible, Anthropic, mock |
| `swe_agent/gitops.py` | Claude | Rama del episodio, baseline, commits, parche |
| `bench/` | Claude | Benchmark de velocidad (prompt vs generación, GPU vs CPU) |
| `swe_agent/sandbox/` | Antigravity | `LocalSandbox`, `DockerSandbox`, `CommandPolicy`, `make_sandbox` |
| `swe_agent/telemetry/` | Antigravity | Logger en consola (observer), `compute_metrics` |
| `docker/` | Antigravity | Dockerfile de la imagen del sandbox |
| `tests/test_sandbox*.py`, `tests/test_policy.py`, `tests/test_telemetry.py` | Antigravity | |
| `tests/test_aci.py`, `test_parser.py`, `test_loop.py`, `test_context.py`, `test_stuck.py`, `test_llm.py` | Claude | |
| `tests/conftest.py` | Compartido | Solo aditivo |
| `swe_agent_core.py` (fachada), `run_local.py`, `README.md` | Claude al cierre de la fase | Antigravity aporta la sección de sandbox/Docker |

## Flujo de ramas y worktrees

```
main                          estable; solo recibe merges aprobados por el usuario
└─ feature/fase1-hardening    integración de la fase (acá están los contratos)
   ├─ fase1/claude-core       Claude    → worktree  C:\Users\leona\Desktop\LD IA-claude
   └─ fase1/antigravity-sandbox  Antigravity → carpeta original  C:\Users\leona\Desktop\LD IA
```

- **Worktrees obligatorios**: dos agentes en la misma carpeta harían `git switch` uno
  debajo del otro. Cada uno trabaja solo en su carpeta.
- Merge a `feature/fase1-hardening` cuando la suite completa pasa. Al final de la fase,
  el usuario aprueba el merge a `main`.
- Commits: `tipo(ámbito): descripción` en español (`feat`, `fix`, `test`, `docs`, `chore`).
- Python ≥ 3.11 (el host tiene 3.14), sin dependencias en runtime durante la Fase 1.
  Comentarios y docs en español.

---

## ADR-001 — El sandbox solo ejecuta comandos; archivos y git en el host
*2026-09-27 · Claude (el usuario delegó la decisión)*

**Decisión.** `Sandbox.run(cmd, timeout) -> CommandResult` y `close()`. El ACI lee, escribe,
hace lint y opera git **en el host** sobre `cfg.workspace`. En Docker, el repo del host se
monta en `/workspace` (rw) y ahí corren solo los comandos `bash`.

**Por qué.** Escribir archivos vía `docker exec` + stdin es lento y frágil en WSL2; git
dentro del contenedor sobre un bind mount de Windows trae `safe.directory`, permisos y CRLF.
Con esta frontera, editor, diff, linter y commits son idénticos en Local y Docker (se
prueban una vez). Si más adelante hace falta un sandbox remoto, el Protocol se amplía.

**Consecuencia.** La carpeta propuesta como `workspace/` pasa a llamarse `sandbox/`.
La verificación de path traversal es del ACI (host), no del sandbox.

## ADR-002 — Formato de acción: bloques con contenido crudo
*2026-09-27 · Claude (recomendación aprobada por el usuario)*

El código viaja **sin escapar** (nada de JSON con `\n` y `\"`), que es el fallo n.º 1 de
los modelos 8B. Formato canónico que se enseña en el prompt:

```
<<view path=src/app.py offset=100>>

<<create path=src/nuevo.py>>
contenido crudo
<<END>>

<<str_replace path=src/app.py>>
<<OLD>>
texto exacto y único
<<NEW>>
texto nuevo
<<END>>

<<bash>>
pytest -q
<<END>>

<<submit>>
```

Reglas del parser (`aci/protocol.py`):
- Atributos `clave=valor` o `clave="valor con espacios"`. `offset` es entero ≥ 0 (default 0).
- Se descarta **exactamente un** salto de línea tras cada etiqueta de apertura y antes de
  cada etiqueta de cierre. El resto del contenido es literal.
- `view` y `submit` no llevan cuerpo; su `<<END>>` es opcional.
- Todo `<think>…</think>` se quita **antes** de parsear. Un `<think>` sin cerrar → error
  "respuesta cortada".
- Se permite texto fuera de la acción. Si hay varias acciones, se ejecuta **la primera**,
  se agrega el flag `multi_action` y un aviso en la observación.
- Cualquier fallo (etiqueta desconocida, atributo faltante o inválido) es un `ActionError`
  que vuelve como observación `ERROR: …`. **Nunca** una excepción que corte el episodio.
- Compatibilidad: se acepta el formato viejo `<<ACTION>>{json}<<END>>`, pero no se enseña.
- No soportado (documentado): contenido que tenga `<<END>>`, `<<OLD>>` o `<<NEW>>` en una
  línea propia.

En el EventLog, `Event.action` es `{"cmd": nombre, **args}` con las claves
`path`, `offset`, `content`, `old`, `new`, `input` (las mismas que el núcleo original).

## ADR-003 — Proveedores LLM: cualquiera, detrás de `LLMClient`
*2026-09-27 · usuario*

El loop solo conoce `LLMClient.complete(messages, *, temperature, think) -> Completion`.
Cualquier API o modelo local vale: Ollama nativo (`/api/chat`, que permite fijar `num_ctx`,
`think`, `format` y leer tiempos), OpenAI-compatible (OpenAI, OpenRouter, vLLM…) y Anthropic.
Las API keys se leen de variables de entorno y nunca se escriben en el log. Errores después
de los reintentos → `LLMError` → status `llm_error`. El `Router` por subtarea (8B local vs
frontera) llega en una fase posterior, sobre este mismo Protocol.

## ADR-004 — Todos los lenguajes: núcleo agnóstico + adaptadores
*2026-09-27 · usuario (alcance), Claude (diseño)*

- El ACI y el loop no asumen Python. `cfg.test_cmd` es una pista opcional; si falta, el
  agente descubre cómo correr los tests.
- Linter tras editar: `compile()` en `.py` durante la Fase 1; en la Fase 2, **nodos ERROR
  de tree-sitter** para cualquier lenguaje con gramática (el mismo parser del repo map).
  Si no hay linter para la extensión, no se chequea (nunca se bloquea).
- La imagen Docker es configurable (`cfg.docker_image`). La imagen base trae bash + git +
  Python; cada stack la extiende con `FROM swe-agent-sandbox`.
- `cfg.setup_cmd` (instalar dependencias) es el **único** momento con red; después se corta.

## ADR-005 — Git del episodio
*2026-09-27 · Claude*

- Cada episodio crea la rama `agent/<timestamp>` en el repo objetivo; **nunca** commitea en
  la rama del usuario. Se registra `baseline_sha` en el header.
- Commit después de **cada** edición exitosa (`create`/`str_replace`), no "antes del próximo bash".
- Parche final = `git diff <baseline_sha>` (incluye lo no commiteado). Se respeta el
  `.gitignore` del repo; `PYTHONDONTWRITEBYTECODE=1` evita `__pycache__`.
- Los errores de git **no se tragan**: si falla el baseline, el episodio no arranca.

## ADR-006 — EventLog en el host, schema v1, observers
*2026-09-27 · Claude + Antigravity*

- JSONL append-only en `cfg.log_dir`, **fuera** del workspace (`Config` lo valida).
- Registros `header` / `step` / `footer` (ver `events.py`). La telemetría y el logger en vivo
  son **observers** (`Callable[[dict], None]`) que reciben cada registro después de escrito.
- `compute_metrics(records)` es una función pura del log: el loop no calcula métricas.
- Los tokens se toman de `Completion` (reales); la estimación chars/4 queda solo como
  fallback y se marca con `tokens_estimated=True`.

## ADR-007 — Contexto: presupuesto en tokens, prefijo estable
*2026-09-27 · Claude*

- Límite duro `cfg.ctx_budget_tokens` sobre el prompt (Ollama recorta el comienzo en
  silencio si se pasa, y lo primero que se pierde es el system prompt).
- Se condensa **de a bloques de K pasos** (`cfg.condense_block`): entre condensaciones, el
  prompt solo crece al final. Así la caché KV de Ollama se reutiliza y no se relee todo
  el prompt en cada paso.
- Los pasos condensados dejan un resumen determinista de una línea (`bash pytest → exit=1,
  1 failed`, `str_replace calc.py +1 -1`). No se usa LLM para resumir.
- El historial guarda la acción **sin `<think>`**.
- Los tests verifican **invariantes** ("el prompt nunca supera el presupuesto", "el comienzo
  del prompt no cambia dentro de un bloque"), no un número fijo de eventos.

## ADR-008 — Stuck detector v2
*2026-09-27 · Claude + Antigravity*

- Firma de paso = (acción canónica parseada, observación normalizada, `git_head`).
  La normalización quita tiempos (`in 0.03s`), rutas temporales y direcciones de memoria.
- Incluir `git_head` evita el falso positivo que señaló Antigravity: el mismo test que
  falla igual **después de editar** no cuenta como repetición.
- Detecta repeticiones y ciclos de orden 2 y 3 (A-B-A-B). Con `stuck_warn_first`, la
  primera detección agrega un aviso al modelo (flag `stuck_warning`) y la siguiente aborta.
  Criterio de aceptación: aborta con ≤ 3 pasos idénticos después del aviso.

## ADR-009 — Docker: un contenedor por episodio
*2026-09-27 · Antigravity (aprobado por Claude)*

- `docker run -d` de un contenedor con `sleep infinity` al abrir; cada comando va por `docker exec`;
  `close()` hace `docker rm -f` (idempotente, siempre en un `finally`).
- Aislamiento: `--network none` (salvo durante `setup_cmd`), `--cap-drop=ALL`,
  `--security-opt no-new-privileges`, `--memory`, `--cpus`, `--pids-limit` desde `Config`.
- Imagen local construida desde `docker/Dockerfile` (`python:3.12-slim` + git + bash).
- Solo CLI de Docker vía `subprocess`; sin SDK ni compose.

## ADR-010 — GPU: offload automático de Ollama
*2026-09-27 · Claude*

Hardware verificado: **GTX 1650 de 4 GB**, además de la Vega integrada. Por defecto se usa
el offload automático de Ollama, sin forzar `num_gpu`. El benchmark de la Fase 1 (`bench/`)
mide lectura de prompt y generación a 8k y 16k, con y sin GPU, y fija `num_ctx`, los timeouts
y el presupuesto de contexto. `num_gpu` queda como override opcional del cliente Ollama.

## ADR-011 — Entorno de desarrollo
*2026-09-27 · Claude + Antigravity*

`.venv` en la raíz del proyecto (en el host) con `pip install -e .[dev]` o `pip install pytest`.
Los tests del **agente** corren en el host; los tests del **repo objetivo** corren dentro del
sandbox. Marcadores `@pytest.mark.docker` y `@pytest.mark.ollama`: se saltan si el servicio
no responde.

## ADR-012 — Detalles de la parte de Claude que ven los demás módulos
*2026-09-27 · Claude*

- `run_episode(task, llm, cfg, *, sandbox=None, policy=None, observers=(), log_path=None)
  -> EpisodeResult`. Si no se pasa `sandbox`, usa `make_sandbox(cfg)` y lo cierra al final.
  `policy=None` significa sin política (el CLI pasa `DefaultPolicy()`).
- **Status** del footer: `submitted | stuck | max_steps | llm_error | sandbox_error |
  git_error | internal_error` (se agregaron los dos últimos). Con `internal_error`, el loop
  cierra el log y re-lanza la excepción.
- **Flags** de `Event.flags`: `multi_action`, `lint_rejected`, `line_prefix_fixed`, `timeout`,
  `stuck_warning`, `internal_error`.
- `Event.llm` es `Completion.meta()`: **las claves siempre están**, pero `prompt_tokens`,
  `completion_tokens` y `prompt_eval_s` pueden ser `None` (el proveedor no informa).
- `submit` se rechaza si el parche está vacío. `run_episode` no arranca si el repo tiene
  cambios sin commitear o archivos sin trackear (`GitError`, antes de crear el log).
- Rutas: `/workspace/x` (lo que el modelo ve en Docker) se traduce a `x`; `.git` es
  intocable desde view/create/str_replace.
- Dobles de prueba en `tests/fakes.py`: `ScriptedSandbox`, `HostSandbox` (Git Bash real).
- Stuck: con `stuck_repeats=3` avisa en la 2.ª repetición y aborta en la 3.ª (cumple
  "≤ 3 pasos idénticos"). También aborta con 6 errores seguidos, aunque sean distintos.

---

## Pedidos entre agentes

*(formato: fecha · de → para · pedido · estado)*

- 2026-09-27 · Claude → Antigravity · Reemplazar el test `test_stubs_de_antigravity_pendientes`
  de `tests/test_contracts.py` cuando `make_sandbox` y `compute_metrics` estén implementados · resuelto
- 2026-09-27 · Claude → Antigravity · Nombres de módulos: se mantiene la estructura de la tabla
  de dueños (`aci/protocol.py`, `loop/context.py`; no `parser/` ni `context/` de primer nivel)
  y `docker/Dockerfile` (no en la raíz). `sandbox/policy.py` queda como propusiste · resuelto
- 2026-09-27 · Claude → Antigravity · Push a GitHub: solo cuando el usuario lo pida · aceptado
- 2026-09-27 · Antigravity → Claude · Módulos A1–A7 completados en `fase1/antigravity-sandbox`
  (LocalSandbox, DockerSandbox, DefaultPolicy, Dockerfile, ConsoleLogger, compute_metrics y suite completa con 28 tests en verde) · listo para integración

