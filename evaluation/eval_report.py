"""Kalibriert Schwellen pro Segment auf dem Calibration-Split und berichtet auf dem Test-Split (Issue #31).

Aufruf: python -m evaluation.eval_report. Braucht den Index, läuft deshalb nicht in der CI.
"""

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import config
from evaluation.evalset import (
    STANDARD_FAELLE,
    Kosten,
    _kosten,
    bericht,
    eval_version,
    lade_faelle,
    schwellen_pro_segment,
    teile,
)
from evaluation.kalibrierung import ece, fitte_temperatur
from gate import SEGMENTE, kalibriere, segment_von
from laya import JudgeFehler, LayaGate, SystemOneClient
from manifest import Manifest, schreibe_manifest

ZIEL_FALLZAHL = 200
MANIFEST_VERZEICHNIS = Path(__file__).parent / "manifest"
LAYA_DEFINITION = (
    "Laya beurteilt, ob die gelieferte Evidenz die Antwort enthält; das Label y = 1 sagt, der Korpus beantworte "
    "die Frage (erwartet == answer). Verfehlt das Retrieval den richtigen Chunk, ist das Label für Laya falsch. "
    "Laya wird nur bei Retrieval-Score >= MIN_RETRIEVAL_SCORE befragt; verworfene Fälle zählen als Eskalation. "
    "Die Evidenz hängt vom Index-Stand ab (index_fingerprint)."
)


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


def normalisiere_revision(revision):
    """Getrimmte Revision oder None; eine blanke Revision schreibt kein Manifest, das der Loader ablehnt."""
    return (revision or "").strip() or None


def hole_laya_zeilen(faelle, retriever, laya_gate, min_retrieval_score):
    """Wie hole_zeilen, plus Laufzeitsegment und Laya-Rohscore (nur bei bestandenem Guard, wie die Kaskade)."""
    zeilen = []
    for fall in faelle:
        nodes = retriever.retrieve(fall["frage"])
        top = max((n.score for n in nodes), default=None)
        roh = None
        if top is not None and top >= min_retrieval_score:
            try:
                roh = laya_gate.answerable_roh(fall["frage"], nodes)
            except JudgeFehler:
                raise SystemExit(
                    f"Laya-Aufruf für Fall {fall['id']!r} fehlgeschlagen oder fremder Checkpoint "
                    f"(erwartet {laya_gate.modell!r}). Bericht mit Lücken wird nicht geschrieben."
                ) from None
        zeilen.append(
            {
                "id": fall["id"],
                "segment": fall["segment"],
                "gruppe": fall["gruppe"],
                "erwartet": fall["erwartet"],
                "top": top,
                "segment_laufzeit": segment_von(nodes),
                "laya_roh": roh,
            }
        )
    return zeilen


def index_fingerprint():
    datei = Path(config.CHROMA_VERZEICHNIS) / "fingerprint.json"
    return hashlib.sha256(datei.read_bytes()).hexdigest() if datei.is_file() else None


def _ece_bericht(zeilen, temperatur):
    bewertet = [z for z in zeilen if z["laya_roh"] is not None]
    if not bewertet:
        return None
    y = [int(z["erwartet"] == "answer") for z in bewertet]
    roh = [z["laya_roh"] for z in bewertet]
    return {"vorher": ece(roh, y), "nachher": ece([kalibriere(p, temperatur) for p in roh], y)}


def _abweichende_segmente(zeilen):
    return [
        f"Fall {z['id']!r}: Eval-Segment '{z['segment']}' weicht vom Laufzeitsegment '{z['segment_laufzeit']}' ab."
        for z in zeilen
        if z["erwartet"] == "answer" and z["segment"] != z["segment_laufzeit"]
    ]


def _mit_kalibriert(zeilen, temperatur):
    return [
        {**z, "laya_kal": None if z["laya_roh"] is None else kalibriere(z["laya_roh"], temperatur)} for z in zeilen
    ]


class _Zwischenspeicher:
    """Ruft den Retriever pro Frage nur einmal auf: #31-Report und Laya-Zeilen sehen dieselben Nodes."""

    def __init__(self, retriever):
        self._retriever = retriever
        self._treffer = {}

    def retrieve(self, frage):
        if frage not in self._treffer:
            self._treffer[frage] = self._retriever.retrieve(frage)
        return self._treffer[frage]


