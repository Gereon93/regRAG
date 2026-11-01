"""Decision Gate zwischen Retrieval und Generierung — ohne schwere Imports, damit die CI ihn testen kann."""

import json
import logging
import math
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Protocol

import metrics

log = logging.getLogger(__name__)

MAX_FRAGE_ZEICHEN = 2000
MAX_EVIDENZ_ZEICHEN = 2000
ESKALATIONS_DATEI_RECHTE = 0o600
STANDARD_MAX_BYTES = 5 * 1024 * 1024
STANDARD_MAX_ARCHIVE = 5
STANDARD_AUFBEWAHRUNG_TAGE = 90

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


def logit(p):
    """logit(clip(p, EPS, 1-EPS)); gemeinsame Eingabeklemmung fuer Gate und NLL-Fit."""
    p = min(max(p, EPS), 1 - EPS)
    return math.log(p / (1 - p))


def skaliere(p, temperatur):
    """sigmoid(logit(clip(p, EPS, 1-EPS)) / T) ohne Ausgabe-Clip."""
    return 1 / (1 + math.exp(-logit(p) / temperatur))


def kalibriere(p, temperatur):
    """Temperature Scaling, Ausgabe auf [EPS, 1-EPS] geklemmt (kleines T saettigt sonst auf exakt 0/1)."""
    return min(max(skaliere(p, temperatur), EPS), 1 - EPS)

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
        if bester is None or not bester >= self.min_score:
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
        "segment": segment_von(nodes),
    }


@dataclass(frozen=True)
class EskalationsGrenzen:
    """Obergrenzen des Eskalationsdatensatzes: aktive Datei, Zahl der Archive, Alter (ADR 0010)."""

    max_bytes: int = STANDARD_MAX_BYTES
    max_archive: int = STANDARD_MAX_ARCHIVE
    aufbewahrung_tage: int = STANDARD_AUFBEWAHRUNG_TAGE

    def __post_init__(self):
        if self.max_bytes < 1 or self.aufbewahrung_tage < 1 or self.max_archive < 0:
            raise ValueError("Grenzen: max_bytes und aufbewahrung_tage >= 1, max_archive >= 0.")


def grenzen_aus(einstellungen):
    return EskalationsGrenzen(
        einstellungen.ESKALATION_MAX_BYTES,
        einstellungen.ESKALATION_MAX_ARCHIVE,
        einstellungen.ESKALATION_AUFBEWAHRUNG_TAGE,
    )


# Rotation und Append laufen im Prozess nie parallel; mehrere Prozesse bleiben ein akzeptiertes Race (ADR 0010).
_ESKALATIONS_SPERRE = threading.Lock()


def _aeltester_zeitpunkt(datei):
    """Zeitpunkt des ältesten Datensatzes: erste Zeile mit gültigem `zeitpunkt`, sonst die Änderungszeit.

    Ein Zeitstempel ohne Zeitzone gilt als UTC. Ein Parsingproblem darf den Append nie verhindern.
    """
    with open(datei, encoding="utf-8", errors="replace") as f:
        for zeile in f:
            try:
                zeitpunkt = datetime.fromisoformat(json.loads(zeile)["zeitpunkt"])
            except (ValueError, KeyError, TypeError):
                continue
            return zeitpunkt if zeitpunkt.tzinfo else zeitpunkt.replace(tzinfo=timezone.utc)
    return datetime.fromtimestamp(Path(datei).stat().st_mtime, timezone.utc)


def _archive(ziel):
    """Archive heißen `<name>.<UTC-Zeitstempel>.<pid>`; fremde Dateien (`.bak`, `.lock`) zählen nicht."""
    muster = re.compile(re.escape(ziel.name) + r"\.\d{8}T\d{12}Z\.\d+")
    return sorted(p for p in ziel.parent.glob(f"{ziel.name}.*") if muster.fullmatch(p.name))


def _ist_aelter(datei, grenze):
    try:
        return _aeltester_zeitpunkt(datei) < grenze
    except FileNotFoundError:
        return False


