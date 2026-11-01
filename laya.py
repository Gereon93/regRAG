"""Laya-Adapter hinter dem Gate-Port (System-One-/Jev-Protokoll)."""

import logging
import math
from urllib.parse import urlsplit

import httpx

from gate import (
    AbstainReason,
    GateDecision,
    GateScores,
    Kaskade,
    Outcome,
    RetrievalScoreGate,
    kalibriere,
    segment_von,
)
from manifest import lade_manifest

log = logging.getLogger(__name__)

PFAD = "/v1/systemone"
CONNECT_TIMEOUT_SEKUNDEN = 2.0
FRAGE_ID = "answerable"
FRAGEN = {FRAGE_ID: {"type": "noul", "instructions": "Enthält die Evidenz die Antwort auf die Frage?"}}
MAX_JUDGE_EVIDENZ_ZEICHEN = 20_000
LOKALE_HOSTS = {"localhost", "127.0.0.1", "::1", "host.docker.internal"}
ERSTER_FEHLERSTATUS = 400
ERSTER_SERVERFEHLER = 500


class JudgeFehler(Exception):
    pass


class SystemOneClient:
    def __init__(self, base_url, api_key="", timeout=30.0, transport=None):
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=httpx.Timeout(timeout, connect=CONNECT_TIMEOUT_SEKUNDEN),
            follow_redirects=False,
            transport=transport,
        )

    def entscheide(self, state, fragen, **optionen):
        try:
            antwort = self._http.post(PFAD, json={"state": state, "questions": fragen, **optionen})
        except httpx.HTTPError as e:
            log.warning("Laya nicht erreichbar: %s", type(e).__name__)
            raise JudgeFehler from None
        if antwort.status_code != httpx.codes.OK:
            self._melde_status(antwort.status_code)
            raise JudgeFehler
        try:
            return antwort.json()
        except ValueError:
            log.warning("Laya lieferte kein JSON")
            raise JudgeFehler from None

    @staticmethod
    def _melde_status(status):
        if ERSTER_FEHLERSTATUS <= status < ERSTER_SERVERFEHLER:
            log.error("Laya lehnt die Anfrage ab (HTTP %s) — Konfiguration prüfen", status)
        else:
            log.warning("Laya antwortet mit HTTP %s", status)


class LayaGate:
    def __init__(
        self,
        client,
        schwelle_fuer,
        kalibriere_score=lambda p: p,
        max_len=4096,
        lang="de",
        max_evidenz_zeichen=MAX_JUDGE_EVIDENZ_ZEICHEN,
        modell="multilingual",
        modell_streng=False,
    ):
        self.client = client
        self.schwelle_fuer = schwelle_fuer
        self.kalibriere_score = kalibriere_score
        self.max_len = max_len
        self.lang = lang
        self.max_evidenz_zeichen = max_evidenz_zeichen
        self.modell = modell
        self.modell_streng = modell_streng

    def answerable_roh(self, frage, nodes):
        """Rohwahrscheinlichkeit von Laya; wirft JudgeFehler bei Ausfall oder (streng) fremdem Checkpoint."""
        evidenz = "\n\n".join(n.text for n in nodes)[: self.max_evidenz_zeichen]
        state = {"frage": frage, "evidenz": evidenz}
        antwort = self.client.entscheide(state, FRAGEN, model=self.modell, lang=self.lang, max_len=self.max_len)
        wahrscheinlichkeit = _lies_wahrscheinlichkeit(antwort)
        if not self._pruefe_auffaelligkeiten(antwort) and self.modell_streng:
            raise JudgeFehler
        return wahrscheinlichkeit

    def entscheide(self, frage, nodes):
        try:
            wahrscheinlichkeit = self.answerable_roh(frage, nodes)
        except JudgeFehler:
            return GateDecision(Outcome.ABSTAIN, (AbstainReason.JUDGE_UNAVAILABLE,), GateScores())
        scores = GateScores(answerable=wahrscheinlichkeit)
        if self.kalibriere_score(wahrscheinlichkeit) < self.schwelle_fuer(segment_von(nodes)):
            return GateDecision(Outcome.ABSTAIN, (AbstainReason.NOT_ANSWERABLE,), scores)
        return GateDecision(Outcome.ANSWER, (), scores)

    def _pruefe_auffaelligkeiten(self, antwort):
        """Loggt Auffälligkeiten; gibt False zurück, wenn ein anderer Checkpoint geantwortet hat."""
        usage = antwort.get("usage")
        if isinstance(usage, dict) and usage.get("truncated") is True:
            log.warning("Laya hat den State gekürzt — Antwort beruht auf unvollständiger Evidenz")
        routing = antwort.get("routing")
        modell = routing.get("model") if isinstance(routing, dict) else None
        if modell is not None and modell != self.modell:
            log.warning("Laya hat mit Checkpoint %s statt %s geantwortet", modell, self.modell)
            return False
        return True


def _lies_wahrscheinlichkeit(antwort):
    try:
        wert = antwort["answers"][FRAGE_ID]["noul"]
    except (KeyError, TypeError):
        log.warning("Laya-Antwort ohne %s", FRAGE_ID)
        raise JudgeFehler from None
    if isinstance(wert, bool) or not isinstance(wert, int | float) or not math.isfinite(wert) or not 0 <= wert <= 1:
        log.warning("Laya-Antwort mit ungültiger Wahrscheinlichkeit")
        raise JudgeFehler
    return wert


def warne_wenn_nicht_lokal(url):
    host = urlsplit(url).hostname
    if host not in LOKALE_HOSTS:
        log.warning("Laya-Server %s ist nicht lokal — Fragen und Evidenz verlassen diesen Rechner", host)


def baue_gate(einstellungen, transport=None):
    score_gate = RetrievalScoreGate(einstellungen.MIN_RETRIEVAL_SCORE)
    url = (einstellungen.LAYA_URL or "").strip()
    manifest_pfad = (einstellungen.GATE_MANIFEST or "").strip()
    if not url:
        if manifest_pfad:
            raise ValueError("REGRAG_GATE_MANIFEST ist gesetzt, aber REGRAG_LAYA_URL fehlt.")
        return score_gate
    warne_wenn_nicht_lokal(url)
    client = SystemOneClient(url, einstellungen.LAYA_API_KEY, einstellungen.LAYA_TIMEOUT, transport)
    optionen = {
        "max_len": einstellungen.LAYA_MAX_LEN,
        "lang": einstellungen.LAYA_LANG,
        "modell": einstellungen.LAYA_MODELL,
    }
    if manifest_pfad:
        manifest = lade_manifest(manifest_pfad, einstellungen)
        laya = LayaGate(
            client,
            manifest.schwelle_fuer,
            lambda p: kalibriere(p, manifest.temperatur),
            modell_streng=True,
            **optionen,
        )
    else:
        laya = LayaGate(client, lambda segment: einstellungen.LAYA_MIN_ANSWERABLE, **optionen)
    return Kaskade([score_gate, laya])
