"""Vergleicht zwei Laya-Reports per gepaartem Cluster-Bootstrap (Issue #32).

Aufruf: python -m evaluation.vergleich <baseline.json> <kandidat.json>
"""

import argparse
import json
from pathlib import Path

from evaluation.kalibrierung import vergleiche


def _lade(pfad):
    report = json.loads(Path(pfad).read_text(encoding="utf-8"))
    return {**report, "test_faelle": report.get("test_faelle", report.get("laya", {}).get("test_faelle"))}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m evaluation.vergleich")
    parser.add_argument("baseline")
    parser.add_argument("kandidat")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    try:
        ergebnis = vergleiche(_lade(args.baseline), _lade(args.kandidat), seed=args.seed)
    except (ValueError, KeyError, TypeError) as fehler:
        raise SystemExit(f"Vergleich nicht möglich: {fehler}") from fehler
    print(json.dumps(ergebnis, indent=2))


if __name__ == "__main__":
    main()
