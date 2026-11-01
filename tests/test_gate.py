import json
import logging
import stat
import threading
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from gate import (
    ABSTAIN_ANTWORT_NICHT_VORGEMERKT,
    ABSTAIN_ANTWORT_VORGEMERKT,
    MAX_EVIDENZ_ZEICHEN,
    MAX_FRAGE_ZEICHEN,
    SEGMENTE,
    UNBEKANNT,
    AbstainReason,
    GateDecision,
    GateScores,
    Kaskade,
    Outcome,
    RetrievalScoreGate,
    abstain_antwort,
    aktiviere_score_log,
    entscheide_und_eskaliere,
    eskalationsdatensatz,
    kalibriere,
    normalisiere_segment,
    schreibe_eskalation,
    segment_von,
)


def node(score, text="Art. 5 DORA", quelle="dora.md"):
    return SimpleNamespace(score=score, text=text, metadata={"quelle": quelle})


def test_abstain_ohne_reason_ist_unzulaessig():
    with pytest.raises(ValueError):
        GateDecision(Outcome.ABSTAIN, (), GateScores(relevance=0.1))


def test_answer_mit_reason_ist_unzulaessig():
    with pytest.raises(ValueError):
        GateDecision(Outcome.ANSWER, (AbstainReason.LOW_RELEVANCE,), GateScores(relevance=0.9))


def test_reasons_decken_alle_fachlichen_gruende_ab():
    assert {r.name for r in AbstainReason} == {
        "LOW_RELEVANCE",
        "NOT_ANSWERABLE",
        "OUTDATED_SOURCE",
        "CONFLICTING_PROVISIONS",
        "UNGROUNDED_CLAIM",
        "JUDGE_UNAVAILABLE",
    }


def test_score_knapp_drunter_ist_abstain():
    d = RetrievalScoreGate(min_score=0.62).entscheide("f", [node(0.619)])

    assert d.outcome is Outcome.ABSTAIN
    assert d.reasons == (AbstainReason.LOW_RELEVANCE,)
    assert d.scores.relevance == 0.619


def test_score_genau_auf_schwelle_ist_answer():
    d = RetrievalScoreGate(min_score=0.62).entscheide("f", [node(0.62)])

    assert d.outcome is Outcome.ANSWER
    assert d.reasons == ()


def test_score_knapp_drueber_ist_answer():
    d = RetrievalScoreGate(min_score=0.62).entscheide("f", [node(0.621)])

    assert d.outcome is Outcome.ANSWER


def test_keine_treffer_ist_abstain_mit_reason():
    d = RetrievalScoreGate(min_score=0.62).entscheide("f", [])

    assert d.outcome is Outcome.ABSTAIN
    assert d.reasons == (AbstainReason.LOW_RELEVANCE,)
    assert d.scores.relevance is None


def test_nan_score_ist_abstain():
    """PR #43: NaN < min_score ist False; der Guard laesst NaN trotzdem nie durch (wie der Report-Guard)."""
    d = RetrievalScoreGate(min_score=0.62).entscheide("f", [node(float("nan"))])

    assert d.outcome is Outcome.ABSTAIN
    assert d.reasons == (AbstainReason.LOW_RELEVANCE,)


def test_bester_treffer_zaehlt_nicht_die_reihenfolge():
    d = RetrievalScoreGate(min_score=0.62).entscheide("f", [node(0.5), node(0.7)])

    assert d.outcome is Outcome.ANSWER
    assert d.scores.relevance == 0.7


def test_eskalationsdatensatz_enthaelt_frage_evidenz_scores_reasons_utc():
    jetzt = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
    d = GateDecision(Outcome.ABSTAIN, (AbstainReason.LOW_RELEVANCE,), GateScores(relevance=0.5))

    satz = eskalationsdatensatz("Frage?", [node(0.5, text="Text", quelle="q.md")], d, jetzt=jetzt)

    assert satz == {
        "zeitpunkt": "2026-10-27T12:00:00+00:00",
        "frage": "Frage?",
        "evidenz": [{"quelle": "q.md", "score": 0.5, "text": "Text"}],
        "scores": {"relevance": 0.5, "answerable": None},
        "reasons": ["LOW_RELEVANCE"],
        "segment": UNBEKANNT,
    }


