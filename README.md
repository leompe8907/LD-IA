# 🤖 Mini-SWE-Agent Local
### Agente de ingeniería de software minimalista, ejecutable 100% en tu hardware

Arquitectura destilada del análisis de **OpenHands, SWE-agent, Mini-SWE-Agent,
Agentless y Aider** — el scaffold commodity (sandbox + loop + ACI) con las lecciones
de diseño de cada uno, corriendo con modelos locales vía Ollama.

---

## 1. ¿Qué es esto?

Un núcleo de agente SWE de ~280 líneas (sin dependencias externas) que:

| Pieza | Origen | Qué hace |
|---|---|---|
| **ACI restringido** | SWE-agent | Solo 5 acciones (`view`, `create`, `str_replace`, `bash`, `submit`), cada una validada |
| **Editor validado** | SWE-agent | `old_str` debe ser único; devuelve diff; bloquea corrupciones |
| **Estado en git** | Mini-SWE-Agent + Aider | Commit automático tras cada edición → reversibilidad total |
| **Agente puro** | OpenHands V1 | historial → mensajes → acción. Sin estado mutable en el agente |
| **EventLog append-only** | OpenHands V1 | JSONL auditable y replayable |
| **Anti-loop** | OpenHands | Stuck detector por observaciones/acciones repetidas |
| **Contexto acotado** | SWE-agent + OpenHands | Lectura paginada (100 líneas), salidas truncadas, historial condensado |

## 2. Tu hardware: veredicto honesto

| Recurso | Valor | Implicación |
|---|---|---|
| CPU | Ryzen 5 4600H (6C/12T, Zen 2) | ~4–8 tok/s con modelo 7–8B Q4 por CPU. Usable para agente paso a paso |
| RAM | 40 GB | Tu gran activo: caben modelos Q4 de hasta ~24B (lentos pero posibles) |
| VRAM | 4 GB | ⚠️ El 4600H lleva **Radeon Vega 6 integrada** (comparte RAM de sistema). ROCm para Vega 6 no es práctico → **inferencia 100% por CPU**. No pierdas tiempo con GPU offload |

**Regla de pulso:** ~1 GB de RAM por cada 1B de parámetros a Q4.

| Modelo | Tamaño Q4 | Velocidad estimada (tu CPU) | Uso recomendado |
|---|---|---|---|
| **Qwen3 8B** ⭐ | ~5.2 GB | 4–8 tok/s | Motor principal. Tool calling + modo thinking |
| Qwen2.5-Coder-7B | ~4.7 GB | 5–9 tok/s | Alternativa especializada en código |
| Devstral Small 24B | ~14.5 GB | 1.5–3 tok/s | Opcional: mejor en bucles agenticos, muy lento. Solo para corridas no interactivas |

> **Expectativa realista:** con el 8B resolverás bugs de 1–2 archivos en repos
> pequeños. Esto es para **aprender y validar la arquitectura**, no para
> competir con SWE-bench (eso requiere modelos frontera en la nube — y el
> cliente soporta ambos sin cambiar código).

---

## 3. Setup paso a paso (tu máquina exacta)

### 3.1 Instalar Ollama

```bash
# Linux
curl -fsSL https://ollama.com/install.sh | sh

# Windows (PowerShell admin)
winget install Ollama.Ollama
```

### 3.2 Descargar el modelo

```bash
ollama pull qwen3:8b        # ~5.2 GB de descarga
```

### 3.3 Crear el modelo afinado para agente (OBLIGATORIO)

El `num_ctx` por defecto de Ollama es muy bajo para un agente (necesitas cargar
archivos + salida de tests + historial). Usa el `Modelfile` incluido:

```bash
ollama create swe-qwen -f Modelfile
```

Contenido del Modelfile (ya incluido en este repo):

```
FROM qwen3:8b
PARAMETER num_ctx 32768      # mínimo aceptable: 16384
PARAMETER temperature 0      # determinismo para coding
PARAMETER top_p 0.9
PARAMETER num_thread 6       # cores FÍSICOS del 4600H (Zen 2: los SMT no ayudan a inferencia)
```

### 3.4 Verificar

