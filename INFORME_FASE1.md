# Informe de Arquitectura — Fase 1: Hardening

**Proyecto:** Mini-SWE-Agent Local  
**Fecha:** 27 de Septiembre de 2026  
**Autores / Arquitectos:** Antigravity 🤝 Claude Code  
**Rama:** `feature/fase1-hardening` (`fase1/antigravity-sandbox` + `fase1/claude-core`)  
**Estado:** ✅ Aprobado y verificado (124 tests PASSED, 3 SKIPPED)

---

## 1. Resumen Ejecutivo

La Fase 1 transformó el núcleo minimalista inicial (`swe_agent_core.py`, ~280 líneas de stdlib) en una plataforma de agentes para ingeniería de software **endurecida, segura, determinista y modular**, preparada tanto para ejecución local ágil como para aislamiento hermético en contenedores Docker, sin dependencias pesadas y optimizada para operar en el hardware específico del usuario:
* **CPU:** AMD Ryzen 5 4600H (6C/12T Zen 2).
* **RAM:** 40 GB de memoria de sistema.
* **GPU:** NVIDIA GeForce GTX 1650 (4 GB VRAM dedicada, CUDA 12.7) + AMD Radeon Vega 6 integrada.
* **Inferencia:** Ollama local con modelo `qwen3:8b` (o `qwen2.5-coder:7b`).

---

## 2. Qué se Cambió del Núcleo Original y Por Qué

| Componente Original | Limitación Crítica Identificada | Nueva Implementación (Fase 1) | Justificación de Arquitectura |
|---|---|---|---|
| **Ejecución Bash** | `subprocess.run(shell=True)` en el host directamente sobre la carpeta del proyecto. | Abstracción `Sandbox` con dos implementaciones: `LocalSandbox` (con Git Bash y terminación de árboles de procesos vía `taskkill /T /F`) y `DockerSandbox` (contenedor efímero, `--network none`, cgroups de 4 GB RAM / 4 CPUs / 256 PIDs, y `timeout -s KILL` en cgroup). | Aislamiento hermético: código no confiable (`pytest`, scripts del repo analizado) no puede exfiltrar datos ni dañar el sistema operativo host. |
| **Política de Seguridad** | Lista negra ingenua de substrings: `banned = ["rm -rf /", "git push", "curl", "wget", "sudo"]`. | `DefaultPolicy` con tokenización real mediante `shlex.shlex(punctuation_chars=True)`. | Evita falsos positivos en comandos legítimos (ej: `echo "curl es genial"` o `grep -nE "foo\|bar"`) mientras bloquea ejecutables y subcomandos prohibidos reales o destructivos (`rm -rf /`). Si el parseo es ambiguo, pasa a bash para no bloquear al modelo. |
| **Formato de Acción** | JSON embebido en `<<ACTION>>{json}<<END>>`. | Protocolo canónico de bloques con contenido crudo: `<<str_replace path=x>>\n<<OLD>>\n...\n<<NEW>>\n...\n<<END>>`. | Editar código dentro de cadenas JSON es la principal causa de fallas de sintaxis en modelos 7B/8B (escapado de comillas, barras invertidas y saltos de línea). El texto crudo elimina el 100% de ese ruido. |
| **Path Traversal y Symlinks** | `str(path).startswith(str(ws))` | `Path.is_relative_to()` canónico con validación estricta y protección de la carpeta `.git`. | Evita escapes de directorio (`C:\repo_malo` vs `C:\repo`) y enlaces simbólicos que apunten fuera de la raíz del workspace. |
| **Linter de Edición** | Ausente en código (solo mencionado en docstrings). | Validación sintáctica inmediata post-edición con `compile(code, path, "exec")` para archivos Python. | Si el modelo 8B introduce un `SyntaxError`, la edición se rechaza en el acto y se revierte el buffer en memoria antes de tocar git, entregándole feedback formativo al agente. |
| **Operaciones Git** | Commits diferidos antes del próximo `bash` y sobre la rama del usuario; `patch` calculado con `diff HEAD~1 HEAD`. | Módulo `gitops.py`: rama efímera `agent/<timestamp>`, commit automático inmediatamente tras cada edición exitosa, y cálculo del parche final relativo al commit `baseline` inicial (`git diff <baseline_sha>..HEAD`). | Garantiza que el trabajo del agente nunca contamine la rama de trabajo del desarrollador y que el diff represente fielmente la totalidad de las modificaciones. |
| **Gestión de Contexto** | Condensación simple basada en N pasos fijos recientes; eliminaba eventos de uno en uno. | Condensación en bloques de $K$ pasos (`loop/context.py`) con presupuesto estricto de tokens (`ctx_budget_tokens`). Resúmenes deterministas de una línea. | **Preservación crítica de la caché KV de Ollama**: cambiar el prefijo del prompt en cada paso fuerza a la CPU/GPU a reprocesar todo el contexto (minutos por llamada). Con prefijo estable, el procesamiento del prompt toma fracciones de segundo. |
| **Stuck Detector** | Comparación de strings crudos (incluyendo divagaciones) y ventana simple de observaciones. | `StuckDetector` v2: firmas compuestas `(acción canónica, observación normalizada, git_head)`, detección de ciclos de orden 2 y 3 (A-B-A-B), y advertencia previa al modelo antes de abortar. | Evita falsos positivos cuando un test falla de forma idéntica pero el agente ya modificó el código (`git_head` distinto); detecta bucles oscilantes complejos. |
| **Fuga de Razonamiento** | `<think>` de Qwen3 se guardaba en el historial y permitía falsas acciones dentro de pensamientos. | Extracción y descarte estricto de `<think>...</think>` antes del parseo y antes de persistir en el `EventLog`. | Aumenta el determinismo del agente y previene alucinaciones sintácticas. |
| **Telemetría y Métricas** | Print crudo en terminal; ausencia de métricas estructuradas. | `ConsoleLogger` (observer seguro para Windows/cp1252) y función pura `compute_metrics(records)` en `swe_agent/telemetry/`. | Métricas formales post-episodio: duración, tokens de entrada/salida (reales o estimados), tiempo de lectura de prompt (`prompt_eval_s`), commits, diffstats y warnings. |
| **Estructura de Código** | Archivo monolítico de 280 líneas. | Paquete modular `swe_agent/` con submódulos de <250 líneas y contratos desacoplados, manteniendo `swe_agent_core.py` como fachada retrocompatible. | Mantenibilidad, testabilidad unitaria y desarrollo concurrente sin conflictos. |

