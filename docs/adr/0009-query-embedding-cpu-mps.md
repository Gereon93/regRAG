# 0009 — Query-Embedding im CPU-Container

Status: akzeptiert (2026-09-30)

## Kontext und Messung

Jede Anfrage embeddet ihre Frage mit `BAAI/bge-m3` (1024 Dimensionen). Gemessen wurden dieselben acht DORA-Fragen aus `evaluation.dataset.ANTWORTBAR` plus eine synthetische, achtfach längere Frage; je Frage fünf Warm-ups und 20 Zeitmessungen. Die Tabelle fasst 180 Query-Aufrufe pro Lauf zusammen; p95 ist nach Nearest-Rank (`ceil(0.95 * N)`) berechnet. Modellladen ist separat erfasst und nicht in der Query-Latenz enthalten. Das lokale M4-System hat 10 CPU-Kerne und 32 GB RAM. Docker Desktop meldete Linux/aarch64, 10 CPUs und 8,3 GB RAM; MPS und CUDA waren im Container nicht verfügbar.

| Umgebung | Threads | Median | p95 | Läufe |
|---|---:|---:|---:|---|
| macOS 27, M4 MPS, Python 3.14.7, Torch 2.13.0 | 4 | 28 ms | 52 ms | 1 |
| Docker CPU, Python 3.12.14, Torch 2.14.0+cu130 | 10 (Default) | 226–241 ms | 581–669 ms | 2 |
| Docker CPU, Python 3.12.14, Torch 2.14.0+cu130 | 8 (`OMP_NUM_THREADS=8`) | 179–180 ms | 502–526 ms | 2 |

In beiden Vergleichsläufen waren Median und p95 mit acht Threads niedriger: der Median um 20–26 %, p95 um 14–21 %. Rohmessungen: [MPS](0009-query-embedding-mps.json), [CPU Default 1](0009-query-embedding-cpu-default.json), [CPU Default 2](0009-query-embedding-cpu-default-repeat.json), [CPU OMP 8 1](0009-query-embedding-cpu-omp-8.json) und [CPU OMP 8 2](0009-query-embedding-cpu-omp-8-repeat.json). Benchmark: `python -m evaluation.embedding_latency --device cpu` (Container), `--device mps` (lokal); CPU mit `OMP_NUM_THREADS=8` starten.

Die MPS-Messung bleibt deutlich schneller; sie belegt aber keinen Vorteil eines anderen Modells oder einer separaten Laufzeit.

## Entscheidung

`BAAI/bge-m3` und die In-Process-Torch-Inferenz bleiben bestehen; Docker setzt `OMP_NUM_THREADS` standardmäßig auf 8, per Compose-Umgebungsvariable überschreibbar. Modell, Gewichte und Dimension bleiben gleich; ein identischer CPU-Fragetext lieferte bei 10 und 8 Threads denselben FP32-Vektor (maximale absolute Differenz 0), daher ist kein Index-Neuaufbau erforderlich. Der Container verwendet denselben Hugging-Face-Cache wie der Build und lädt das Modell im Betrieb strikt offline.

## Bewertete Alternativen und Folgen

- **Kleineres Modell:** `BAAI/bge-small-en-v1.5` ist als englischsprachig ausgewiesen und daher kein vertretbarer direkter Ersatz für den deutschen DORA-Korpus. Jeder Modellwechsel macht die persistierten Vektoren wertlos; `dokumente.diff()` erzwingt dann einen Voll-Rebuild und die Retrieval-Schwelle muss neu kalibriert werden ([ADR 0006](0006-inkrementeller-index-merge-statt-voll-rebuild.md)).
- **ONNX/INT8:** Die Änderung der Threadzahl ändert Modell und Gewichte nicht; beim Referenzprompt waren die Vektoren bitgleich. ONNX-Export und Quantisierung wurden nicht prototypisiert; ONNX Runtime weist auf mögliche Genauigkeitseinbußen durch Quantisierung hin. Das wäre erst nach Retrieval-Evaluation auf dem deutschen Korpus eine tragfähige Änderung ([ONNX Runtime: Quantisierung](https://onnxruntime.ai/docs/performance/model-optimizations/quantization.html)).
- **Eigener Embedding-Service:** Nicht gemessen; würde Netzwerk-Hop und zusätzlichen Betriebsaufwand einführen, ohne den einzelnen CPU-Embedding-Aufruf nachweislich zu beschleunigen.
- **Imagegröße:** Das gebaute Image ist 17,7 GB groß; enthalten ist `torch 2.14.0+cu130`, obwohl CUDA im Container nicht verfügbar ist. Ein CPU-only-ARM64-PyTorch-Wheel für Python 3.12 wurde im offiziellen CPU-Wheel-Index nicht gefunden; ein Versionsrücksprung oder eine Laufzeitmigration ist daher nicht Teil dieser Entscheidung ([PyTorch CPU-Wheel-Index](https://download.pytorch.org/whl/cpu/torch/)). Die Imagegröße bleibt ein separater Optimierungsbedarf.