def test_eskalationsdatensatz_verweigert_naive_zeit():
    d = GateDecision(Outcome.ABSTAIN, (AbstainReason.LOW_RELEVANCE,), GateScores())

    with pytest.raises(ValueError):
        eskalationsdatensatz("f", [], d, jetzt=datetime.fromisoformat("2026-10-27T12:00:00"))


def test_eskalationsdatensatz_nur_bei_abstain():
    d = GateDecision(Outcome.ANSWER, (), GateScores(relevance=0.9))

    with pytest.raises(ValueError):
        eskalationsdatensatz("f", [node(0.9)], d)


def test_schreibe_eskalation_haengt_jsonl_zeilen_an(tmp_path):
    ziel = tmp_path / "unterordner" / "eskalationen.jsonl"
    d = GateDecision(Outcome.ABSTAIN, (AbstainReason.LOW_RELEVANCE,), GateScores(relevance=0.4))

    schreibe_eskalation(ziel, "erste?", [node(0.4)], d)
    schreibe_eskalation(ziel, "zweite?", [], d)

    zeilen = [json.loads(z) for z in ziel.read_text(encoding="utf-8").splitlines()]
    assert [z["frage"] for z in zeilen] == ["erste?", "zweite?"]
    assert zeilen[1]["evidenz"] == []


def test_schreibfehler_wird_geloggt_und_nicht_geworfen(tmp_path, caplog):
    blockiert = tmp_path / "datei"
    blockiert.write_text("x")
    d = GateDecision(Outcome.ABSTAIN, (AbstainReason.LOW_RELEVANCE,), GateScores(relevance=0.4))

    geschrieben = schreibe_eskalation(blockiert / "eskalationen.jsonl", "f", [], d)

    assert geschrieben is False
    assert "Eskalationsdatensatz" in caplog.text


def test_schreibe_eskalation_meldet_erfolg(tmp_path):
    d = GateDecision(Outcome.ABSTAIN, (AbstainReason.LOW_RELEVANCE,), GateScores(relevance=0.4))

    assert schreibe_eskalation(tmp_path / "e.jsonl", "f", [node(0.4)], d) is True


def test_nicht_serialisierbare_metadaten_kippen_die_verweigerung_nicht(tmp_path):
    d = GateDecision(Outcome.ABSTAIN, (AbstainReason.LOW_RELEVANCE,), GateScores(relevance=0.4))
    kaputt = SimpleNamespace(score=0.4, text="t", metadata={"quelle": object()})

    assert schreibe_eskalation(tmp_path / "e.jsonl", "f", [kaputt], d) is False


def test_eskalationsdatei_ist_nur_fuer_den_besitzer_lesbar(tmp_path):
    ziel = tmp_path / "e.jsonl"
    d = GateDecision(Outcome.ABSTAIN, (AbstainReason.LOW_RELEVANCE,), GateScores(relevance=0.4))

    schreibe_eskalation(ziel, "f", [], d)

    assert stat.S_IMODE(ziel.stat().st_mode) == 0o600