```bash
ollama run swe-qwen "Di solo: OK"
# Debe responder al instante. Ctrl+D para salir.

# Test de velocidad real con contexto largo:
ollama run swe-qwen --verbose "Resume: $(seq 1 2000 | tr '\n' ' ')"
# Fíjate en 'eval rate': ese es tu tok/s real con carga de contexto
```

### 3.5 (Opcional) Devstral 24B para corridas lentas-but-mejor

```bash
ollama pull devstral           # 24B Q4_K_M, ~14.5 GB en RAM
ollama create swe-devstral -f Modelfile.devstral
```
Úsalo solo para evaluación no interactiva (`run_local.py --model swe-devstral`).
A 1.5–3 tok/s, un episodio de 10 pasos tarda 15–40 min.

---

## 4. Correr el agente

### 4.1 Test sin modelo (valida el núcleo en 5 segundos)

```bash
python test_core.py
# Debe mostrar: episodio completo con bug arreglado, git commits por paso,
# stuck detection, y recuperación de errores de formato.
```

### 4.2 Episodio real con el modelo local

```bash
# Terminal 1: servidor Ollama (si no está corriendo ya)
ollama serve

# Terminal 2:
python run_local.py --repo ./repo_de_prueba --task "Arregla la función para que pase los tests"
```

Salida esperada por paso:
```
[step 0] view src/app.py
[step 1] bash pytest -q            → exit=1 (1 failed)
[step 2] str_replace src/app.py    → editado (+2 -1)
[step 3] bash pytest -q            → exit=0
[step 4] submit                    → SUBMIT + diff
status: submitted | pasos: 5
```

Los eventos quedan en `logs/<timestamp>.jsonl` — replayables y auditables.

### 4.3 Modo híbrido (local + cloud en el mismo código)

Edita `run_local.py`:
```python
llm = OpenAICompatClient(
    model="swe-qwen",
    base_url="http://localhost:11434/v1",   # ← local
    api_key="ollama",
)
# o para cloud, sin tocar nada más:
# llm = OpenAICompatClient(model="gpt-4o-mini", api_key=os.environ["OPENAI_API_KEY"])
```

---

## 5. Hoja de ruta

| Fase | Qué | Estado |
|---|---|---|
| 0 | Análisis del núcleo + plan | ✅ Este repo |
| 1 | **Hardening: sandbox Docker intercambiable, tests del ACI** | 👉 HOY |
| 2 | Repo map con tree-sitter como acción `search` | Pendiente |
| 3 | Critic model (verificación del diff antes de submit) | Pendiente |
| 4 | Pipeline tipo Agentless para bugs acotados | Pendiente |
| 5 | CLI + evaluación en repos con tests + informe honesto | Pendiente |

## 6. Protocolo de evaluación (Fase 5)

1. Crear 3 repos de prueba con bug + tests (calc, parser, CLI simple).
2. Correr 3 episodios por modelo (swe-qwen, swe-devstral).
3. Medir: pasos hasta submit, stucks, tasa de éxito (tests en verde), minutos/episodio.
4. Documentar en `EVALUACION.md`: qué funcionó 100% local, qué no, y qué cambiarías con API frontera.

## 7. Troubleshooting

| Síntoma | Causa | Fix |
|---|---|---|
| Respuestas cortas / "contexto lleno" | `num_ctx` bajo | Recrea el modelo con el Modelfile (32768) |
| 2–3 tok/s | SMT/threads malos, modelo muy grande | `num_thread 6` (físicos); baja a 7B |
| El agente repite la misma acción | Tarea mal acotada o modelo débil | Es el stuck detector trabajando — revisa el log y reformula la tarea |
| `old_str aparece N veces` | ACI haciendo su trabajo | Pide al agente más contexto en `old` — es la lección #1 de SWE-agent |
| OOM con devstral 24B | Otras apps comiendo RAM | Cierra navegador/IDE; el Q4 necesita ~16 GB libres |

## 8. Licencia y créditos

MIT. Lecciones de diseño extraídas de OpenHands (MIT), SWE-agent (MIT),
Mini-SWE-Agent, Agentless y Aider (Apache-2.0). Núcleo original escrito para
este proyecto.
