"""Gültigkeitsmetadaten und Stichtag (ADR 0015) — nur Standardbibliothek, damit die CI es testen kann."""

import json
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


def lies_sidecar(md_pfad):
    """Das eine Lesen des Sidecars für Index und Dokumentliste; jeder Lesefehler ist ein MetadatenFehler."""
    sidecar = md_pfad.with_suffix(".source.json")
    try:
        daten = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.exists() else {}
    except (OSError, ValueError) as e:
        raise MetadatenFehler(f"Sidecar nicht lesbar: {e}") from e
    if not isinstance(daten, dict):
        raise MetadatenFehler("Sidecar ist kein Objekt.")
    return daten


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


def fassungs_kennung(metadata):
    return tuple(metadata.get(feld) for feld in FELDER)


def heute_berlin(jetzt=None):
    return (jetzt or datetime.now(timezone.utc)).astimezone(BERLIN).date()
