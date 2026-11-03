"""Misst, wie oft das lokale Modell das Markerformat befolgt (ADR 0014). Braucht LLM und Index."""

import argparse
import os

import config
from agent import app
from evaluation.dataset import ANTWORTBAR


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datei", default=os.devnull, help="Eskalationsdatei mit den Gründen je Aussage")
    config.ESKALATION_DATEI = parser.parse_args().datei

    bestanden = 0
    for frage in ANTWORTBAR:
        entscheidung = app.invoke({"frage": frage})["entscheidung"]
        bestanden += not entscheidung.verweigert
        ergebnis = ",".join(r.value for r in entscheidung.reasons) or "bestanden"
        print(f"{ergebnis:20s} {frage[:60]}")
    print(f"\nBefolgung: {bestanden} von {len(ANTWORTBAR)}")


if __name__ == "__main__":
    main()
