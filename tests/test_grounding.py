import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import gate
import grounding
import metrics
from gate import AbstainReason, GateDecision, GateScores, Outcome
from grounding import (
    MAX_AUSSAGE_ZEICHEN,
    MAX_AUSSAGEN,
    SYSTEM_PROMPT,
    baue_kontext,
    pruefe_antwort,
    pruefe_antwort_und_eskaliere,
    zahlen_judge,
    zerlege,
)

ANSWER = GateDecision(Outcome.ANSWER, (), GateScores(relevance=0.8, answerable=0.9))


def node(text="Fundstelle", segment="verordnung"):
    return SimpleNamespace(score=0.8, text=text, metadata={"quelle": "dora.md", "segment": segment})


def nodes(k=3):
    return [node(f"Fundstelle {i}") for i in range(1, k + 1)]


def alles_stimmt(_aussage, _fundstellen):
    return True


class FakeJudge:
    def __init__(self, ergebnisse=()):
        self.ergebnisse = list(ergebnisse)
        self.aufrufe = []

    def __call__(self, aussage, fundstellen):
        self.aufrufe.append((aussage, fundstellen))
        return self.ergebnisse.pop(0) if self.ergebnisse else True


def fehlstellen(antwort, k=3):
    return pruefe_antwort(antwort, nodes(k), alles_stimmt).fehlstellen


@pytest.mark.parametrize(
    "antwort",
    [
        "Finanzunternehmen, z. B. Banken und Versicherungen, müssen Vorfälle melden [1].",
        "Das Leitungsorgan haftet, d. h. Verantwortung ist nicht delegierbar [2].",
        "Erfasst sind u. a. Zahlungsinstitute [1].",
        "Das gilt für Kreditinstitute bzw. Wertpapierfirmen [1].",
        "Die sog. Kritischen IKT-Drittdienstleister unterliegen der Aufsicht [3].",
        "Die Pflicht folgt aus Art. 5 Abs. 2 i. V. m. Art. 6 Abs. 1 [2].",
        "DORA gilt seit dem 17. Januar 2025 [1].",
        "Das Bußgeld beträgt bis zu 5 Mio. EUR [1].",
        "Veröffentlicht im ABl. L 333 vom 27.12.2022, S. 1 [1].",
        "Gemäß Art. 3 Nr. 8 ist ein IKT-Vorfall ein Ereignis [1].",
        "Die Verordnung (EU) 2022/2554 gilt unmittelbar [1]. Sie ergänzt die Richtlinie (EU) 2022/2555 [2].",
        "Die Frist beginnt mit Kenntnis. [1] Sie endet später. [2]",
        "Die Frist beginnt.[1] Sie endet.[2]",
        "Art. 5 regelt Governance [1, 2]. Art. 6 regelt den Rahmen [2][3].",
    ],
)
def test_konforme_antwort_wird_nicht_verworfen(antwort):
    assert pruefe_antwort(antwort, nodes(), alles_stimmt).bestanden


@pytest.mark.parametrize(
    "antwort",
    [
        "Folgende Pflichten bestehen:\n- Risikomanagement [1]\n- Meldung [2]",
        "| Pflicht | Frist |\n|---|---|\n| Erstmeldung | bald [1] |",
        (
            "**Zusammenfassung:**\n\nFinanzunternehmen müssen Folgendes sicherstellen:\n\n"
            "1. **Governance:**\n   * Das Leitungsorgan trägt die Verantwortung [1].\n"
            "2. **Meldung:** Vorfälle sind zu melden [2]."
        ),
        (
            "Art. 5 bestimmt die Aufgaben des Leitungsorgans [1], darunter:\n\n"
            "- h) die Billigung der Strategie [1].\n- i) die Überprüfung der Vereinbarungen [2].\n\n"
            "Quelle: Amtsblatt der Europäischen Union, L 333/52"
        ),
    ],
)
def test_struktur_wird_nicht_als_aussage_verworfen(antwort):
    befund = pruefe_antwort(antwort, nodes(), alles_stimmt)

    assert befund.bestanden, befund.fehlstellen


