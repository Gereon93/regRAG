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
)

log = logging.getLogger(__name__)

PFAD = "/v1/systemone"
CONNECT_TIMEOUT_SEKUNDEN = 2.0
MODELL = "multilingual"
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
    def __init__(self, client, min_answerable, max_len=4096, lang="de", max_evidenz_zeichen=MAX_JUDGE_EVIDENZ_ZEICHEN):
        self.client = client
        self.min_answerable = min_answerable
        self.max_len = max_len
        self.lang = lang
        self.max_evidenz_zeichen = max_evidenz_zeichen

    def entscheide(self, frage, nodes):
        evidenz = "\n\n".join(n.text for n in nodes)[: self.max_evidenz_zeichen]
        state = {"frage": frage, "evidenz": evidenz}
        try:
            antwort = self.client.entscheide(state, FRAGEN, model=MODELL, lang=self.lang, max_len=self.max_len)
            wahrscheinlichkeit = _lies_wahrscheinlichkeit(antwort)
        except JudgeFehler:
            return GateDecision(Outcome.ABSTAIN, (AbstainReason.JUDGE_UNAVAILABLE,), GateScores())
        _warne_bei_auffaelligkeiten(antwort)
        scores = GateScores(answerable=wahrscheinlichkeit)
        if wahrscheinlichkeit < self.min_answerable:
            return GateDecision(Outcome.ABSTAIN, (AbstainReason.NOT_ANSWERABLE,), scores)
        return GateDecision(Outcome.ANSWER, (), scores)


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


def _warne_bei_auffaelligkeiten(antwort):
    usage = antwort.get("usage")
    if isinstance(usage, dict) and usage.get("truncated") is True:
        log.warning("Laya hat den State gekürzt — Antwort beruht auf unvollständiger Evidenz")
    routing = antwort.get("routing")
    modell = routing.get("model") if isinstance(routing, dict) else None
    if modell is not None and modell != MODELL:
        log.warning("Laya hat mit Checkpoint %s statt %s geantwortet", modell, MODELL)


def warne_wenn_nicht_lokal(url):
    host = urlsplit(url).hostname
    if host not in LOKALE_HOSTS:
        log.warning("Laya-Server %s ist nicht lokal — Fragen und Evidenz verlassen diesen Rechner", host)


def baue_gate(einstellungen, transport=None):
    score_gate = RetrievalScoreGate(einstellungen.MIN_RETRIEVAL_SCORE)
    url = (einstellungen.LAYA_URL or "").strip()
    if not url:
        return score_gate
    warne_wenn_nicht_lokal(url)
    client = SystemOneClient(url, einstellungen.LAYA_API_KEY, einstellungen.LAYA_TIMEOUT, transport)
    laya = LayaGate(client, einstellungen.LAYA_MIN_ANSWERABLE, einstellungen.LAYA_MAX_LEN, einstellungen.LAYA_LANG)
    return Kaskade([score_gate, laya])