def erzeuge_laya_bericht(faelle, retriever, laya_gate, kosten, checkpoint_revision):
    checkpoint_revision = normalisiere_revision(checkpoint_revision)
    retriever = _Zwischenspeicher(retriever)
    ergebnis = erzeuge_bericht(faelle, retriever, kosten)
    splits = teile(faelle)
    guard = config.MIN_RETRIEVAL_SCORE
    kalibrierung = hole_laya_zeilen(splits["calibration"], retriever, laya_gate, guard)
    test = hole_laya_zeilen(splits["test"], retriever, laya_gate, guard)

    bewertet = [z for z in kalibrierung if z["laya_roh"] is not None]
    if not any(z["segment_laufzeit"] in SEGMENTE for z in bewertet):
        raise SystemExit(
            "Kein Calibration-Segment hat einen Laya-Score (alle Fälle unter dem Retrieval-Guard oder "
            "ohne eindeutiges Segment). Mehr Calibration-Fälle labeln oder REGRAG_MIN_SCORE prüfen."
        )
    labels = [int(z["erwartet"] == "answer") for z in bewertet]
    temperatur, warnungen = fitte_temperatur([z["laya_roh"] for z in bewertet], labels)
    schaetzbar = len(set(labels)) == 2
    warnungen = [
        *warnungen,
        *einseitige_segmente(
            [{**z, "segment": z["segment_laufzeit"]} for z in bewertet if z["segment_laufzeit"] in SEGMENTE]
        ),
    ]
    if not schaetzbar:
        warnungen.append("Kein Manifest: Temperatur nicht schätzbar, T = 1.0 ist nicht einsetzbar.")
    schwellen = schwellen_pro_segment(
        _mit_kalibriert(bewertet, temperatur), kosten, score_feld="laya_kal", segment_feld="segment_laufzeit"
    )
    fallback = max(schwellen.values())
    test_kal = _mit_kalibriert(test, temperatur)
    laya_bericht = bericht(
        test_kal,
        schwellen,
        kosten,
        ergebnis["eval_version"],
        score_feld="laya_kal",
        segment_feld="segment_laufzeit",
        fallback=fallback,
    )
    ergebnis["min_retrieval_score"] = guard
    ergebnis["laya"] = {
        "temperatur": temperatur,
        "warnungen": [*warnungen, *_abweichende_segmente(kalibrierung + test)],
        "schwellen": schwellen,
        "fallback_schwelle": fallback,
        "ece": _ece_bericht(test, temperatur),
        "checkpoint": {"modell": laya_gate.modell, "revision": checkpoint_revision},
        "index_fingerprint": index_fingerprint(),
        "definition": LAYA_DEFINITION,
        "segmente": laya_bericht["segmente"],
        "gesamt": laya_bericht["gesamt"],
        "test_faelle": [
            {
                "id": z["id"],
                "gruppe": z["gruppe"],
                "kosten": _kosten(
                    [z], schwellen.get(z["segment_laufzeit"], fallback), kosten, "laya_kal"
                ),
            }
            for z in test_kal
        ],
    }
    manifest = None
    if checkpoint_revision and schaetzbar:
        manifest = Manifest(
            checkpoint_modell=laya_gate.modell,
            checkpoint_revision=checkpoint_revision,
            eval_version=ergebnis["eval_version"],
            min_retrieval_score=guard,
            temperatur=temperatur,
            schwellen=schwellen,
            fallback_schwelle=fallback,
            kosten={"falschantwort": kosten.falschantwort, "eskalation": kosten.eskalation},
            erzeugt=ergebnis["zeitpunkt"],
        )
    return ergebnis, manifest


def schreibe(ergebnis, verzeichnis):
    ziel = Path(verzeichnis)
    ziel.mkdir(parents=True, exist_ok=True)
    name = f"bericht-{ergebnis['eval_version'].removeprefix('sha256:')[:12]}"
    if "laya" in ergebnis:
        revision = ergebnis["laya"]["checkpoint"]["revision"]
        name += "-laya" + (f"-{re.sub(r'[^A-Za-z0-9._-]', '_', revision)[:40]}" if revision else "")
    datei = ziel / f"{name}.json"
    datei.write_text(json.dumps(ergebnis, ensure_ascii=False, indent=2), encoding="utf-8")
    return datei


def baue_laya_gate():
    client = SystemOneClient(config.LAYA_URL, config.LAYA_API_KEY, config.LAYA_TIMEOUT)
    return LayaGate(
        client,
        lambda segment: config.LAYA_MIN_ANSWERABLE,
        max_len=config.LAYA_MAX_LEN,
        lang=config.LAYA_LANG,
        modell=config.LAYA_MODELL,
        modell_streng=True,
    )


def main(argv=None):
    from rag import index

    parser = argparse.ArgumentParser(prog="python -m evaluation.eval_report")
    parser.add_argument("--checkpoint-revision", help="Revision des Laya-Checkpoints; schreibt das Gate-Manifest.")
    args = parser.parse_args(argv)

    faelle = lade_faelle(STANDARD_FAELLE)
    if len(faelle) < ZIEL_FALLZAHL:
        print(f"WARNUNG: {len(faelle)} Fälle, Ziel sind >= {ZIEL_FALLZAHL}. Werte sind nicht belastbar.", file=sys.stderr)
    kosten = Kosten(config.KOSTEN_FALSCHANTWORT, config.KOSTEN_ESKALATION)
    retriever = index.as_retriever(similarity_top_k=3)
    manifest = None
    if config.LAYA_URL:
        ergebnis, manifest = erzeuge_laya_bericht(faelle, retriever, baue_laya_gate(), kosten, args.checkpoint_revision)
        laya = ergebnis["laya"]
        if not normalisiere_revision(args.checkpoint_revision):
            print("Hinweis: ohne --checkpoint-revision wird kein Gate-Manifest geschrieben.", file=sys.stderr)
        if manifest:
            print(
                f"Laya: T={laya['temperatur']} Schwellen={laya['schwellen']} Fallback={laya['fallback_schwelle']}",
                file=sys.stderr,
            )
        for warnung in laya["warnungen"]:
            print(f"WARNUNG (Laya): {warnung}", file=sys.stderr)
    else:
        ergebnis = erzeuge_bericht(faelle, retriever, kosten)
    datei = schreibe(ergebnis, config.EVAL_BERICHT_VERZEICHNIS)
    print(json.dumps({"eval_version": ergebnis["eval_version"], "schwellen": ergebnis["schwellen"]}, indent=2))
    print(f"Bericht: {datei}")
    if manifest:
        print(f"Manifest: {schreibe_manifest(manifest, MANIFEST_VERZEICHNIS)}")


if __name__ == "__main__":
    main()
