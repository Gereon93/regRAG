"""Decision Gate zwischen Retrieval und Generierung — ohne schwere Imports, damit die CI ihn testen kann."""

import json
import logging
import math
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)

MAX_FRAGE_ZEICHEN = 2000
MAX_EVIDENZ_ZEICHEN = 2000
ESKALATIONS_DATEI_RECHTE = 0o600

SEGMENTE = ("gesetz", "verordnung", "richtlinie", "leitlinie", "auslegung")
UNBEKANNT = "unbekannt"
EPS = 1e-6
_GEWARNTE_SEGMENTE = set()


def normalisiere_segment(wert, quelle=None):
    """Bildet einen Metadatenwert auf SEGMENTE oder UNBEKANNT ab; unbekannte nicht leere Werte warnen einmalig."""
    if not isinstance(wert, str):
        return UNBEKANNT
    segment = wert.strip().lower()
    if segment in SEGMENTE:
        return segment
    if segment and (segment, quelle) not in _GEWARNTE_SEGMENTE:
        _GEWARNTE_SEGMENTE.add((segment, quelle))
        log.warning("Unbekanntes Segment %r in %s; behandelt als %s.", wert, quelle or "Metadaten", UNBEKANNT)
    return UNBEKANNT


def segment_von(nodes):
    """Gemeinsames Segment aller Nodes, sonst UNBEKANNT (gemischte Evidenz bekommt die strengste Schwelle)."""
    segmente = {normalisiere_segment(n.metadata.get("segment")) for n in nodes}
    return segmente.pop() if len(segmente) == 1 else UNBEKANNT


def kalibriere(p, T):
    """Temperature Scaling: sigmoid(logit(clip(p, EPS, 1-EPS)) / T)."""
    p = min(max(p, EPS), 1 - EPS)
    return 1 / (1 + math.exp(-math.log(p / (1 - p)) / T))

ABSTAIN_ANTWORT_VORGEMERKT = (
    "Keine belastbare Grundlage in den indexierten Dokumenten. "
    "Die Anfrage wurde zur Prüfung durch einen Menschen vorgemerkt."
)
ABSTAIN_ANTWORT_NICHT_VORGEMERKT = "Keine belastbare Grundlage in den indexierten Dokumenten."
JUDGE_AUSFALL_ANTWORT = "Die Prüfung der Antwortbarkeit ist derzeit nicht verfügbar. Bitte später erneut versuchen."
JUDGE_AUSFALL_ANTWORT_VORGEMERKT = (
    f"{JUDGE_AUSFALL_ANTWORT} Die Anfrage wurde zur Prüfung durch einen Menschen vorgemerkt."
)


class Outcome(Enum):
    ANSWER = "ANSWER"
    ABSTAIN = "ABSTAIN"


class AbstainReason(Enum):
    LOW_RELEVANCE = "LOW_RELEVANCE"
    NOT_ANSWERABLE = "NOT_ANSWERABLE"
    OUTDATED_SOURCE = "OUTDATED_SOURCE"
    CONFLICTING_PROVISIONS = "CONFLICTING_PROVISIONS"
    UNGROUNDED_CLAIM = "UNGROUNDED_CLAIM"
    JUDGE_UNAVAILABLE = "JUDGE_UNAVAILABLE"


@dataclass(frozen=True)
class GateScores:
    relevance: float | None = None
    answerable: float | None = None


@dataclass(frozen=True)
class GateDecision:
    outcome: Outcome
    reasons: tuple[AbstainReason, ...]
    scores: GateScores

    def __post_init__(self):
        if self.outcome is Outcome.ABSTAIN and not self.reasons:
            raise ValueError("ABSTAIN braucht mindestens eine Reason.")
        if self.outcome is Outcome.ANSWER and self.reasons:
            raise ValueError("ANSWER darf keine Reasons tragen.")

    @property
    def verweigert(self):
        return self.outcome is Outcome.ABSTAIN


class Gate(Protocol):
    def entscheide(self, frage, nodes) -> GateDecision: ...


class RetrievalScoreGate:
    """Verweigert, wenn der beste Retrieval-Score unter der Schwelle liegt (docs/adr/0002, 0005)."""

    def __init__(self, min_score):
        self.min_score = min_score

    def entscheide(self, frage, nodes):
        bester = max((n.score for n in nodes), default=None)
        scores = GateScores(relevance=bester)
        if bester is None or bester < self.min_score:
            return GateDecision(Outcome.ABSTAIN, (AbstainReason.LOW_RELEVANCE,), scores)
        return GateDecision(Outcome.ANSWER, (), scores)


