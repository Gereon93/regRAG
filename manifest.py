"""Gate-Manifest: Schwellen, Temperatur und Checkpoint-Herkunft laden, validieren, schreiben (stdlib only)."""

import json
import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path

from gate import SEGMENTE

log = logging.getLogger(__name__)

SCHEMA = 1
TEMPERATUR_MIN = 0.05
TEMPERATUR_MAX = 20.0


# noinspection PyClassHasNoInit
@dataclass(frozen=True)
class Manifest:
    """Gate-Manifest (docs/adr/0013); __init__ erzeugt @dataclass."""

    checkpoint_modell: str
    checkpoint_revision: str
    eval_version: str
    min_retrieval_score: float
    temperatur: float
    schwellen: dict[str, float]
    fallback_schwelle: float
    kosten: dict[str, float]
    erzeugt: str

    def schwelle_fuer(self, segment: str) -> float:
        return self.schwellen.get(segment, self.fallback_schwelle)


def _zahl(wert, feld: str) -> float:
    try:
        endlich = (
            not isinstance(wert, bool)
            and isinstance(wert, (int, float))
            and math.isfinite(wert)
        )
    except OverflowError:  # riesige int-Literale lassen sich nicht in float wandeln
        endlich = False
    if not endlich:
        raise ValueError(f"Manifest: {feld} muss eine endliche Zahl sein.")
    return float(wert)


def _anteil(wert, feld: str) -> float:
    zahl = _zahl(wert, feld)
    if not 0.0 <= zahl <= 1.0:
        raise ValueError(f"Manifest: {feld} muss in [0, 1] liegen.")
    return zahl


def _text(wert, feld: str) -> str:
    if not isinstance(wert, str) or not wert.strip():
        raise ValueError(f"Manifest: {feld} muss ein nicht leerer String sein.")
    return wert


def _objekt(wert, feld: str) -> dict:
    if not isinstance(wert, dict):
        raise ValueError(f"Manifest: {feld} muss ein Objekt sein.")  # noqa: TRY004 - Spec 23: jede Verletzung ist ValueError
    return wert


def _feld(daten: dict, feld: str, name: str = ""):
    if feld not in daten:
        raise ValueError(f"Manifest: Feld {name or feld} fehlt.")
    return daten[feld]


def _konstante_ablehnen(name: str):
    raise ValueError(
        f"Manifest: ungültige JSON-Konstante {name} (nur endliche Zahlen erlaubt)."
    )


def lade_manifest(pfad, einstellungen) -> Manifest:
    pfad = Path(pfad)
    try:
        text = pfad.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as fehler:
        raise ValueError(
            f"Manifest {pfad.name} nicht lesbar: {fehler.__class__.__name__}"
        ) from fehler
    try:
        daten = json.loads(text, parse_constant=_konstante_ablehnen)
    except json.JSONDecodeError as fehler:
        raise ValueError(
            f"Manifest {pfad.name}: ungültiges JSON ({fehler})"
        ) from fehler

    daten = _objekt(daten, "Manifest (Wurzel)")
    if _feld(daten, "schema") != SCHEMA or isinstance(daten["schema"], bool):
        raise ValueError(f"Manifest: schema muss {SCHEMA} sein.")

    checkpoint = _objekt(_feld(daten, "checkpoint"), "checkpoint")
    modell = _text(
        _feld(checkpoint, "modell", "checkpoint.modell"), "checkpoint.modell"
    )
    if modell != einstellungen.LAYA_MODELL:
        raise ValueError(
            f"Manifest: checkpoint.modell {modell!r} passt nicht zu LAYA_MODELL {einstellungen.LAYA_MODELL!r}."
        )
    revision = _text(
        _feld(checkpoint, "revision", "checkpoint.revision"), "checkpoint.revision"
    )

    eval_version = _text(_feld(daten, "eval_version"), "eval_version")
    if not eval_version.startswith("sha256:"):
        raise ValueError("Manifest: eval_version muss mit 'sha256:' beginnen.")

    min_score = _zahl(_feld(daten, "min_retrieval_score"), "min_retrieval_score")
    if min_score != einstellungen.MIN_RETRIEVAL_SCORE:
        raise ValueError(
            f"Manifest: min_retrieval_score {min_score} passt nicht zu MIN_RETRIEVAL_SCORE "
            f"{einstellungen.MIN_RETRIEVAL_SCORE}."
        )

    temperatur = _zahl(_feld(daten, "temperatur"), "temperatur")
    if not TEMPERATUR_MIN <= temperatur <= TEMPERATUR_MAX:
        raise ValueError(
            f"Manifest: temperatur muss in [{TEMPERATUR_MIN}, {TEMPERATUR_MAX}] liegen."
        )

    roh_schwellen = _objekt(_feld(daten, "schwellen"), "schwellen")
    for segment in roh_schwellen:
        if segment not in SEGMENTE:
            raise ValueError(
                f"Manifest: schwellen enthält unzulässiges Segment {segment!r}."
            )
    schwellen = {
        segment: _anteil(wert, f"schwellen.{segment}")
        for segment, wert in roh_schwellen.items()
    }

    fallback = _anteil(_feld(daten, "fallback_schwelle"), "fallback_schwelle")
    if schwellen and fallback < max(schwellen.values()):
        raise ValueError("Manifest: fallback_schwelle muss >= max(schwellen) sein.")

    roh_kosten = _objekt(_feld(daten, "kosten"), "kosten")
    kosten = {name: _zahl(wert, f"kosten.{name}") for name, wert in roh_kosten.items()}
    erzeugt = _text(_feld(daten, "erzeugt"), "erzeugt")

    log.info(
        "Gate-Manifest geladen: eval_version=%s checkpoint=%s@%s datei=%s",
        eval_version,
        modell,
        revision,
        pfad.name,
    )
    return Manifest(
        checkpoint_modell=modell,
        checkpoint_revision=revision,
        eval_version=eval_version,
        min_retrieval_score=min_score,
        temperatur=temperatur,
        schwellen=schwellen,
        fallback_schwelle=fallback,
        kosten=kosten,
        erzeugt=erzeugt,
    )


def schreibe_manifest(manifest: Manifest, verzeichnis) -> Path:
    """Schreibt gate-manifest-<eval_version[7:19]>-<revision>.json; ein zweiter Checkpoint ersetzt den ersten nicht."""
    inhalt = {
        "schema": SCHEMA,
        "checkpoint": {
            "modell": manifest.checkpoint_modell,
            "revision": manifest.checkpoint_revision,
        },
        "eval_version": manifest.eval_version,
        "min_retrieval_score": manifest.min_retrieval_score,
        "temperatur": manifest.temperatur,
        "schwellen": manifest.schwellen,
        "fallback_schwelle": manifest.fallback_schwelle,
        "kosten": manifest.kosten,
        "erzeugt": manifest.erzeugt,
    }
    text = json.dumps(inhalt, indent=2, ensure_ascii=False, allow_nan=False)
    verzeichnis = Path(verzeichnis)
    verzeichnis.mkdir(parents=True, exist_ok=True)
    revision = re.sub(r"[^A-Za-z0-9._-]", "_", manifest.checkpoint_revision)[:40]
    datei = verzeichnis / f"gate-manifest-{manifest.eval_version[7:19]}-{revision}.json"
    datei.write_text(text + "\n", encoding="utf-8")
    return datei