def _rotiere(ziel, grenzen, jetzt):
    halbe_frist = jetzt - timedelta(days=grenzen.aufbewahrung_tage) / 2
    try:
        groesse = ziel.stat().st_size
    except FileNotFoundError:
        return
    if groesse and (groesse >= grenzen.max_bytes or _ist_aelter(ziel, halbe_frist)):
        archiv = ziel.with_name(f"{ziel.name}.{jetzt:%Y%m%dT%H%M%S%fZ}.{os.getpid()}")
        if not archiv.exists():
            os.replace(ziel, archiv)


def _bereinige(ziel, grenzen, jetzt):
    """Rotiert die aktive Datei bei Größe oder halber Frist, löscht Archive über Frist oder Anzahl."""
    if ziel.exists() and not ziel.is_file():
        return
    _rotiere(ziel, grenzen, jetzt)
    frist = jetzt - timedelta(days=grenzen.aufbewahrung_tage)
    veraltet = [a for a in _archive(ziel) if _ist_aelter(a, frist)]
    for archiv in veraltet:
        archiv.unlink(missing_ok=True)
    if veraltet:
        log.info("Eskalationsarchive nach Aufbewahrungsfrist gelöscht: %d", len(veraltet))
    archive = _archive(ziel)
    ueberzaehlig = archive[: max(0, len(archive) - grenzen.max_archive)]
    for archiv in ueberzaehlig:
        archiv.unlink(missing_ok=True)
    if ueberzaehlig:
        log.warning(
            "Eskalationsarchive vor Fristende wegen max_archive=%d verdrängt: %d (Flut?)",
            grenzen.max_archive,
            len(ueberzaehlig),
        )


def bereinige_eskalationen(ziel, grenzen):
    """Wendet Frist und Archivgrenze ohne neuen Datensatz an (Start des Servers). Fehler werden nur geloggt."""
    ziel = Path(ziel)
    with _ESKALATIONS_SPERRE:
        try:
            _bereinige(ziel, grenzen, datetime.now(timezone.utc))
        except OSError:
            log.exception("Bereinigung des Eskalationsdatensatzes %s fehlgeschlagen", ziel)


def schreibe_eskalation(ziel, frage, nodes, entscheidung, grenzen=None):
    """Hängt den Datensatz als JSONL-Zeile an und meldet, ob das gelang. Ein Fehler kippt die Verweigerung nicht."""
    grenzen = grenzen or EskalationsGrenzen()
    try:
        zeile = json.dumps(eskalationsdatensatz(frage, nodes, entscheidung), ensure_ascii=False) + "\n"
        ziel = Path(ziel)
        ziel.parent.mkdir(parents=True, exist_ok=True)
        with _ESKALATIONS_SPERRE:
            try:
                _bereinige(ziel, grenzen, datetime.now(timezone.utc))
            except OSError:
                log.exception("Rotation des Eskalationsdatensatzes %s fehlgeschlagen", ziel)
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


def _zaehle(entscheidung, nodes):
    """Metriken sind Beiwerk: ein Fehler beim Zaehlen darf die Entscheidung nie veraendern."""
    try:
        metrics.METRIKEN.zaehle(entscheidung, segment_von(nodes))
    except Exception:  # Zaehlen darf die Gate-Entscheidung nie kippen (breit gefangen mit Absicht)
        log.exception("Metriken konnten nicht gezaehlt werden")


def entscheide_und_eskaliere(gate, eskalations_datei, frage, nodes, grenzen=None):
    entscheidung = gate.entscheide(frage, nodes)
    log.info(
        "Gate: %s reasons=%s relevance=%s answerable=%s",
        entscheidung.outcome.value,
        [r.value for r in entscheidung.reasons],
        entscheidung.scores.relevance,
        entscheidung.scores.answerable,
    )
    _zaehle(entscheidung, nodes)
    vorgemerkt = entscheidung.verweigert and schreibe_eskalation(
        eskalations_datei, frage, nodes, entscheidung, grenzen
    )
    return Pruefung(entscheidung, vorgemerkt)
