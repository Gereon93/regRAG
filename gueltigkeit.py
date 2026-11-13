"""Gültigkeitsmetadaten und Stichtag (ADR 0015) — nur Standardbibliothek, damit die CI es testen kann."""

import re
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")
ISO_DATUM = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
MAX_FASSUNG_ZEICHEN = 100

GUELTIG = "gueltig"
UNBEKANNT = "unbekannt"
NOCH_NICHT_GUELTIG = "noch_nicht_gueltig"
ABGELAUFEN = "abgelaufen"
METADATEN_UNGUELTIG = "metadaten_ungueltig"
NUTZBAR = frozenset({GUELTIG, UNBEKANNT})

FELDER = ("gueltig_ab", "gueltig_bis", "fassung")


class MetadatenFehler(ValueError):
    pass


def parse_datum(wert, feld):
    if not isinstance(wert, str) or not ISO_DATUM.fullmatch(wert):
        raise MetadatenFehler(f"{feld}: erwartet YYYY-MM-DD.")
    try:
        return date.fromisoformat(wert)
    except ValueError:
        raise MetadatenFehler(f"{feld}: kein gültiges Datum.") from None


def _pruefe_fassung(wert):
    fassung = wert.strip() if isinstance(wert, str) else ""
    if not 1 <= len(fassung) <= MAX_FASSUNG_ZEICHEN:
        raise MetadatenFehler(f"fassung: erwartet 1 bis {MAX_FASSUNG_ZEICHEN} Zeichen Text.")
    return fassung


def normalisiere(roh):
    """Die eine Validierung für Sidecar und Upload; liefert nur gesetzte Felder."""
    felder = {}
    ab, bis = (roh.get(feld) for feld in ("gueltig_ab", "gueltig_bis"))
    if ab is not None:
        parse_datum(ab, "gueltig_ab")
        felder["gueltig_ab"] = ab
    if bis is not None:
        parse_datum(bis, "gueltig_bis")
        felder["gueltig_bis"] = bis
    if ab is not None and bis is not None and bis < ab:
        raise MetadatenFehler("gueltig_bis liegt vor gueltig_ab.")
    if roh.get("fassung") is not None:
        felder["fassung"] = _pruefe_fassung(roh["fassung"])
    return felder


def status_von(metadata, stichtag):
    """Status einer Fundstelle am Stichtag; wirft nie, auch nicht bei manipuliertem Index."""
    ab, bis = metadata.get("gueltig_ab"), metadata.get("gueltig_bis")
    if ab is None and bis is None:
        return UNBEKANNT
    try:
        normalisiere({"gueltig_ab": ab, "gueltig_bis": bis})
    except MetadatenFehler:
        return METADATEN_UNGUELTIG
    if ab is not None and stichtag < date.fromisoformat(ab):
        return NOCH_NICHT_GUELTIG
    if bis is not None and stichtag > date.fromisoformat(bis):
        return ABGELAUFEN
    return GUELTIG


def ist_verdraengt(status_top_k):
    """Im Top-k ein nicht nutzbarer Treffer und kein gültiger: der Fall, in dem heute `OUTDATED_SOURCE` folgt (#53)."""
    return GUELTIG not in status_top_k and any(s not in NUTZBAR for s in status_top_k)


def waehle_treffer(kandidaten, top_k, stichtag):
    """Top-k; nur wenn abgelöste Treffer die gültige Fassung daraus verdrängen, länger (docs/adr/0015, #53).

    Dann das kürzeste Präfix mit `top_k` nutzbaren Treffern; die nicht nutzbaren davor bleiben für Gate und Prüfer.
    """
    status = [status_von(n.metadata, stichtag) for n in kandidaten]
    if not ist_verdraengt(status[:top_k]):
        return kandidaten[:top_k]
    nutzbar = 0
    for ende, s in enumerate(status, start=1):
        nutzbar += s in NUTZBAR
        if nutzbar == top_k:
            return kandidaten[:ende]
    return kandidaten


def fassungs_kennung(metadata):
    return tuple(metadata.get(feld) for feld in FELDER)


def heute_berlin(jetzt=None):
    return (jetzt or datetime.now(timezone.utc)).astimezone(BERLIN).date()
