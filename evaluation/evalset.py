"""Versioniertes Eval-Set, Splits, Kostenfunktion, Schwellen pro Segment und Report (docs/adr/0012, Issue #31).

Reine Funktionen ohne schwere Imports, damit die CI sie testen kann.
"""

import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

STANDARD_FAELLE = Path(__file__).parent / "evalset" / "v1.jsonl"

PFLICHTFELDER = ("id", "frage", "erwartet", "segment", "kategorie")
ERWARTET = ("answer", "abstain")
SPLIT_ANTEILE = (("train", 60), ("calibration", 20), ("test", 20))
SPLIT_BUCKETS = sum(anteil for _, anteil in SPLIT_ANTEILE)
SPLIT_GRENZEN = tuple(
    (name, sum(anteil for _, anteil in SPLIT_ANTEILE[: i + 1])) for i, (name, _) in enumerate(SPLIT_ANTEILE)
)
Z_95 = 1.959963984540054

FALSCHANTWORT_DEFINITION = (
    "Falschantwort = erwartet abstain, Gate antwortet. Eskalation = Gate verweigert. "
    "Falschantwortrate = Falschantworten / beantwortet, Eskalationsrate = eskaliert / n, "
    "Coverage = beantwortet / n. Gemessen wird die Gate-Entscheidung, nicht die Textqualität der Antwort."
)


@dataclass(frozen=True)
class Kosten:
    falschantwort: float
    eskalation: float

    def __post_init__(self):
        if self.falschantwort < 0 or self.eskalation < 0:
            raise ValueError("Kostengewichte dürfen nicht negativ sein.")


def lade_faelle(pfad):
    faelle, gesehen = [], set()
    for nummer, zeile in enumerate(Path(pfad).read_text(encoding="utf-8").splitlines(), 1):
        if not zeile.strip():
            continue
        try:
            fall = json.loads(zeile)
        except json.JSONDecodeError as fehler:
            raise ValueError(f"Zeile {nummer}: kein gültiges JSON ({fehler.msg}).") from fehler
        _pruefe(fall, nummer, gesehen)
        faelle.append({**fall, "gruppe": fall.get("gruppe") or fall["id"]})
    return faelle


def _pruefe(fall, nummer, gesehen):
    for feld in PFLICHTFELDER:
        if not isinstance(fall.get(feld), str) or not fall[feld].strip():
            raise ValueError(f"Zeile {nummer}: Feld '{feld}' fehlt oder ist leer.")
    if fall["erwartet"] not in ERWARTET:
        raise ValueError(f"Zeile {nummer}: erwartet muss {ERWARTET} sein, nicht {fall['erwartet']!r}.")
    if fall["id"] in gesehen:
        raise ValueError(f"Zeile {nummer}: doppelte id {fall['id']!r}.")
    gesehen.add(fall["id"])


def eval_version(faelle):
    kanonisch = json.dumps(sorted(faelle, key=lambda f: f["id"]), sort_keys=True, ensure_ascii=False)
    return "sha256:" + hashlib.sha256(kanonisch.encode("utf-8")).hexdigest()


def teile(faelle):
    """Teilt nach Hash der Gruppe, damit Gruppen zusammenbleiben und neue Fälle bestehende nicht verschieben."""
    splits = {name: [] for name, _ in SPLIT_ANTEILE}
    for fall in faelle:
        splits[_split_von(fall["gruppe"])].append(fall)
    return splits


def _split_von(gruppe):
    bucket = int(hashlib.sha256(gruppe.encode("utf-8")).hexdigest(), 16) % SPLIT_BUCKETS
    return next(name for name, grenze in SPLIT_GRENZEN if bucket < grenze)


def _beantwortet(zeile, schwelle):
    """Ohne Retrieval-Treffer (top=None) verweigert das Gate immer, wie RetrievalScoreGate."""
    return zeile["top"] is not None and zeile["top"] >= schwelle


def _kosten(zeilen, schwelle, kosten):
    summe = 0.0
    for z in zeilen:
        beantwortet = _beantwortet(z, schwelle)
        if beantwortet and z["erwartet"] == "abstain":
            summe += kosten.falschantwort
        elif not beantwortet and z["erwartet"] == "answer":
            summe += kosten.eskalation
    return summe


def _kandidaten(scores):
    werte = sorted({s for s in scores if s is not None})
    if not werte:
        return [math.inf]
    mitten = [(a + b) / 2 for a, b in pairwise(werte)]
    return [werte[0], *mitten, math.nextafter(werte[-1], math.inf)]


def schwellen_pro_segment(zeilen, kosten):
    nach_segment = defaultdict(list)
    for z in zeilen:
        nach_segment[z["segment"]].append(z)
    return {
        segment: min(
            _kandidaten(z["top"] for z in gruppe),
            key=lambda schwelle: (_kosten(gruppe, schwelle, kosten), -schwelle),
        )
        for segment, gruppe in nach_segment.items()
    }


def wilson(erfolge, n, z=Z_95):
    if n == 0:
        return 0.0, 1.0
    p = erfolge / n
    nenner = 1 + z * z / n
    mitte = (p + z * z / (2 * n)) / nenner
    halb = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / nenner
    return max(0.0, mitte - halb), min(1.0, mitte + halb)


def _rate(zaehler, nenner):
    if nenner == 0:
        return {"wert": None, "ci95": wilson(0, 0)}
    return {"wert": zaehler / nenner, "ci95": wilson(zaehler, nenner)}


def _kennzahlen(zeilen, schwelle_von):
    beantwortet = [z for z in zeilen if _beantwortet(z, schwelle_von(z))]
    n = len(zeilen)
    falsch = sum(z["erwartet"] == "abstain" for z in beantwortet)
    return {
        "n": n,
        "beantwortet": len(beantwortet),
        "falschantworten": falsch,
        "eskaliert": n - len(beantwortet),
        "falschantwortrate": _rate(falsch, len(beantwortet)),
        "eskalationsrate": _rate(n - len(beantwortet), n),
        "coverage": _rate(len(beantwortet), n),
    }


def bericht(zeilen, schwellen, kosten, version):
    nach_segment = defaultdict(list)
    for z in zeilen:
        nach_segment[z["segment"]].append(z)
    fehlend = sorted(set(nach_segment) - set(schwellen))
    if fehlend:
        raise KeyError(f"Keine Schwelle für Segment(e): {', '.join(fehlend)}")
    return {
        "eval_version": version,
        "definition": FALSCHANTWORT_DEFINITION,
        "kosten": {"falschantwort": kosten.falschantwort, "eskalation": kosten.eskalation},
        "schwellen": schwellen,
        "segmente": {
            segment: _kennzahlen(gruppe, lambda z: schwellen[z["segment"]])
            for segment, gruppe in sorted(nach_segment.items())
        },
        "gesamt": _kennzahlen(zeilen, lambda z: schwellen[z["segment"]]),
    }
