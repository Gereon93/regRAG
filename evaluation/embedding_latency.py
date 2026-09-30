import argparse
import json
import math
import os
import platform
import statistics
import time
from pathlib import Path

import torch

from embedding import create_embed_model
from evaluation.dataset import ANTWORTBAR


def messen(device, threads, warmup, repetitions):
    if threads is not None:
        torch.set_num_threads(threads)
    load_start = time.perf_counter()
    model = create_embed_model(device=device)
    synchronize = torch.mps.synchronize if device == "mps" else lambda: None
    synchronize()
    load_seconds = time.perf_counter() - load_start
    queries = ANTWORTBAR + [ANTWORTBAR[0] + (" " + ANTWORTBAR[1]) * 8]
    for frage in queries:
        for _ in range(warmup):
            model.get_query_embedding(frage)
    samples = []
    for frage in queries:
        durations = []
        for _ in range(repetitions):
            synchronize()
            start = time.perf_counter()
            vector = model.get_query_embedding(frage)
            synchronize()
            durations.append(time.perf_counter() - start)
            dimension = model._model.get_embedding_dimension()
            if (
                len(vector) != dimension
                or not all(math.isfinite(component) for component in vector)
                or not math.isclose(
                    math.sqrt(sum(component * component for component in vector)),
                    1.0,
                    rel_tol=1e-4,
                )
            ):
                raise RuntimeError("Embedding-Dimension, Endlichkeit oder Norm unerwartet")
        samples.append({"query": frage, "seconds": durations})
    values = sorted(value for sample in samples for value in sample["seconds"])
    return {
        "dimension": model._model.get_embedding_dimension(),
        "device": str(model._device),
        "host": platform.platform(),
        "cpu_count": os.cpu_count(),
        "omp_num_threads": os.getenv("OMP_NUM_THREADS"),
        "mkl_num_threads": os.getenv("MKL_NUM_THREADS"),
        "torch_threads": torch.get_num_threads(),
        "torch_interop_threads": torch.get_num_interop_threads(),
        "python": platform.python_version(),
        "torch": torch.__version__,
        "model": model.model_name,
        "model_load_seconds": load_seconds,
        "warmup": warmup,
        "repetitions": repetitions,
        "query_count": len(queries),
        "samples": samples,
        "summary_seconds": {
            "median": statistics.median(values),
            "p95": values[min(len(values) - 1, int(len(values) * 0.95))],
            "min": values[0],
            "max": values[-1],
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", choices=("cpu", "mps"), required=True)
    parser.add_argument("--threads", type=int)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--repetitions", type=int, default=20)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = messen(args.device, args.threads, args.warmup, args.repetitions)
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(serialized + "\n")
    print(serialized)


if __name__ == "__main__":
    main()