@pytest.mark.parametrize(
    ("antwort", "fehlend"),
    [
        ("Satz ohne Marker. Satz zwei hat einen [1].", "Satz ohne Marker."),
        ("Satz eins [1]. Satz zwei ohne Marker.", "Satz zwei ohne Marker."),
        ("Satz eins [1]. **Zweiter** Satz ohne Marker. Letzter [2].", "Zweiter Satz ohne Marker."),
        ("Satz [1].\n\n**Die Frist beträgt 72 Stunden**", "Die Frist beträgt 72 Stunden"),
        (
            "Art. 5 regelt Governance [1]. Zu Sanktionen enthält der Kontext keine Angaben.",
            "Zu Sanktionen enthält der Kontext keine Angaben.",
        ),
        ("Satz ohne Marker. Ähnliches gilt für Art. 6 [1].", "Satz ohne Marker."),
    ],
)
def test_aussage_ohne_marker_scheitert_und_wird_benannt(antwort, fehlend):
    befund = pruefe_antwort(antwort, nodes(), alles_stimmt)

    assert not befund.bestanden
    assert [(f.text, f.grund) for f in befund.fehlstellen] == [(fehlend, "kein_marker")]


@pytest.mark.parametrize("antwort", ["", "   \n", "# Titel", "---", "[1]."])
def test_antwort_ohne_aussage_besteht_nie(antwort):
    assert not pruefe_antwort(antwort, nodes(), alles_stimmt).bestanden


def test_doppelpunkt_ohne_folgende_liste_bleibt_aussage():
    assert zerlege("Die Frist beträgt:\n\nSehr lang [1].") == ["Die Frist beträgt:", "Sehr lang [1]."]


def test_zeile_mit_doppelpunkt_vor_tabelle_ist_einleitung():
    assert zerlege("Die Fristen:\n| a [1] |") == ["| a [1] |"]


def test_ueberschrift_ist_keine_aussage_wenn_inhalt_folgt():
    assert zerlege("# Titel\n**Fett:**\nSatz [1].") == ["Satz [1]."]


def test_lange_fette_zeile_ist_keine_ueberschrift():
    lang = "**" + "x" * 81 + "**"

    assert zerlege(f"{lang}\nSatz [1].") == ["x" * 81, "Satz [1]."]


def test_zitatzeile_wird_wie_text_behandelt():
    assert zerlege("> Satz eins [1]. Satz zwei [2].") == ["Satz eins [1].", "Satz zwei [2]."]


def test_fremde_abkuerzung_vor_grossbuchstabe_trennt_fail_closed():
    antwort = "Das regelt die Abk. Vorschrift [1]."

    assert [f.text for f in fehlstellen(antwort)] == ["Das regelt die Abk."]


@pytest.mark.parametrize(
    ("antwort", "marker_gueltig"),
    [("Satz [3].", True), ("Satz [4].", False), ("Satz [0].", False), ("Satz [1,9].", False)],
)
def test_marker_bereich_ist_eins_bis_k(antwort, marker_gueltig):
    gefunden = fehlstellen(antwort)

    assert not gefunden if marker_gueltig else [f.grund for f in gefunden] == ["ungueltiger_marker"]


def test_k_gleich_eins_begrenzt_auf_eins():
    assert [f.grund for f in fehlstellen("Satz [2].", k=1)] == ["ungueltiger_marker"]


@pytest.mark.parametrize("antwort", ["Satz (1).", "Satz [1-3].", "Satz [Fundstelle 1]."])
def test_fremde_markerformen_gelten_nicht(antwort):
    assert [f.grund for f in fehlstellen(antwort)] == ["kein_marker"]


def test_alle_fehlstellen_werden_gemeldet():
    gruende = [f.grund for f in fehlstellen("Eins. Zwei [9]. Drei [1].")]

    assert gruende == ["kein_marker", "ungueltiger_marker"]


def test_judge_wird_bei_marker_fehlern_nicht_gefragt():
    judge = FakeJudge()

    pruefe_antwort("Ohne Marker. Falsch [9].", nodes(), judge)

    assert judge.aufrufe == []


