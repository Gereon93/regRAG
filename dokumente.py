"""Upload-Validierung und Fingerprint-Logik — ohne schwere Imports, damit die CI sie testen kann."""

import hashlib
import os
import re
from pathlib import Path

MAX_MB = float(os.getenv("REGRAG_UPLOAD_MAX_MB", "25"))
PDF_MAGIC = b"%PDF-"
_UNERLAUBT = re.compile(r"[^A-Za-z0-9._-]")


class UploadFehler(ValueError):
    def __init__(self, meldung, status):
        super().__init__(meldung)
        self.status = status


def saeubere_dateiname(name):
    roh = Path(str(name).replace("\\", "/")).name
    sauber = _UNERLAUBT.sub("_", roh).lstrip(".")
    if not sauber.lower().endswith(".pdf"):
        raise UploadFehler("Nur PDF-Dateien werden angenommen.", 400)
    if len(sauber) <= len(".pdf"):
        raise UploadFehler("Dateiname fehlt.", 400)
    return sauber


def saeubere_md_name(name):
    roh = Path(str(name).replace("\\", "/")).name
    sauber = _UNERLAUBT.sub("_", roh).lstrip(".")
    if not sauber.lower().endswith(".md"):
        raise UploadFehler("Kein Dokument dieses Namens.", 400)
    if len(sauber) <= len(".md"):
        raise UploadFehler("Dateiname fehlt.", 400)
    return sauber


def pruefe_groesse(anzahl_bytes):
    if anzahl_bytes > MAX_MB * 1024 * 1024:
        raise UploadFehler(f"Datei ist größer als {MAX_MB:g} MB.", 413)


def pruefe_pdf(daten):
    pruefe_groesse(len(daten))
    if not daten.startswith(PDF_MAGIC):
        raise UploadFehler("Datei ist kein PDF.", 400)


def datei_hash(md_pfad):
    md_pfad = Path(md_pfad)
    h = hashlib.sha256()
    h.update(md_pfad.read_bytes())
    sidecar = md_pfad.with_suffix(".source.json")
    if sidecar.exists():
        h.update(sidecar.read_bytes())
    return h.hexdigest()


def leerer_fingerprint(embedding_modell, metrik):
    return {"embedding_modell": embedding_modell, "metrik": metrik, "dokumente": {}}


def fingerprint(md_dateien, embedding_modell, metrik):
    fp = leerer_fingerprint(embedding_modell, metrik)
    fp["dokumente"] = {Path(p).name: datei_hash(p) for p in md_dateien}
    return fp


def ohne_dokument(fp, name):
    """Fingerprint ohne den Eintrag für `name` — der Rest bleibt unangetastet."""
    neu = dict(fp)
    neu["dokumente"] = {n: h for n, h in fp["dokumente"].items() if n != name}
    return neu


def diff(alt, neu):
    """(zu_indexieren, zu_loeschen, voll_rebuild) — eine geänderte Datei zählt in beide Listen."""
    alt = alt or {}
    neue_docs = neu["dokumente"]
    alte_docs = alt.get("dokumente")

    fingerprint_aus_altem_release = alte_docs is not None and not isinstance(alte_docs, dict)
    voll_rebuild = (
        fingerprint_aus_altem_release
        or alt.get("embedding_modell") != neu["embedding_modell"]
        or alt.get("metrik") != neu["metrik"]
    )
    if voll_rebuild:
        return sorted(neue_docs), [], True

    alte_docs = alte_docs or {}
    zu_indexieren = sorted(n for n, h in neue_docs.items() if alte_docs.get(n) != h)
    zu_loeschen = sorted(n for n, h in alte_docs.items() if neue_docs.get(n) != h)
    return zu_indexieren, zu_loeschen, False