def test_parallele_schreiber_verschachteln_keine_zeilen(tmp_path):
    ziel = tmp_path / "e.jsonl"
    d = GateDecision(Outcome.ABSTAIN, (AbstainReason.LOW_RELEVANCE,), GateScores(relevance=0.4))
    lange_evidenz = [node(0.4, text="x" * MAX_EVIDENZ_ZEICHEN) for _ in range(3)]

    def schreiber():
        for _ in range(25):
            schreibe_eskalation(ziel, "f", lange_evidenz, d)

    threads = [threading.Thread(target=schreiber) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    zeilen = ziel.read_text(encoding="utf-8").splitlines()
    assert len(zeilen) == 200
    assert all(json.loads(z)["frage"] == "f" for z in zeilen)


def test_frage_und_evidenz_werden_im_datensatz_begrenzt():
    d = GateDecision(Outcome.ABSTAIN, (AbstainReason.LOW_RELEVANCE,), GateScores(relevance=0.4))
    langer_text = node(0.4, text="y" * (MAX_EVIDENZ_ZEICHEN * 3))

    satz = eskalationsdatensatz("q" * (MAX_FRAGE_ZEICHEN * 3), [langer_text], d)

    assert len(satz["frage"]) == MAX_FRAGE_ZEICHEN
    assert len(satz["evidenz"][0]["text"]) == MAX_EVIDENZ_ZEICHEN


def test_treffer_ohne_quelle_liefert_none():
    d = GateDecision(Outcome.ABSTAIN, (AbstainReason.LOW_RELEVANCE,), GateScores(relevance=0.4))
    ohne = SimpleNamespace(score=0.4, text="t", metadata={})

    assert eskalationsdatensatz("f", [ohne], d)["evidenz"][0]["quelle"] is None


def test_answer_schreibt_keinen_datensatz(tmp_path):
    ziel = tmp_path / "e.jsonl"

    pruefung = entscheide_und_eskaliere(RetrievalScoreGate(0.62), ziel, "f", [node(0.7)])

    assert not pruefung.verweigert
    assert pruefung.vorgemerkt is False
    assert not ziel.exists()


def test_abstain_wird_vorgemerkt_wenn_schreiben_gelingt(tmp_path):
    ziel = tmp_path / "e.jsonl"

    pruefung = entscheide_und_eskaliere(RetrievalScoreGate(0.62), ziel, "f", [node(0.5)])

    assert pruefung.verweigert
    assert pruefung.vorgemerkt is True
    assert ziel.exists()


def test_abstain_wird_nicht_als_vorgemerkt_gemeldet_wenn_schreiben_scheitert(tmp_path):
    blockiert = tmp_path / "datei"
    blockiert.write_text("x")

    pruefung = entscheide_und_eskaliere(RetrievalScoreGate(0.62), blockiert / "e.jsonl", "f", [node(0.5)])

    assert pruefung.verweigert
    assert pruefung.vorgemerkt is False


def test_abstain_antwort_verspricht_vormerkung_nur_wenn_sie_stattfand():
    assert abstain_antwort(True) == ABSTAIN_ANTWORT_VORGEMERKT
    assert abstain_antwort(False) == ABSTAIN_ANTWORT_NICHT_VORGEMERKT
    assert "vorgemerkt" not in ABSTAIN_ANTWORT_NICHT_VORGEMERKT


def test_judge_unavailable_ist_ein_eigener_grund():
    assert AbstainReason.JUDGE_UNAVAILABLE.value == "JUDGE_UNAVAILABLE"


class FesterGate:
    def __init__(self, entscheidung):
        self.entscheidung = entscheidung
        self.aufrufe = 0

    def entscheide(self, frage, nodes):
        self.aufrufe += 1
        return self.entscheidung


def antwort_entscheidung(**scores):
    return GateDecision(Outcome.ANSWER, (), GateScores(**scores))


def abstain_entscheidung(reason, **scores):
    return GateDecision(Outcome.ABSTAIN, (reason,), GateScores(**scores))


def test_kaskade_ohne_gates_ist_unzulaessig():
    with pytest.raises(ValueError):
        Kaskade([])


def test_kaskade_fuehrt_scores_zusammen_wenn_alle_zustimmen():
    kaskade = Kaskade([FesterGate(antwort_entscheidung(relevance=0.8)), FesterGate(antwort_entscheidung(answerable=0.7))])

    d = kaskade.entscheide("f", [])

    assert d.outcome is Outcome.ANSWER
    assert d.scores == GateScores(relevance=0.8, answerable=0.7)


def test_kaskade_bricht_beim_ersten_abstain_ab_und_ruft_spaetere_gates_nicht():
    spaeter = FesterGate(antwort_entscheidung(answerable=0.9))
    kaskade = Kaskade([FesterGate(abstain_entscheidung(AbstainReason.LOW_RELEVANCE, relevance=0.3)), spaeter])

    d = kaskade.entscheide("f", [])

    assert d.reasons == (AbstainReason.LOW_RELEVANCE,)
    assert d.scores == GateScores(relevance=0.3, answerable=None)
    assert spaeter.aufrufe == 0


def test_kaskade_zweiter_gate_verweigert_mit_scores_beider():
    kaskade = Kaskade(
        [FesterGate(antwort_entscheidung(relevance=0.8)), FesterGate(abstain_entscheidung(AbstainReason.NOT_ANSWERABLE, answerable=0.2))]
    )

    d = kaskade.entscheide("f", [])

    assert d.reasons == (AbstainReason.NOT_ANSWERABLE,)
    assert d.scores == GateScores(relevance=0.8, answerable=0.2)


def test_kaskade_frueherer_gate_gewinnt_bei_gleichem_score_feld():
    kaskade = Kaskade([FesterGate(antwort_entscheidung(relevance=0.8)), FesterGate(antwort_entscheidung(relevance=0.1))])

    assert kaskade.entscheide("f", []).scores.relevance == 0.8


def test_scores_werden_pro_entscheidung_geloggt_auch_bei_answer(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="gate")

    entscheide_und_eskaliere(RetrievalScoreGate(0.62), tmp_path / "e.jsonl", "f", [node(0.7)])

    assert "ANSWER" in caplog.text
    assert "relevance=0.7" in caplog.text


def test_kaskade_weist_leeren_generator_zurueck():
    with pytest.raises(ValueError):
        Kaskade(g for g in [])


def test_abstain_antwort_nennt_judge_ausfall_statt_fehlender_grundlage():
    for vorgemerkt in (True, False):
        text = abstain_antwort(vorgemerkt, (AbstainReason.JUDGE_UNAVAILABLE,))
        assert "nicht verfügbar" in text
        assert "Keine belastbare Grundlage" not in text
        assert ("vorgemerkt" in text) is vorgemerkt


def test_abstain_antwort_bei_anderen_gruenden_bleibt_unveraendert():
    assert abstain_antwort(True, (AbstainReason.NOT_ANSWERABLE,)) == ABSTAIN_ANTWORT_VORGEMERKT


def test_aktiviere_score_log_macht_info_sichtbar_und_ist_idempotent():
    logger = logging.getLogger("gate")
    vorher = (list(logger.handlers), logger.level)
    try:
        logger.handlers.clear()
        aktiviere_score_log()
        aktiviere_score_log()
        assert logger.isEnabledFor(logging.INFO)
        assert len(logger.handlers) == 1
    finally:
        logger.handlers[:], logger.level = vorher


@pytest.mark.parametrize(
    ("wert", "erwartet"),
    [("Verordnung ", "verordnung"), ("gesetz", "gesetz"), ("foo", UNBEKANNT), (None, UNBEKANNT), ("", UNBEKANNT), (5, UNBEKANNT)],
)
def test_normalisiere_segment(wert, erwartet):
    assert normalisiere_segment(wert) == erwartet


def test_unbekannt_ist_kein_segment_und_vokabular_ist_fest():
    assert SEGMENTE == ("gesetz", "verordnung", "richtlinie", "leitlinie", "auslegung")
    assert UNBEKANNT not in SEGMENTE


def test_normalisiere_segment_warnt_bei_unbekanntem_nicht_leerem_wert(caplog):
    with caplog.at_level(logging.WARNING, logger="gate"):
        normalisiere_segment("foo", "dora.md")
    assert "dora.md" in caplog.text
    assert "foo" in caplog.text


def segment_node(segment):
    metadata = {} if segment is None else {"segment": segment}
    return SimpleNamespace(metadata=metadata, score=0.9, text="Art. 5")


def test_segment_von_einheitlich():
    assert segment_von([segment_node("verordnung"), segment_node("verordnung")]) == "verordnung"


def test_segment_von_gemischt_leer_und_ohne_feld_ist_unbekannt():
    assert segment_von([segment_node("verordnung"), segment_node("gesetz")]) == UNBEKANNT
    assert segment_von([]) == UNBEKANNT
    assert segment_von([segment_node("verordnung"), segment_node(None)]) == UNBEKANNT
    assert segment_von([segment_node(UNBEKANNT)]) == UNBEKANNT


def test_kalibriere_identitaet_bei_t1_und_clip():
    assert kalibriere(0.3, 1.0) == pytest.approx(0.3)
    assert 0 < kalibriere(0.0, 1.0) < 1
    assert 0 < kalibriere(1.0, 1.0) < 1
    assert kalibriere(0.8, 2.0) < 0.8


def test_eskalationsdatensatz_enthaelt_segment():
    d = GateDecision(Outcome.ABSTAIN, (AbstainReason.LOW_RELEVANCE,), GateScores(relevance=0.5))

    assert eskalationsdatensatz("F?", [segment_node("gesetz")], d)["segment"] == "gesetz"
    assert eskalationsdatensatz("F?", [segment_node(None)], d)["segment"] == UNBEKANNT