def test_judge_bekommt_aussage_ohne_marker_und_nur_zitierte_chunks():
    judge = FakeJudge()

    pruefe_antwort("Eine Aussage [1, 3].", nodes(), judge)

    assert judge.aufrufe == [("Eine Aussage.", "Fundstelle 1\n\nFundstelle 3")]


def test_judge_verneint_und_uebrige_aussagen_werden_weiter_geprueft():
    judge = FakeJudge([False, True])

    befund = pruefe_antwort("Eins [1]. Zwei [2].", nodes(), judge)

    assert [f.grund for f in befund.fehlstellen] == ["nicht_gestuetzt"]
    assert len(judge.aufrufe) == 2
    assert befund.aussagen_gesamt == 2


@pytest.mark.parametrize(
    ("aussage", "fundstelle"),
    [
        ("Das regelt Art. 30 Abs. 2.", "Artikel 30 Vertragsbestimmungen (2) Die Vereinbarungen"),
        ("Gilt seit dem 27. Dezember 2022.", "vom 27.12.2022"),
        ("Bis zu 22.000 EUR.", "bis zu 22 000 EUR"),
        ("Binnen 30 Tage.", "innerhalb von 30 Kalendertagen"),
        ("Eine 72-Stunden-Frist gilt.", "binnen 72 Stunden"),
        ("Das sind 50 %.", "mindestens 50 Prozent"),
        ("Das Leitungsorgan haftet.", "irgendein Text"),
        ("Die Frist beträgt 4 h.", "binnen 4 Stunden"),
        ("Die Frist beträgt 4 Std.", "binnen 4 Stunden"),
        ("Die Frist beträgt 4 Stunden.", "binnen 4 Std."),
        ("Bis zu 500 €.", "bis zu 500 EUR"),
        ("Der Anteil beträgt 3,5 %.", "mindestens 3,5 Prozent"),
        ("Der Wert beträgt 1,5.", "Der Wert beträgt 1.5."),
        ("Die Frist beträgt 5,0 Tage.", "Die Frist beträgt 5 Tage."),
        ("Der Wert beträgt 1,50.", "Der Wert beträgt 1,5."),
        ("Der Anteil beträgt 0,0 %.", "Der Anteil beträgt 0 %."),
        ("Die Frist beträgt 30 Min.", "binnen 30 Minuten"),
        ("Die Frist beträgt 30 Sek.", "binnen 30 Sekunden"),
    ],
)
def test_zahlen_judge_besteht(aussage, fundstelle):
    assert zahlen_judge(aussage, fundstelle)


@pytest.mark.parametrize(
    ("aussage", "fundstelle"),
    [
        ("Das steht in Art. 99.", "Artikel 30"),
        ("Es gilt ISO 27001.", "Es gelten anerkannte Standards."),
        ("Die Richtlinie 2022/2555 gilt.", "Die Verordnung 2022/2554 gilt."),
        ("Die Frist beträgt 4 Stunden.", "Absatz 4 sieht eine Meldung binnen 72 Stunden vor."),
        ("Binnen 30 Arbeitstagen.", "innerhalb von 30 Kalendertagen"),
        ("Die Frist beträgt 4 h.", "binnen 4 Tagen"),
        ("Bis zu 500 €.", "bis zu 500 Stück"),
        ("Der Schwellenwert beträgt 5,1.", "Der Schwellenwert beträgt 1,5."),
        ("Der Wert beträgt 10,5.", "Der Wert beträgt 10."),
        ("Gemäß Tz. 2.10.", "Gemäß Tz. 2.1."),
        ("Die Frist beträgt 30 Minuten.", "Die Frist beträgt 30 Tage."),
        ("Die Frist beträgt 30 Sekunden.", "Die Frist beträgt 30 Minuten."),
        ("Die Frist beträgt 1.5 Monate.", "Artikel 1: Die Frist beträgt 5 Monate."),
        ("Der Wert beträgt 3,5.", "Absatz 3 nennt Absatz 5."),
    ],
)
def test_zahlen_judge_scheitert(aussage, fundstelle):
    assert not zahlen_judge(aussage, fundstelle)


