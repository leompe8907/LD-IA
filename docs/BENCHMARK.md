# Benchmark de Ollama en esta máquina

- Modelo: `qwen3:8b` (Q4_K_M, 4.9 GB) · Ollama 0.34.4 · `num_ctx` 16384 · `think: false`
- Hardware: Ryzen 5 4600H (6C/12T), 40 GB de RAM, GTX 1650 de 4 GB · Windows 11
- Fecha: 2026-09-27 · script: `bench/speed.py` (prompt de código Python sintético, 64 tokens
  de respuesta)

| Modo | Reparto | Prompt (tok) | Lectura del prompt (s) | Lectura (tok/s) | Generación (tok/s) | Mismo prefijo, con caché (s) |
|---|---|---|---|---|---|---|
| auto | 30% GPU (2.2/7.3 GiB) | 2425 | 36.4 | 66.6 | 3.6 | 2.6 |
| auto | 30% GPU | 7168 | 128.0 | 56.0 | 1.7 | 6.4 |
| auto | 30% GPU | 14344 | 310.1 | 46.3 | 1.1 | 8.7 |
| solo CPU (`num_gpu=0`) | 0% GPU | 2425 | 74.4 | 32.6 | 3.9 | 2.1 |
| solo CPU | 0% GPU | 7168 | 257.5 | 27.8 | 1.8 | 5.4 |
| solo CPU | 0% GPU | 14344 | 700.1 | 20.5 | 1.1 | 9.8 |

Los 7.3 GiB incluyen la caché KV para 16k de contexto; en la GPU entran ~2.2 GiB (un 30%).
Con caché, Ollama igual informa todos los tokens en `prompt_eval_count`; lo que muestra la
reutilización es el **tiempo**.

## Qué significa para el agente

1. **La GPU duplica la lectura del prompt** (46–67 tok/s contra 20–33 en CPU; a 14k tokens,
   310 s contra 700 s). En generación no aporta: el cuello de botella es la memoria de la CPU.
   → Se usa el reparto automático de Ollama; `num_gpu` queda solo como override.
2. **La generación cae con el largo del contexto**: 3.6–3.9 tok/s con 2.4k tokens, 1.7 con 7k,
   1.1 con 14k. Una respuesta de ~100 tokens cuesta ~30 s con contexto chico y ~90 s con 14k.
   → Presupuesto del prompt bajado de 12k a **8k** tokens, `num_ctx` por defecto 12288.
3. **La caché KV es la mayor palanca**: releer el mismo prefijo de 14k cuesta 9 s en vez de
   310 s. Un paso que rompe el prefijo paga la lectura entera; uno que solo agrega al final
   paga segundos. → Confirma la condensación de a bloques (ADR-007). Con el presupuesto de 8k,
   una condensación cuesta ~2–3 min extra, una vez cada K pasos.
4. **El código tokeniza más denso de lo supuesto**: ~2.5 caracteres por token (el estimador
   arrancaba en 3.0 y subestimaba). → Valor inicial 2.5; se recalibra con los tokens reales.
5. **`think` tiene que ir apagado por defecto**: 500 tokens de razonamiento a 2–3 tok/s son
   3–4 minutos por paso.

Estimación por paso con el prefijo en caché y contexto de 4–8k: **~30–90 s**. Un episodio de
10 pasos: **~10–15 min**, más ~2–3 min por cada condensación. Los tiempos reales por episodio
se miden en la evaluación (Fase 5).
