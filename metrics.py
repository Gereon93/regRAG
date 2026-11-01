"""Prozesslokale Eskalationsmetriken fuer GET /metrics — nur Zaehler, keine Inhalte, nur stdlib."""

import threading
from collections import Counter
from datetime import datetime, timezone

BUCKETS = 10
BUCKET_SCHLUESSEL = tuple(f"{i / BUCKETS:.1f}-{(i + 1) / BUCKETS:.1f}" for i in range(BUCKETS))
HISTOGRAMM_SCHLUESSEL = (*BUCKET_SCHLUESSEL, "unter_0", "ueber_1", "none")
SCORE_FELDER = ("answerable", "relevance")


def _bucket(wert):
    if wert is None:
        return "none"
    if wert < 0:
        return "unter_0"
    if wert > 1:
        return "ueber_1"
    return BUCKET_SCHLUESSEL[min(int(wert * BUCKETS), BUCKETS - 1)]


def _rate(eskalationen, gesamt):
    return eskalationen / gesamt if gesamt else None


class Metriken:
    def __init__(self):
        self._lock = threading.Lock()
        self._seit = datetime.now(timezone.utc)
        self._gesamt = 0
        self._eskalationen = 0
        self._reasons = Counter()
        self._segment_gesamt = Counter()
        self._segment_eskalationen = Counter()
        self._histogramme = {feld: Counter() for feld in SCORE_FELDER}

    def zaehle(self, entscheidung, segment):
        buckets = {feld: _bucket(getattr(entscheidung.scores, feld)) for feld in SCORE_FELDER}
        reasons = {r.value for r in entscheidung.reasons}
        with self._lock:
            self._gesamt += 1
            self._segment_gesamt[segment] += 1
            if entscheidung.verweigert:
                self._eskalationen += 1
                self._segment_eskalationen[segment] += 1
                self._reasons.update(reasons)
            for feld, bucket in buckets.items():
                self._histogramme[feld][bucket] += 1

    def schnappschuss(self):
        with self._lock:
            return {
                "entscheidungen_gesamt": self._gesamt,
                "eskalationen_gesamt": self._eskalationen,
                "eskalationsrate": _rate(self._eskalationen, self._gesamt),
                "eskalationen_nach_reason": dict(self._reasons),
                "nach_segment": {
                    segment: {
                        "entscheidungen": gesamt,
                        "eskalationen": self._segment_eskalationen[segment],
                        "eskalationsrate": _rate(self._segment_eskalationen[segment], gesamt),
                    }
                    for segment, gesamt in self._segment_gesamt.items()
                },
                "histogramme": {
                    feld: {k: zaehler[k] for k in HISTOGRAMM_SCHLUESSEL}
                    for feld, zaehler in self._histogramme.items()
                },
                "seit": self._seit.isoformat(),
            }


METRIKEN = Metriken()
