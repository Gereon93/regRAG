"""Misst, wie oft das lokale Modell das Markerformat befolgt (ADR 0014). Braucht LLM und Index."""

import argparse
import os

import config
from evaluation.dataset import ANTWORTBAR


def _ergebnis(lauf):
    if lauf.get("llm_fehler"):
        return "LLM_FEHLER"
    return ",".join(r.value for r in lauf["entscheidung"].reasons) or "bestanden"


def main(argv=None):
    from agent import app

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datei", default=os.devnull, help="Eskalationsdatei mit den Gründen je Aussage")
    config.ESKALATION_DATEI = parser.parse_args(argv).datei

    bestanden = 0
    for frage in ANTWORTBAR:
        ergebnis = _ergebnis(app.invoke({"frage": frage}))
        bestanden += ergebnis == "bestanden"
        print(f"{ergebnis:20s} {frage[:60]}")
    print(f"\nBefolgung: {bestanden} von {len(ANTWORTBAR)}")


if __name__ == "__main__":
    main()
