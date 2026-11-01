"""Kalibriert Schwellen pro Segment auf dem Calibration-Split und berichtet auf dem Test-Split (Issue #31).

Aufruf: python -m evaluation.eval_report. Braucht den Index, läuft deshalb nicht in der CI.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import config
from evaluation.evalset import (
    STANDARD_FAELLE,
    Kosten,
    bericht,
    eval_version,
    lade_faelle,
    schwellen_pro_segment,
    teile,
)

ZIEL_FALLZAHL = 200


def hole_zeilen(faelle, retriever):
    zeilen = []
    for fall in faelle:
        nodes = retriever.retrieve(fall["frage"])
        top = max((n.score for n in nodes), default=None)
        zeilen.append({"id": fall["id"], "segment": fall["segment"], "erwartet": fall["erwartet"], "top": top})
    return zeilen


def einseitige_segmente(zeilen):
    """Segmente, deren Calibration-Fälle nur ein Label tragen: die Schwelle sagt dort nichts über Trennschärfe."""
    labels = {}
    for z in zeilen:
        labels.setdefault(z["segment"], set()).add(z["erwartet"])
    return [f"Segment '{s}': Calibration enthält nur '{next(iter(werte))}', Schwelle nicht belastbar." for s, werte in sorted(labels.items()) if len(werte) == 1]


def erzeuge_bericht(faelle, retriever, kosten):
    version = eval_version(faelle)
    splits = teile(faelle)
    leere = [name for name, fs in splits.items() if not fs]
    if leere:
        raise SystemExit(
            f"Leere Splits {leere}: zu wenige Fälle für eine Kalibrierung ({len(faelle)}, Ziel >= {ZIEL_FALLZAHL}). "
            "Mehr Fälle in evaluation/evalset/v1.jsonl labeln."
        )
    kalibrierung = hole_zeilen(splits["calibration"], retriever)
    schwellen = schwellen_pro_segment(kalibrierung, kosten)
    test = hole_zeilen(splits["test"], retriever)
    fehlend = sorted({z["segment"] for z in test} - set(schwellen))
    if fehlend:
        raise SystemExit(
            f"Segmente ohne Calibration-Fälle, keine Schwelle möglich: {fehlend}. "
            "Mehr Fälle dieser Segmente labeln oder die Segmente zusammenlegen."
        )
    ergebnis = bericht(test, schwellen, kosten, version)
    ergebnis["split"] = "test"
    ergebnis["warnungen"] = einseitige_segmente(kalibrierung)
    ergebnis["faelle"] = {name: len(fs) for name, fs in splits.items()}
    ergebnis["zeitpunkt"] = datetime.now(timezone.utc).isoformat()
    return ergebnis


def schreibe(ergebnis, verzeichnis):
    ziel = Path(verzeichnis)
    ziel.mkdir(parents=True, exist_ok=True)
    datei = ziel / f"bericht-{ergebnis['eval_version'].removeprefix('sha256:')[:12]}.json"
    datei.write_text(json.dumps(ergebnis, ensure_ascii=False, indent=2), encoding="utf-8")
    return datei


def main():
    from rag import index

    faelle = lade_faelle(STANDARD_FAELLE)
    if len(faelle) < ZIEL_FALLZAHL:
        print(f"WARNUNG: {len(faelle)} Fälle, Ziel sind >= {ZIEL_FALLZAHL}. Werte sind nicht belastbar.", file=sys.stderr)
    kosten = Kosten(config.KOSTEN_FALSCHANTWORT, config.KOSTEN_ESKALATION)
    ergebnis = erzeuge_bericht(faelle, index.as_retriever(similarity_top_k=3), kosten)
    datei = schreibe(ergebnis, config.EVAL_BERICHT_VERZEICHNIS)
    print(json.dumps({"eval_version": ergebnis["eval_version"], "schwellen": ergebnis["schwellen"]}, indent=2))
    print(f"Bericht: {datei}")


if __name__ == "__main__":
    main()
