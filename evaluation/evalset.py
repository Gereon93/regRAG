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

import gueltigkeit
from gate import SEGMENTE, UNBEKANNT, AbstainReason, Kaskade, vorpruefung

STANDARD_FAELLE = Path(__file__).parent / "evalset" / "v1.jsonl"

PFLICHTFELDER = ("id", "frage", "erwartet", "segment", "kategorie")
ERWARTET = ("answer", "abstain")
AUSSERHALB = "ausserhalb"
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
    ausserhalb = fall["segment"] == AUSSERHALB and fall["erwartet"] == "abstain"
    if fall["segment"] not in SEGMENTE and not ausserhalb:
        raise ValueError(
            f"Zeile {nummer}: segment muss {SEGMENTE} sein oder '{AUSSERHALB}' mit erwartet abstain, "
            f"nicht {fall['segment']!r} bei erwartet {fall['erwartet']!r}."
        )
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


def top_score(nodes):
    """Bester Retrieval-Score wie im RetrievalScoreGate; None ohne Treffer."""
    return max((n.score for n in nodes), default=None)


LEERES_RETRIEVAL = AbstainReason.LOW_RELEVANCE.value  # mit Schwelle -inf meldet der Score-Gate nur noch leere Treffer


def laufzeit_evidenz(nodes, stichtag):
    """Was die Vorprüfung des Laufzeitpfads am Stichtag übrig lässt (ADR 0015, #54): (genutzte Nodes, Reason oder None).

    Die Score-Schwelle steht auf -inf: Sie wird in der Eval erst gesucht bzw. vom Aufrufer als Guard angewandt. Der
    Score-Gate verwirft so nur leere Treffer. Bei ABSTAIN sind die genutzten Nodes die bis dahin eingeengten, wie für
    Segment und Metriken im Laufzeitpfad.
    """
    entscheidung = Kaskade(vorpruefung(-math.inf, lambda: stichtag)).entscheide("", nodes)
    reason = entscheidung.reasons[0].value if entscheidung.verweigert else None
    return entscheidung.genutzte_nodes(nodes), reason


def hole_zeilen(faelle, retriever, stichtag=None):
    """Bester Score der Evidenz, die die Laufzeit am Stichtag nutzt; None, wenn sie vorab verweigert."""
    stichtag = stichtag or gueltigkeit.heute_berlin()
    zeilen = []
    for fall in faelle:
        nodes, reason = laufzeit_evidenz(retriever.retrieve(fall["frage"]), stichtag)
        zeilen.append(
            {
                "id": fall["id"],
                "segment": fall["segment"],
                "kategorie": fall["kategorie"],
                "erwartet": fall["erwartet"],
                "top": None if reason else top_score(nodes),
                "vorpruefung": reason,
            }
        )
    return zeilen


def vorab_verweigert(zeilen):
    """Fälle, die Gültigkeit oder Fassung vor jeder Schwelle verweigern, je Reason; leeres Retrieval zählt nicht."""
    zaehler = defaultdict(int)
    for z in zeilen:
        if z["vorpruefung"] not in (None, LEERES_RETRIEVAL):
            zaehler[z["vorpruefung"]] += 1
    return dict(zaehler)


def _beantwortet(zeile, schwelle, score_feld="top"):
    """Ohne Score (None) verweigert das Gate immer, wie RetrievalScoreGate."""
    return zeile[score_feld] is not None and zeile[score_feld] >= schwelle


def _kosten(zeilen, schwelle, kosten, score_feld="top"):
    summe = 0.0
    for z in zeilen:
        beantwortet = _beantwortet(z, schwelle, score_feld)
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


def _waehle_schwelle(gruppe, kosten, score_feld):
    kandidaten = _kandidaten(z[score_feld] for z in gruppe)
    if score_feld != "top" and not all(math.isfinite(k) and k <= 1 for k in kandidaten):
        raise ValueError(f"Schwellenkandidaten für {score_feld!r} müssen endlich und <= 1 sein: {kandidaten}")
    return min(kandidaten, key=lambda schwelle: (_kosten(gruppe, schwelle, kosten, score_feld), -schwelle))


def kostenminimale_schwelle(zeilen, kosten, score_feld="top"):
    """Schwelle mit den geringsten Kosten über alle Zeilen, dazu ihre Kosten und die Zahl korrekter Entscheidungen."""
    schwelle = _waehle_schwelle(zeilen, kosten, score_feld)
    korrekt = sum(_beantwortet(z, schwelle, score_feld) == (z["erwartet"] == "answer") for z in zeilen)
    return schwelle, _kosten(zeilen, schwelle, kosten, score_feld), korrekt


def schwellen_pro_segment(zeilen, kosten, score_feld="top", segment_feld="segment"):
    """Schwelle je Segment; UNBEKANNT bekommt nie eine eigene Schwelle (es gilt der Fallback)."""
    nach_segment = defaultdict(list)
    for z in zeilen:
        if z[segment_feld] != UNBEKANNT:
            nach_segment[z[segment_feld]].append(z)
    return {segment: _waehle_schwelle(gruppe, kosten, score_feld) for segment, gruppe in nach_segment.items()}


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


def _kennzahlen(zeilen, schwelle_von, score_feld="top"):
    beantwortet = [z for z in zeilen if _beantwortet(z, schwelle_von(z), score_feld)]
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


def _gruppiert(zeilen, feld):
    gruppen = defaultdict(list)
    for z in zeilen:
        gruppen[z[feld]].append(z)
    return dict(sorted(gruppen.items()))


def bericht(zeilen, schwellen, kosten, version, score_feld="top", segment_feld="segment", fallback=None):
    """Kennzahlen je Segment, je Kategorie und gesamt; die Schwelle kommt immer aus dem Segment des Falls."""
    nach_segment = _gruppiert(zeilen, segment_feld)
    fehlend = sorted(set(nach_segment) - set(schwellen))
    if fehlend and fallback is None:
        raise KeyError(f"Keine Schwelle für Segment(e): {', '.join(fehlend)}")

    def schwelle_von(z):
        return schwellen.get(z[segment_feld], fallback)

    return {
        "eval_version": version,
        "definition": FALSCHANTWORT_DEFINITION,
        "kosten": {"falschantwort": kosten.falschantwort, "eskalation": kosten.eskalation},
        "schwellen": schwellen,
        "segmente": {segment: _kennzahlen(gruppe, schwelle_von, score_feld) for segment, gruppe in nach_segment.items()},
        "kategorien": {
            kategorie: _kennzahlen(gruppe, schwelle_von, score_feld)
            for kategorie, gruppe in _gruppiert(zeilen, "kategorie").items()
        },
        "gesamt": _kennzahlen(zeilen, schwelle_von, score_feld),
    }
