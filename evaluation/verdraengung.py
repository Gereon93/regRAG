"""Verdrängung durch abgelöste Fassungen messen (Issue #53, ADR 0015).

Aufruf: python -m evaluation.verdraengung [--faktor N] [--stichtag YYYY-MM-DD] [--fragen DATEI]

Je Frage ein Kandidatenabruf mit `top_k * faktor`: "vorher" ist dessen Top-k, "nachher" die Auswahl von
`gueltigkeit.waehle_treffer`. Beide Seiten sehen also dieselben Kandidaten. Ein zweiter Abruf mit `top_k` misst nur
die Latenz (Reihenfolge wechselt je Frage, beide enthalten das Query-Embedding) und meldet, ob sein Top-k abweicht.
Der Gate ist `baue_gate` ohne Laya, damit die Messung deterministisch ist und am übergebenen Stichtag läuft.
Ohne datierte Fassungen im Index bleibt `verdraengt` bei 0.
"""

import argparse
import json
import statistics
import time
from collections import Counter
from pathlib import Path

import config
import gueltigkeit
from evaluation.embedding_latency import nearest_rank_p95
from evaluation.evalset import STANDARD_FAELLE, lade_faelle
from gate import FassungsGate, GueltigkeitsGate, Kaskade, RetrievalScoreGate

TOP_K_WIE_AGENT = 3
FLAGS = ("verdraengt", "gueltige_unter_kandidaten", "gueltige_in_auswahl", "top_k_abweichend")


def baue_messgate(min_score, stichtag):
    """Reihenfolge wie `laya.baue_gate` ohne Laya: Score, Gültigkeit, Score, Fassung (ADR 0015)."""
    score = RetrievalScoreGate(min_score)
    return Kaskade([score, GueltigkeitsGate(uhr=lambda: stichtag), score, FassungsGate()])


def _status(nodes, stichtag):
    return [gueltigkeit.status_von(n.metadata, stichtag) for n in nodes]


def _seite(gate, frage, nodes, stichtag):
    entscheidung = gate.entscheide(frage, nodes)
    nutzbar = [n.score for n, s in zip(nodes, _status(nodes, stichtag), strict=True) if s in gueltigkeit.NUTZBAR]
    return {"reasons": [r.value for r in entscheidung.reasons], "bester_nutzbarer_score": max(nutzbar, default=None)}


def _abrufe(nummer, frage, retriever, zeit):
    reihenfolge = list(retriever.items())
    if nummer % 2:
        reihenfolge.reverse()
    treffer, dauer = {}, {}
    for name, r in reihenfolge:
        start = zeit()
        treffer[name] = r.retrieve(frage)
        dauer[name] = zeit() - start
    return treffer, dauer


def _zeile(frage, treffer, dauer, top_k, gate, stichtag):
    kandidaten = treffer["kandidaten"]
    vorher = kandidaten[:top_k]
    nachher = gueltigkeit.waehle_treffer(kandidaten, top_k, stichtag)
    status_vorher = _status(vorher, stichtag)
    verdraengt = gueltigkeit.GUELTIG not in status_vorher and any(
        s not in gueltigkeit.NUTZBAR for s in status_vorher
    )
    return {
        "frage": frage,
        "top_score": kandidaten[0].score if kandidaten else None,
        "verdraengt": verdraengt,
        "gueltige_unter_kandidaten": verdraengt and gueltigkeit.GUELTIG in _status(kandidaten, stichtag),
        "gueltige_in_auswahl": verdraengt and gueltigkeit.GUELTIG in _status(nachher, stichtag),
        "top_k_abweichend": [n.text for n in treffer["top_k"]] != [n.text for n in vorher],
        "vorher": _seite(gate, frage, vorher, stichtag),
        "nachher": _seite(gate, frage, nachher, stichtag),
        "latenz_sekunden": dauer,
    }


def _verteilung(werte):
    if not werte:
        return None
    return {"median": statistics.median(werte), "p95": nearest_rank_p95(sorted(werte))}


def _entscheidung(seite):
    return "+".join(seite["reasons"]) or "ANSWER"


def _fasse_zusammen(zeilen, top_k, stichtag):
    latenz = {name: [z["latenz_sekunden"][name] for z in zeilen] for name in ("top_k", "kandidaten")}
    differenz = [k - t for k, t in zip(latenz["kandidaten"], latenz["top_k"], strict=True)]
    return {
        "n": len(zeilen),
        "top_k": top_k,
        "stichtag": stichtag.isoformat(),
        **{flag: sum(z[flag] for z in zeilen) for flag in FLAGS},
        "wechsel": dict(
            Counter(
                f"{_entscheidung(z['vorher'])} -> {_entscheidung(z['nachher'])}"
                for z in zeilen
                if z["vorher"]["reasons"] != z["nachher"]["reasons"]
            )
        ),
        "latenz_sekunden": {
            "top_k": _verteilung(latenz["top_k"]),
            "kandidaten": _verteilung(latenz["kandidaten"]),
            "differenz_median": statistics.median(differenz) if differenz else None,
        },
    }


def miss(fragen, retriever_top_k, retriever_kandidaten, top_k, gate, stichtag, zeit=time.perf_counter):
    retriever = {"top_k": retriever_top_k, "kandidaten": retriever_kandidaten}
    zeilen = [
        _zeile(frage, *_abrufe(nummer, frage, retriever, zeit), top_k, gate, stichtag)
        for nummer, frage in enumerate(fragen)
    ]
    return {"zusammenfassung": _fasse_zusammen(zeilen, top_k, stichtag), "zeilen": zeilen}


def _lies_fragen(pfad):
    return [zeile.strip() for zeile in Path(pfad).read_text(encoding="utf-8").splitlines() if zeile.strip()]


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m evaluation.verdraengung")
    parser.add_argument("--faktor", type=int, default=config.UEBERABRUF_FAKTOR, help="Überabruf-Faktor (>= 1).")
    parser.add_argument(
        "--stichtag", type=lambda wert: gueltigkeit.parse_datum(wert, "--stichtag"), help="YYYY-MM-DD, sonst heute."
    )
    parser.add_argument("--fragen", help="Eine Frage je Zeile; sonst alle Fragen des Eval-Sets.")
    args = parser.parse_args(argv)
    if args.faktor < 1:
        parser.error("--faktor muss mindestens 1 sein.")

    from rag import index

    stichtag = args.stichtag or gueltigkeit.heute_berlin()
    fragen = _lies_fragen(args.fragen) if args.fragen else [f["frage"] for f in lade_faelle(STANDARD_FAELLE)]
    bericht = miss(
        fragen,
        index.as_retriever(similarity_top_k=TOP_K_WIE_AGENT),
        index.as_retriever(similarity_top_k=TOP_K_WIE_AGENT * args.faktor),
        TOP_K_WIE_AGENT,
        baue_messgate(config.MIN_RETRIEVAL_SCORE, stichtag),
        stichtag,
    )
    bericht["zusammenfassung"]["faktor"] = args.faktor
    print(json.dumps(bericht, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