---

## 3. Estado de la Suite de Pruebas

La suite completa bajo `pytest` cuenta con **127 pruebas unitarias, de integración y de contrato**:
* **124 PASSED** en verde.
* **3 SKIPPED**:
  * 1 test de symlinks omitido automáticamente en Windows si el SO no está en Modo Desarrollador.
  * 2 tests de integración directa con Docker omitidos limpiamente si el daemon de Docker Desktop no se encuentra encendido (cumpliendo ADR-011).

### Cobertura de Casos Críticos de 1.2
1. **Path Traversal:** Verificado en `tests/test_aci.py` con escapes `../`, rutas absolutas ajenas y symlinks.
2. **Timeout de Bash:** Verificado en `tests/test_sandbox.py` y `tests/test_e2e.py` asegurando retorno de `exit_code=124`, `timed_out=True` y terminación del árbol de procesos.
3. **Política de Comandos:** Verificado en `tests/test_policy.py` con comandos encadenados, tuberías, y respeto a cadenas con operadores legítimos (`grep -E "a|b"`, `python -c "import sys; print(1)"`).
4. **Truncamiento de Observaciones:** Verificado en `tests/test_aci.py` con salidas >6,000 caracteres preservando cabecera y cola.
5. **Condensación de Historial:** Verificado en `tests/test_context.py` demostrando invarianza del prefijo de tokens y respeto al presupuesto duro.
6. **Replay Determinista:** Verificado en `tests/test_contracts.py` y `tests/test_telemetry.py` garantizando la reconstrucción exacta del episodio a partir del JSONL.

---

## 4. Qué Quedó Pendiente para las Fases 2 a 5 y su Justificación

Conforme al roadmap original y los Acuerdos de Decisiones de Arquitectura (ADR-001 a ADR-012), se dejaron pendientes los siguientes componentes de forma justificada:

### Fase 2: Repo Map con Tree-Sitter y Acción `search`
* **Pendiente:** Integración de gramáticas Tree-sitter para extracción de definiciones de símbolos y referencias, y ordenamiento de contexto mediante PageRank en Python puro.
* **Justificación:** Requiere descargar o compilar *wheels* de Tree-sitter compatibles con Python 3.14. En Fase 1 el objetivo era afianzar el núcleo de ejecución; para repos pequeños de prueba (1–3 archivos), `view`, `grep` y `find` bastan plenamente.

### Fase 3: Critic Model (Verificación de Diff antes de Submit)
* **Pendiente:** Módulo verificador que evalúa el parche generado mediante filtros deterministas (no tocar tests, diff no vacío, sintaxis limpia) y consulta opcional a un LLM antes de ejecutar `submit`.
* **Justificación:** Un modelo 8B tiene sesgo de confirmación al evaluarse a sí mismo; requiere primero estabilizar el benchmark de inferencia (Fase 1 C8) para no duplicar los tiempos de ejecución por paso innecesariamente.

### Fase 4: Pipeline estilo Agentless para Bugs Acotados
* **Pendiente:** Modo de ejecución de paso directo (localización de archivos → localización de funciones → parche candidato en un solo disparo) sin bucle de memoria episódica.
* **Justificación:** Es un paradigma alternativo al loop conversacional SWE-agent. La Fase 1 garantizó que el motor de ejecución (`Sandbox`, `gitops`, `EventLog`) sirva de cimiento idéntico para este pipeline.

### Fase 5: CLI Unificado, Mini-Benchmark y Evaluación Sistemática
* **Pendiente:** Evaluación comparativa con 3 repos reales de prueba (calc, parser, CLI) ejecutando 3 repeticiones por modelo (Qwen3 8B vs Devstral 24B), midiendo tasas de éxito y volcando los resultados en `docs/EVALUACION.md`.
* **Justificación:** Requiere que el usuario tenga levantados los servicios auxiliares (`ollama serve` con los modelos descargados y Docker Desktop activo) para ejecutar las corridas no asistidas de larga duración.

---

## 5. Instrucciones de Verificación Local

1. **Activar entorno y correr la suite:**
   ```powershell
   .\.venv\Scripts\pytest -v
   ```
2. **Ejecutar episodio de demostración con LocalSandbox:**
   ```powershell
   .\.venv\Scripts\python run_local.py --repo .\tests\test_repo --task "Arregla calc.mul" --sandbox local
   ```
3. **Construir y verificar imagen Docker (requiere Docker Desktop activo):**
   ```powershell
   docker build -t swe-agent-sandbox:latest -f docker/Dockerfile .
   ```