def test_standard_judge_prueft_nur_gegen_zitierte_chunks():
    quellen = [node("binnen 72 Stunden"), node("kein Treffer")]

    befund = pruefe_antwort("Die Frist beträgt 72 Stunden [2].", quellen)

    assert [f.grund for f in befund.fehlstellen] == ["nicht_gestuetzt"]


def test_baue_kontext_nummeriert_ab_eins_in_node_reihenfolge():
    assert baue_kontext([node("erst"), node("dann")]) == "[1] erst\n\n[2] dann"


def test_prompt_beispiel_besteht_die_eigene_pruefung():
    beispiel = next(z for z in SYSTEM_PROMPT.splitlines() if z.startswith("Beispiel: "))

    assert pruefe_antwort(beispiel.removeprefix("Beispiel: "), nodes(), alles_stimmt).bestanden


def test_prompt_enthaelt_keine_template_klammern():
    assert "{" not in SYSTEM_PROMPT
    assert "}" not in SYSTEM_PROMPT


def test_bestanden_liefert_vorherige_ohne_datensatz_und_zaehler(tmp_path, monkeypatch):
    monkeypatch.setattr(metrics, "METRIKEN", metrics.Metriken())
    ziel = tmp_path / "e.jsonl"

    pruefung = pruefe_antwort_und_eskaliere(ziel, "Frage?", nodes(), "Satz [1].", ANSWER)

    assert pruefung.entscheidung is ANSWER
    assert not pruefung.vorgemerkt
    assert not ziel.exists()
    assert metrics.METRIKEN.schnappschuss()["eskalationen_gesamt"] == 0


def test_verwurf_ist_abstain_mit_vorherigen_scores_und_einer_zeile(tmp_path, monkeypatch):
    monkeypatch.setattr(metrics, "METRIKEN", metrics.Metriken())
    ziel = tmp_path / "e.jsonl"

    pruefung = pruefe_antwort_und_eskaliere(ziel, "Frage?", nodes(), "Ohne Marker. Mit [1].", ANSWER)

    assert pruefung.verweigert
    assert pruefung.vorgemerkt
    assert pruefung.entscheidung.reasons == (AbstainReason.UNGROUNDED_CLAIM,)
    assert pruefung.entscheidung.scores == ANSWER.scores
    zeilen = ziel.read_text(encoding="utf-8").splitlines()
    assert len(zeilen) == 1
    satz = json.loads(zeilen[0])
    assert satz["reasons"] == ["UNGROUNDED_CLAIM"]
    assert satz["antwort"] == "Ohne Marker. Mit [1]."
    assert satz["aussagen_gesamt"] == 2
    assert satz["aussagen_ohne_beleg"] == [{"text": "Ohne Marker.", "grund": "kein_marker"}]
    assert metrics.METRIKEN.schnappschuss()["eskalationen_nach_reason"] == {"UNGROUNDED_CLAIM": 1}


def test_datensatz_begrenzt_antwort_und_fehlstellen(tmp_path):
    ziel = tmp_path / "e.jsonl"
    lang = " ".join(f"Satz{i} {'x' * 300}." for i in range(30))

    pruefe_antwort_und_eskaliere(ziel, "Frage?", nodes(), lang, ANSWER)

    satz = json.loads(ziel.read_text(encoding="utf-8"))
    assert len(satz["antwort"]) == gate.MAX_EVIDENZ_ZEICHEN
    assert satz["aussagen_gesamt"] == 30
    assert len(satz["aussagen_ohne_beleg"]) == MAX_AUSSAGEN
    assert all(len(f["text"]) == MAX_AUSSAGE_ZEICHEN for f in satz["aussagen_ohne_beleg"])


def test_schreibfehler_verwirft_trotzdem_ohne_vormerkung(tmp_path):
    (tmp_path / "datei").write_text("x")

    pruefung = pruefe_antwort_und_eskaliere(tmp_path / "datei" / "e.jsonl", "Frage?", nodes(), "Ohne.", ANSWER)

    assert pruefung.verweigert
    assert not pruefung.vorgemerkt


