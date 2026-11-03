"""Claim-Level-Grounding nach der Generierung (ADR 0014) — ohne schwere Imports, damit die CI es testen kann."""

import logging
import re
from itertools import pairwise
from typing import NamedTuple, Protocol

import gate
from gate import AbstainReason, GateDecision, Outcome, Pruefung

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
Beantworte die Frage NUR anhand des Kontexts. Der Kontext besteht aus nummerierten Fundstellen [1], [2] usw.
Jeder Satz und jeder Listenpunkt nennt direkt vor dem Satzzeichen die Nummern der Fundstellen, die ihn belegen.
Beispiel: Finanzunternehmen melden schwerwiegende Vorfälle [2]. Das Leitungsorgan trägt die Verantwortung [1][3].
Schreibe nur, was die Fundstellen belegen, und lass alles andere kommentarlos weg. Nenne Zahlen, Fristen und Artikel nur, wenn sie in der zitierten Fundstelle stehen.
Keine Überschriften, keine Quellenliste am Ende.
Belegt keine Fundstelle die Antwort, schreibe nur: Der Kontext reicht nicht aus."""

MARKER = re.compile(r"\[\s*\d+(?:\s*,\s*\d+)*\s*\]")
ABKUERZUNGEN = frozenset(
    {
        "abl", "abs", "art", "bspw", "buchst", "bzw", "ca", "dr", "erwgr", "etc", "evtl", "gem", "ggf",
        "inkl", "insb", "kap", "lit", "mio", "mrd", "nr", "prof", "rn", "sog", "tz", "ua", "usw", "vgl",
        "ziff", "zzgl",
    }
)
LISTENPUNKT = re.compile(r"^(?:[-*•]|\d+[.)]|[a-z]\)|\(\w{1,4}\))\s+")
QUELLENZEILE = re.compile(r"^(?:quellen?|fundstellen?)\s*:", re.IGNORECASE)
TRENNZEILE = re.compile(r"^[|:\-\s]+$")
NUR_FETT = re.compile(r"^(\*\*|__)(?:(?!\*\*|__).)+(\*\*|__):?$")
SATZENDE = re.compile(rf"[.!?][\"”“)]*(?P<nach>(?:\s*{MARKER.pattern})*)(?=\s|$)")
MARKER_AM_ENDE = re.compile(rf"{MARKER.pattern}\s*$")
MARKER_MIT_ABSTAND = re.compile(rf"\s*{MARKER.pattern}")
FORMATIERUNG = re.compile(r"\*\*|__|`")
MAX_UEBERSCHRIFT_ZEICHEN = 80

ZAHL = re.compile(r"\d+(?:/\d+)*")
TAUSENDER = re.compile(r"\b\d{1,3}(?:[ . ]\d{3})+\b")
MENGE = re.compile(
    r"(\d+(?:,\d+)?)[\s-]*(kalender|arbeits|werk|geschäfts)?(stunde|tag|woche|monat|jahr|prozent|%|eur|mio|mrd)",
    re.IGNORECASE,
)

KEIN_MARKER = "kein_marker"
UNGUELTIGER_MARKER = "ungueltiger_marker"
NICHT_GESTUETZT = "nicht_gestuetzt"

MAX_AUSSAGEN = 10
MAX_AUSSAGE_ZEICHEN = 200


class StuetzungsJudge(Protocol):
    def __call__(self, aussage: str, fundstellen: str) -> bool: ...


class Fehlstelle(NamedTuple):
    text: str
    grund: str


class Befund(NamedTuple):
    aussagen_gesamt: int
    fehlstellen: tuple[Fehlstelle, ...]

    @property
    def bestanden(self):
        return self.aussagen_gesamt > 0 and not self.fehlstellen


def baue_kontext(nodes):
    return "\n\n".join(f"[{i}] {n.text}" for i, n in enumerate(nodes, start=1))


def _ist_satzgrenze(vor, nach, rest):
    if nach or MARKER_AM_ENDE.search(vor):
        return True
    woerter = vor.split()
    wort = woerter[-1].strip("()").rsplit(".", 1)[-1] if woerter else ""
    if len(wort) == 1 or wort.lower() in ABKUERZUNGEN or (wort.isdigit() and len(wort) <= 2):
        return False
    return rest.lstrip("„\"('*")[:1].isupper()


def _saetze(text):
    saetze, start = [], 0
    for treffer in SATZENDE.finditer(text):
        rest = text[treffer.end() :].lstrip()
        if rest and _ist_satzgrenze(text[start : treffer.start()], treffer["nach"], rest):
            saetze.append(text[start : treffer.end()].strip())
            start = treffer.end()
    letzter = text[start:].strip()
    return [*saetze, letzter] if letzter else saetze


def _hat_inhalt(zeile):
    return any(c.isalnum() for c in MARKER.sub("", zeile))


def _ist_ueberschrift(zeile, rest):
    """Eine fette Zeile mit Satzzeichen oder Zahl ist Aussage, keine Überschrift; `**Art. 5:**` bleibt Beschriftung."""
    if len(rest) > MAX_UEBERSCHRIFT_ZEICHEN or not (zeile.startswith("#") or NUR_FETT.match(rest)):
        return False
    text = FORMATIERUNG.sub("", rest).rstrip()
    if text.endswith((".", "!", "?")):
        return False
    return text.endswith(":") or not any(c.isdigit() for c in text)


def _aussagen_der_zeile(zeile, folge, hat_nachfolger):
    if not _hat_inhalt(zeile) or QUELLENZEILE.match(zeile):
        return []
    if zeile.startswith("|"):
        return [] if TRENNZEILE.match(folge) else [FORMATIERUNG.sub("", zeile)]
    rest = LISTENPUNKT.sub("", zeile)
    if hat_nachfolger and _ist_ueberschrift(zeile, rest):
        return []
    saetze = _saetze(FORMATIERUNG.sub("", rest))
    if saetze and saetze[-1].endswith(":") and (LISTENPUNKT.match(folge) or folge.startswith("|")):
        saetze.pop()
    return saetze


def zerlege(antwort):
    """Aussage = Satz, Listenpunkt oder Tabellenzeile; Regeln und Belege in .specs/issue-29.md."""
    zeilen = [z for z in (roh.strip().removeprefix("> ").strip() for roh in antwort.splitlines()) if z]
    aussagen = []
    for zeile, folge in reversed(list(pairwise([*zeilen, ""]))):
        aussagen[:0] = _aussagen_der_zeile(zeile, folge, hat_nachfolger=bool(aussagen))
    return aussagen


def _zahlen_und_mengen(text):
    text = TAUSENDER.sub(lambda t: re.sub(r"\D", "", t[0]), text)
    mengen = {
        (zahl, praefix.lower(), "prozent" if stamm == "%" else stamm.lower())
        for zahl, praefix, stamm in MENGE.findall(text)
    }
    return set(ZAHL.findall(text)), mengen


def zahlen_judge(aussage, fundstellen):
    """Prüft nur Zahlen, Norm-Nummern und Zahl-Einheit-Paare; Aussagen ohne Zahl bleiben ungeprüft (ADR 0014)."""
    zahlen, mengen = _zahlen_und_mengen(aussage)
    belegte_zahlen, belegte_mengen = _zahlen_und_mengen(fundstellen)
    ohne_praefix = {(zahl, stamm) for zahl, _, stamm in belegte_mengen}
    return zahlen <= belegte_zahlen and all(
        (zahl, praefix, stamm) in belegte_mengen if praefix else (zahl, stamm) in ohne_praefix
        for zahl, praefix, stamm in mengen
    )


def _pruefe_aussage(aussage, nodes, judge):
    nummern = [int(n) for marker in MARKER.findall(aussage) for n in re.findall(r"\d+", marker)]
    if not nummern:
        return Fehlstelle(aussage, KEIN_MARKER)
    if not all(1 <= n <= len(nodes) for n in nummern):
        return Fehlstelle(aussage, UNGUELTIGER_MARKER)
    fundstellen = "\n\n".join(nodes[n - 1].text for n in nummern)
    if not judge(MARKER_MIT_ABSTAND.sub("", aussage), fundstellen):
        return Fehlstelle(aussage, NICHT_GESTUETZT)
    return None


def pruefe_antwort(antwort, nodes, judge=zahlen_judge):
    aussagen = zerlege(antwort)
    fehlstellen = tuple(f for a in aussagen if (f := _pruefe_aussage(a, nodes, judge)))
    return Befund(len(aussagen), fehlstellen)


def _datensatz_zusatz(antwort, befund):
    return {
        "antwort": antwort[: gate.MAX_EVIDENZ_ZEICHEN],
        "aussagen_gesamt": befund.aussagen_gesamt,
        "aussagen_ohne_beleg": [
            {"text": f.text[:MAX_AUSSAGE_ZEICHEN], "grund": f.grund} for f in befund.fehlstellen[:MAX_AUSSAGEN]
        ],
    }


def pruefe_antwort_und_eskaliere(eskalations_datei, frage, nodes, antwort, vorherige, grenzen=None, judge=zahlen_judge):
    befund = pruefe_antwort(antwort, nodes, judge)
    if befund.bestanden:
        return Pruefung(vorherige, False)
    entscheidung = GateDecision(Outcome.ABSTAIN, (AbstainReason.UNGROUNDED_CLAIM,), vorherige.scores)
    log.info(
        "Grounding: %s aussagen=%d fehlstellen=%d",
        AbstainReason.UNGROUNDED_CLAIM.value,
        befund.aussagen_gesamt,
        len(befund.fehlstellen),
    )
    gate.zaehle_verwurf(entscheidung, nodes)
    vorgemerkt = gate.schreibe_eskalation(
        eskalations_datei, frage, nodes, entscheidung, grenzen, zusatz=_datensatz_zusatz(antwort, befund)
    )
    return Pruefung(entscheidung, vorgemerkt)
