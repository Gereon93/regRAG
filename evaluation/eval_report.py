"""Kalibriert Schwellen pro Segment auf dem Calibration-Split und berichtet auf dem Test-Split (Issue #31).

Aufruf: python -m evaluation.eval_report. Braucht den Index, läuft deshalb nicht in der CI.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import config
import gueltigkeit
from evaluation.evalset import (
    STANDARD_FAELLE,
    Kosten,
    _kosten,
    bericht,
    eval_version,
    hole_zeilen,
    lade_faelle,
    laufzeit_evidenz,
    schwellen_pro_segment,
    teile,
    top_score,
    vorab_verweigert,
)
from evaluation.kalibrierung import ece, fitte_temperatur
from gate import SEGMENTE, kalibriere, segment_von
from laya import JudgeFehler, LayaGate, SystemOneClient
from manifest import (
    Manifest,
    checkpoint_kennung,
    index_fingerprint,
    manifest_ziel,
    pruefe_ueberschreibbar,
    schreibe_manifest,
)
from retrieval import TOP_K, UeberabrufRetriever

ZIEL_FALLZAHL = 200
MANIFEST_VERZEICHNIS = Path(__file__).parent / "manifest"
LAYA_DEFINITION = (
    "Laya beurteilt, ob die gelieferte Evidenz die Antwort enthält; das Label y = 1 sagt, der Korpus beantworte "
    "die Frage (erwartet == answer). Verfehlt das Retrieval den richtigen Chunk, ist das Label für Laya falsch. "
    "Laya sieht wie im Laufzeitpfad nur die am Stichtag nutzbaren Fassungen und wird nur befragt, wenn Gültigkeit und "
    "Fassung nicht verweigern und deren Retrieval-Score >= MIN_RETRIEVAL_SCORE ist; verworfene Fälle zählen als "
    "Eskalation. Die Evidenz hängt vom Index-Stand (index_fingerprint) und vom Stichtag (stichtag) ab."
)


def einseitige_segmente(zeilen):
    """Segmente, deren Calibration-Fälle nur ein Label tragen: die Schwelle sagt dort nichts über Trennschärfe."""
    labels = {}
    for z in zeilen:
        labels.setdefault(z["segment"], set()).add(z["erwartet"])
    return [f"Segment '{s}': Calibration enthält nur '{next(iter(werte))}', Schwelle nicht belastbar." for s, werte in sorted(labels.items()) if len(werte) == 1]


def erzeuge_bericht(faelle, retriever, kosten, fehlende_segmente_warnen=False, stichtag=None):
    """fehlende_segmente_warnen: Test-Segmente ohne Calibration-Fälle warnen statt abbrechen (Laya-Lauf, #42).

    stichtag: Default heute in Europe/Berlin, einmal je Lauf bestimmt (#54).
    """
    stichtag = stichtag or gueltigkeit.heute_berlin()
    version = eval_version(faelle)
    splits = teile(faelle)
    leere = [name for name, fs in splits.items() if not fs]
    if leere:
        raise SystemExit(
            f"Leere Splits {leere}: zu wenige Fälle für eine Kalibrierung ({len(faelle)}, Ziel >= {ZIEL_FALLZAHL}). "
            "Mehr Fälle in evaluation/evalset/v1.jsonl labeln."
        )
    kalibrierung = hole_zeilen(splits["calibration"], retriever, stichtag)
    schwellen = schwellen_pro_segment(kalibrierung, kosten)
    test = hole_zeilen(splits["test"], retriever, stichtag)
    fehlend = sorted({z["segment"] for z in test} - set(schwellen))
    if fehlend and not fehlende_segmente_warnen:
        raise SystemExit(
            f"Segmente ohne Calibration-Fälle, keine Schwelle möglich: {fehlend}. "
            "Mehr Fälle dieser Segmente labeln oder die Segmente zusammenlegen."
        )
    fallback = max(schwellen.values()) if fehlend else None
    ergebnis = bericht(test, schwellen, kosten, version, fallback=fallback)
    ergebnis["split"] = "test"
    ergebnis["warnungen"] = [
        *(
            f"Segment '{s}': keine Calibration-Fälle, Test-Fälle mit der strengsten Schwelle {fallback} bewertet."
            for s in fehlend
        ),
        *einseitige_segmente(kalibrierung),
    ]
    ergebnis["faelle"] = {name: len(fs) for name, fs in splits.items()}
    ergebnis["stichtag"] = stichtag.isoformat()
    ergebnis["vorab_verweigert"] = {"calibration": vorab_verweigert(kalibrierung), "test": vorab_verweigert(test)}
    ergebnis["zeitpunkt"] = datetime.now(timezone.utc).isoformat()
    return ergebnis


def normalisiere_revision(revision):
    """Getrimmte Revision oder None; eine blanke Revision schreibt kein Manifest, das der Loader ablehnt."""
    return (revision or "").strip() or None


def hole_laya_zeilen(faelle, retriever, laya_gate, min_retrieval_score, stichtag=None):
    """Wie hole_zeilen, plus Laufzeitsegment und Laya-Rohscore (nur bei bestandener Vorprüfung, wie die Kaskade)."""
    stichtag = stichtag or gueltigkeit.heute_berlin()
    zeilen = []
    for fall in faelle:
        nodes, reason = laufzeit_evidenz(retriever.retrieve(fall["frage"]), stichtag)
        top = None if reason else top_score(nodes)
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
                "kategorie": fall["kategorie"],
                "erwartet": fall["erwartet"],
                "top": top,
                "vorpruefung": reason,
                "segment_laufzeit": segment_von(nodes),
                "laya_roh": roh,
            }
        )
    return zeilen


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


def _ohne_nur_answer_segmente(schwellen, zeilen):
    """Segmente, deren Calibration nur 'answer' kennt, verlieren ihre eigene Schwelle (#40).

    Die Kostenfunktion setzt sie dort auf den niedrigsten gesehenen Score, Laya wäre abgeschaltet. Solche Segmente
    fallen auf den Fallback; der bleibt das Maximum über alle Segmente, ist also nie lockerer als ihre eigene Schwelle.
    Nur-'abstain'-Segmente behalten ihre (strenge) Schwelle.
    """
    mit_abstain = {z["segment_laufzeit"] for z in zeilen if z["erwartet"] != "answer"}
    return {segment: schwelle for segment, schwelle in schwellen.items() if segment in mit_abstain}


def erzeuge_laya_bericht(faelle, retriever, laya_gate, kosten, checkpoint_revision, stichtag=None):
    checkpoint_revision = normalisiere_revision(checkpoint_revision)
    stichtag = stichtag or gueltigkeit.heute_berlin()
    retriever = _Zwischenspeicher(retriever)
    ergebnis = erzeuge_bericht(faelle, retriever, kosten, fehlende_segmente_warnen=True, stichtag=stichtag)
    splits = teile(faelle)
    guard = config.MIN_RETRIEVAL_SCORE
    kalibrierung = hole_laya_zeilen(splits["calibration"], retriever, laya_gate, guard, stichtag)
    test = hole_laya_zeilen(splits["test"], retriever, laya_gate, guard, stichtag)

    bewertet = [z for z in kalibrierung if z["laya_roh"] is not None]
    if not any(z["segment_laufzeit"] in SEGMENTE for z in bewertet):
        raise SystemExit(
            "Kein Calibration-Segment hat einen Laya-Score (alle Fälle unter dem Retrieval-Guard, von Gültigkeit oder "
            "Fassung verweigert oder ohne eindeutiges Segment). Mehr Calibration-Fälle labeln, REGRAG_MIN_SCORE oder "
            "die Gültigkeitsmetadaten prüfen."
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
    fingerprint = index_fingerprint(config.CHROMA_VERZEICHNIS)
    if fingerprint is None:
        warnungen.append("Kein Manifest: Index ohne fingerprint.json, Manifest wäre an keinen Index gebunden.")
    roh_schwellen = schwellen_pro_segment(
        _mit_kalibriert(bewertet, temperatur), kosten, score_feld="laya_kal", segment_feld="segment_laufzeit"
    )
    schwellen = _ohne_nur_answer_segmente(roh_schwellen, bewertet)
    if not schwellen:
        warnungen.append("Kein Manifest: alle Segmente einseitig (nur 'answer'), keine belastbare Schwelle.")
    fallback = max(roh_schwellen.values())
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
        "index_fingerprint": fingerprint,
        "definition": LAYA_DEFINITION,
        "segmente": laya_bericht["segmente"],
        "kategorien": laya_bericht["kategorien"],
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
    if checkpoint_revision and schaetzbar and fingerprint and schwellen:
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
            index_fingerprint=fingerprint,
        )
    return ergebnis, manifest


def _bericht_identitaet(daten):
    return daten["laya"]["checkpoint"]["modell"], daten["laya"]["checkpoint"]["revision"]


def schreibe(ergebnis, verzeichnis):
    ziel = Path(verzeichnis)
    ziel.mkdir(parents=True, exist_ok=True)
    name = f"bericht-{ergebnis['eval_version'].removeprefix('sha256:')[:12]}"
    identitaet = None
    if "laya" in ergebnis:
        identitaet = _bericht_identitaet(ergebnis)
        name += f"-laya-{checkpoint_kennung(*identitaet)}"
    datei = ziel / f"{name}.json"
    if identitaet:
        pruefe_ueberschreibbar(datei, identitaet, _bericht_identitaet)
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
    stichtag = gueltigkeit.heute_berlin()
    retriever = UeberabrufRetriever(index, TOP_K, config.UEBERABRUF_FAKTOR, uhr=lambda: stichtag)
    manifest = None
    if config.LAYA_URL:
        ergebnis, manifest = erzeuge_laya_bericht(
            faelle, retriever, baue_laya_gate(), kosten, args.checkpoint_revision, stichtag=stichtag
        )
        laya = ergebnis["laya"]
        if not normalisiere_revision(args.checkpoint_revision):
            print("Hinweis: ohne --checkpoint-revision wird kein Gate-Manifest geschrieben.", file=sys.stderr)
        if manifest:
            print(
                f"Laya: T={laya['temperatur']} Schwellen={laya['schwellen']} Fallback={laya['fallback_schwelle']}",
                file=sys.stderr,
            )
        for warnung in ergebnis["warnungen"]:
            print(f"WARNUNG: {warnung}", file=sys.stderr)
        for warnung in laya["warnungen"]:
            print(f"WARNUNG (Laya): {warnung}", file=sys.stderr)
    else:
        ergebnis = erzeuge_bericht(faelle, retriever, kosten, stichtag=stichtag)
    if manifest:
        manifest_ziel(manifest, MANIFEST_VERZEICHNIS)
    datei = schreibe(ergebnis, config.EVAL_BERICHT_VERZEICHNIS)
    print(json.dumps({"eval_version": ergebnis["eval_version"], "schwellen": ergebnis["schwellen"]}, indent=2))
    print(f"Bericht: {datei}")
    if manifest:
        print(f"Manifest: {schreibe_manifest(manifest, MANIFEST_VERZEICHNIS)}")


if __name__ == "__main__":
    main()