def test_grounding_importiert_nur_standardbibliothek_und_gate():
    baum = ast.parse(Path(grounding.__file__).read_text(encoding="utf-8"))
    importe = set()
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.Import):
            importe.update(a.name.split(".")[0] for a in knoten.names)
        elif isinstance(knoten, ast.ImportFrom):
            importe.add((knoten.module or "").split(".")[0])

    assert not importe & {"metrics", "httpx", "langchain", "langchain_core", "llama_index", "openai"}


def test_schluss_ohne_leerzeichen_zaehlt_als_abkuerzung_mit_punkten():
    assert zerlege("Banken, z.B. Sparkassen, melden [1].") == ["Banken, z.B. Sparkassen, melden [1]."]


def test_zeile_mit_mehreren_fetten_stellen_ist_keine_ueberschrift():
    assert zerlege("**Frist** beträgt 72 **Stunden**\n\nSatz [1].") == ["Frist beträgt 72 Stunden", "Satz [1]."]


@pytest.mark.parametrize(
    "fett",
    ["**Die Frist beträgt 72 Stunden.**", "**Die Frist beträgt 72 Stunden**", "# Die Frist beträgt 72 Stunden"],
)
def test_fette_aussage_vor_weiterer_aussage_wird_geprueft(fett):
    gefunden = fehlstellen(f"{fett}\nVorfälle müssen gemeldet werden [1].")

    assert [f.grund for f in gefunden] == ["kein_marker"]


def test_fette_beschriftung_mit_zahl_bleibt_ueberschrift():
    assert zerlege("**Art. 5:**\nSatz [1].") == ["Satz [1]."]


def test_einleitung_mit_zahl_vor_liste_wird_geprueft():
    gefunden = fehlstellen("Für die Meldung gilt eine Frist von 72 Stunden:\n- Vorfälle sind zu melden [1].")

    assert [(f.text, f.grund) for f in gefunden] == [("Für die Meldung gilt eine Frist von 72 Stunden:", "kein_marker")]


def test_einleitung_mit_marker_vor_liste_wird_gegen_die_fundstelle_geprueft():
    quellen = [node("Vorfälle sind zu melden.")]

    befund = pruefe_antwort("Es gilt eine Frist von 72 Stunden [1]:\n- Vorfälle sind zu melden [1].", quellen)

    assert [f.grund for f in befund.fehlstellen] == ["nicht_gestuetzt"]


@pytest.mark.parametrize(
    ("antwort", "fehlend"),
    [
        ("Satz ohne Marker. danach folgt Text [1].", "Satz ohne Marker."),
        ("Satz ohne Marker. 72 Stunden gelten [1].", "Satz ohne Marker."),
    ],
)
def test_satzgrenze_gilt_auch_vor_kleinbuchstabe_und_ziffer(antwort, fehlend):
    assert [f.text for f in fehlstellen(antwort)] == [fehlend]


@pytest.mark.parametrize("abkuerzung", ["bzgl.", "ff.", "max.", "mind.", "vs.", "Std.", "Tsd.", "Sek."])
def test_abkuerzung_vor_kleinbuchstabe_trennt_nicht(abkuerzung):
    antwort = f"Das gilt {abkuerzung} der Frist [1]."

    assert zerlege(antwort) == [antwort]


def test_umbruch_im_satz_bleibt_eine_aussage():
    assert zerlege("Finanzunternehmen müssen\nVorfälle melden [1].") == ["Finanzunternehmen müssen Vorfälle melden [1]."]


def test_umbruch_im_listenpunkt_bleibt_eine_aussage():
    assert zerlege("- Vorfälle sind\n  zu melden [1].\n- Nächster Punkt [2].") == [
        "Vorfälle sind zu melden [1].",
        "Nächster Punkt [2].",
    ]


@pytest.mark.parametrize(
    "antwort",
    ["Eins [1].\nZwei [2].", "Eins [1]\nZwei [2].", "**Fett**\nZwei [2].", "| a [1] |\nZwei [2]."],
)
def test_neue_zeile_nach_abgeschlossener_zeile_bleibt_getrennt(antwort):
    assert len(zerlege(antwort)) >= 1
    assert zerlege(antwort)[-1] == "Zwei [2]."
