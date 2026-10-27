"""Decision Gate zwischen Retrieval und Generierung — ohne schwere Imports, damit die CI ihn testen kann."""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)


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


def eskalationsdatensatz(frage, nodes, entscheidung, jetzt=None):
    if not entscheidung.verweigert:
        raise ValueError("Eskalationsdatensatz gibt es nur bei ABSTAIN.")
    jetzt = jetzt or datetime.now(timezone.utc)
    if jetzt.utcoffset() != timedelta(0):
        raise ValueError("Zeitstempel muss tz-aware UTC sein.")
    return {
        "zeitpunkt": jetzt.isoformat(),
        "frage": frage,
        "evidenz": [
            {
                "quelle": n.metadata.get("quelle") or n.metadata.get("file_name"),
                "score": n.score,
                "text": n.text,
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
    """Hängt den Datensatz als JSONL-Zeile an. Ein Schreibfehler darf die Verweigerung nicht kippen."""
    try:
        zeile = json.dumps(eskalationsdatensatz(frage, nodes, entscheidung), ensure_ascii=False) + "\n"
        ziel = Path(ziel)
        ziel.parent.mkdir(parents=True, exist_ok=True)
        with ziel.open("a", encoding="utf-8") as f:
            f.write(zeile)
    except OSError:
        log.exception("Eskalationsdatensatz konnte nicht nach %s geschrieben werden", ziel)


def entscheide_und_eskaliere(gate, eskalations_datei, frage, nodes):
    entscheidung = gate.entscheide(frage, nodes)
    if entscheidung.verweigert:
        schreibe_eskalation(eskalations_datei, frage, nodes, entscheidung)
    return entscheidung