class Kaskade:
    """Führt Gates der Reihe nach aus; das erste ABSTAIN gewinnt, bei gleichem Score-Feld der frühere Gate."""

    def __init__(self, gates):
        self.gates = tuple(gates)
        if not self.gates:
            raise ValueError("Eine Kaskade braucht mindestens einen Gate.")

    def entscheide(self, frage, nodes):
        scores = GateScores()
        for gate in self.gates:
            entscheidung = gate.entscheide(frage, nodes)
            scores = _fuehre_zusammen(scores, entscheidung.scores)
            if entscheidung.verweigert:
                return GateDecision(entscheidung.outcome, entscheidung.reasons, scores)
        return GateDecision(Outcome.ANSWER, (), scores)


def _fuehre_zusammen(frueher, spaeter):
    return GateScores(
        relevance=frueher.relevance if frueher.relevance is not None else spaeter.relevance,
        answerable=frueher.answerable if frueher.answerable is not None else spaeter.answerable,
    )


def aktiviere_score_log():
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        logger.addHandler(logging.StreamHandler())


def abstain_antwort(vorgemerkt, reasons=()):
    if AbstainReason.JUDGE_UNAVAILABLE in reasons:
        return JUDGE_AUSFALL_ANTWORT_VORGEMERKT if vorgemerkt else JUDGE_AUSFALL_ANTWORT
    return ABSTAIN_ANTWORT_VORGEMERKT if vorgemerkt else ABSTAIN_ANTWORT_NICHT_VORGEMERKT


def eskalationsdatensatz(frage, nodes, entscheidung, jetzt=None):
    if not entscheidung.verweigert:
        raise ValueError("Eskalationsdatensatz gibt es nur bei ABSTAIN.")
    jetzt = jetzt or datetime.now(timezone.utc)
    if jetzt.utcoffset() != timedelta(0):
        raise ValueError("Zeitstempel muss tz-aware UTC sein.")
    return {
        "zeitpunkt": jetzt.isoformat(),
        "frage": frage[:MAX_FRAGE_ZEICHEN],
        "evidenz": [
            {
                "quelle": n.metadata.get("quelle") or n.metadata.get("file_name"),
                "score": n.score,
                "text": n.text[:MAX_EVIDENZ_ZEICHEN],
            }
            for n in nodes
        ],
        "scores": {
            "relevance": entscheidung.scores.relevance,
            "answerable": entscheidung.scores.answerable,
        },
        "reasons": [r.value for r in entscheidung.reasons],
    }


def schreibe_eskalation(ziel, frage, nodes, entscheidung):
    """Hängt den Datensatz als JSONL-Zeile an und meldet, ob das gelang. Ein Fehler kippt die Verweigerung nicht."""
    try:
        zeile = json.dumps(eskalationsdatensatz(frage, nodes, entscheidung), ensure_ascii=False) + "\n"
        ziel = Path(ziel)
        ziel.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(ziel, os.O_WRONLY | os.O_APPEND | os.O_CREAT, ESKALATIONS_DATEI_RECHTE)
        try:
            os.write(fd, zeile.encode("utf-8"))
        finally:
            os.close(fd)
    except (OSError, TypeError, ValueError):
        log.exception("Eskalationsdatensatz konnte nicht nach %s geschrieben werden", ziel)
        return False
    return True


@dataclass(frozen=True)
class Pruefung:
    entscheidung: GateDecision
    vorgemerkt: bool

    @property
    def verweigert(self):
        return self.entscheidung.verweigert


def entscheide_und_eskaliere(gate, eskalations_datei, frage, nodes):
    entscheidung = gate.entscheide(frage, nodes)
    log.info(
        "Gate: %s reasons=%s relevance=%s answerable=%s",
        entscheidung.outcome.value,
        [r.value for r in entscheidung.reasons],
        entscheidung.scores.relevance,
        entscheidung.scores.answerable,
    )
    vorgemerkt = entscheidung.verweigert and schreibe_eskalation(eskalations_datei, frage, nodes, entscheidung)
    return Pruefung(entscheidung, vorgemerkt)
