"""Score-Trennung über das ganze Eval-Set: Diagnose für Korpus- oder Index-Vergleiche (ADR 0001, 0005).

Aufruf: python -m evaluation.calibrate. Wählt keine Schwelle für den Betrieb; dafür gibt es
python -m evaluation.eval_report (Calibration/Test-Split, ADR 0012). Die Schwelle hier minimiert dieselbe
Kostenfunktion, aber über alle Fälle ohne Split und ohne Segmente, und ist nur ein Vergleichswert.
"""

import config
import gueltigkeit
from evaluation.evalset import (
    STANDARD_FAELLE,
    Kosten,
    hole_zeilen,
    kostenminimale_schwelle,
    lade_faelle,
    vorab_verweigert,
)


def _spanne(werte):
    return (min(werte), max(werte)) if werte else None


def diagnose(zeilen, kosten):
    """Score-Spannen je Label, Trennlücke und kostenminimale Schwelle über alle Zeilen."""
    mit_treffer = [z for z in zeilen if z["top"] is not None]
    vorab = vorab_verweigert(zeilen)
    antwortbar = _spanne([z["top"] for z in mit_treffer if z["erwartet"] == "answer"])
    abstain = _spanne([z["top"] for z in mit_treffer if z["erwartet"] == "abstain"])
    schwelle, summe, korrekt = kostenminimale_schwelle(zeilen, kosten)
    return {
        "antwortbar": antwortbar,
        "abstain": abstain,
        "trennluecke": (abstain[1], antwortbar[0]) if antwortbar and abstain else None,
        "ohne_treffer": len(zeilen) - len(mit_treffer) - sum(vorab.values()),
        "vorab_verweigert": vorab,
        "schwelle": schwelle,
        "kosten": summe,
        "korrekt": korrekt,
        "n": len(zeilen),
    }


def _zahl(wert):
    return "—" if wert is None else f"{wert:.3f}"


def main():
    from rag import index

    faelle = lade_faelle(STANDARD_FAELLE)
    stichtag = gueltigkeit.heute_berlin()
    print(f"Stichtag: {stichtag.isoformat()}")
    zeilen = hole_zeilen(faelle, index.as_retriever(similarity_top_k=3), stichtag)
    fragen = {f["id"]: f["frage"] for f in faelle}
    for z in sorted(zeilen, key=lambda z: -1.0 if z["top"] is None else z["top"], reverse=True):
        print(f"{_zahl(z['top'])}  {z['erwartet']:8s}  {z['kategorie']:16s}  {fragen[z['id']][:60]}")

    kosten = Kosten(config.KOSTEN_FALSCHANTWORT, config.KOSTEN_ESKALATION)
    d = diagnose(zeilen, kosten)
    for name, spanne in (("Antwortbar", d["antwortbar"]), ("Abstain", d["abstain"])):
        if spanne:
            print(f"{name:11s}: min={spanne[0]:.3f}  max={spanne[1]:.3f}")
    if d["ohne_treffer"]:
        print(f"Ohne Treffer: {d['ohne_treffer']} (immer verweigert)")
    for reason, n in sorted(d["vorab_verweigert"].items()):
        print(f"Vorab verweigert ({reason}): {n} (Gültigkeit/Fassung, immer verweigert)")
    if d["trennluecke"]:
        unten, oben = d["trennluecke"]
        print(f"Trennlücke : [{unten:.3f} .. {oben:.3f}]  Breite={oben - unten:.3f}")
    print(
        f"Kostenminimale Schwelle: {_zahl(d['schwelle'])}  (Kosten {d['kosten']:g} bei "
        f"Falschantwort={kosten.falschantwort:g}, Eskalation={kosten.eskalation:g}; {d['korrekt']}/{d['n']} korrekt)"
    )
    print("Nur Diagnose. Schwellen für den Betrieb: python -m evaluation.eval_report (ADR 0012).")


if __name__ == "__main__":
    main()
