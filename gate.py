"""Decision Gate zwischen Retrieval und Generierung — ohne schwere Imports, damit die CI ihn testen kann."""

import json
import logging
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

ABSTAIN_ANTWORT_VORGEMERKT = (
    "Keine belastbare Grundlage in den indexierten Dokumenten. "
    "Die Anfrage wurde zur Prüfung durch einen Menschen vorgemerkt."
)
ABSTAIN_ANTWORT_NICHT_VORGEMERKT = "Keine belastbare Grundlage in den indexierten Dokumenten."


class Outcome(Enum):
    ANSWER = "ANSWER"
    ABSTAIN = "ABSTAIN"


class AbstainReason(Enum):
    LOW_RELEVANCE = "LOW_RELEVANCE"
    NOT_ANSWERABLE = "NOT_ANSWERABLE"
    OUTDATED_SOURCE = "OUTDATED_SOURCE"
    CONFLICTING_PROVISIONS = "CONFLICTING_PROVISIONS"
    UNGROUNDED_CLAIM = "UNGROUNDED_CLAIM"


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


def abstain_antwort(vorgemerkt):
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
    vorgemerkt = entscheidung.verweigert and schreibe_eskalation(eskalations_datei, frage, nodes, entscheidung)
    return Pruefung(entscheidung, vorgemerkt)
