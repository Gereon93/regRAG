import json
import logging
import os
import stat
import threading
from datetime import date, datetime, timedelta, timezone
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
    EskalationsGrenzen,
    FassungsGate,
    GateDecision,
    GateScores,
    GueltigkeitsGate,
    Kaskade,
    Outcome,
    RetrievalScoreGate,
    abstain_antwort,
    aktiviere_score_log,
    bereinige_eskalationen,
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
        "AMBIGUOUS_VERSION",
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
        "stichtag": "2026-10-27",
        "frage": "Frage?",
        "evidenz": [{"quelle": "q.md", "score": 0.5, "text": "Text", "gueltigkeit": "unbekannt"}],
        "scores": {"relevance": 0.5, "answerable": None, "konflikt": None},
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


def _abstain():
    return GateDecision(Outcome.ABSTAIN, (AbstainReason.LOW_RELEVANCE,), GateScores(relevance=0.4))


def _archive(ziel):
    return sorted(p.name for p in ziel.parent.glob(f"{ziel.name}.*"))


def test_datei_wird_bei_maximalgroesse_rotiert(tmp_path):
    ziel = tmp_path / "eskalationen.jsonl"
    grenzen = EskalationsGrenzen(max_bytes=600, max_archive=5, aufbewahrung_tage=30)

    for i in range(6):
        assert schreibe_eskalation(ziel, f"frage {i}", [], _abstain(), grenzen=grenzen)

    assert _archive(ziel)
    assert ziel.stat().st_size <= 600 + 500  # eine Zeile darf die Grenze überschreiten, danach wird rotiert
    gesamt = ziel.read_text(encoding="utf-8") + "".join(
        (tmp_path / n).read_text(encoding="utf-8") for n in _archive(ziel)
    )
    assert gesamt.count("\n") == 6


def test_aktive_datei_bleibt_nach_rotation_0600(tmp_path):
    ziel = tmp_path / "eskalationen.jsonl"
    grenzen = EskalationsGrenzen(max_bytes=1, max_archive=5, aufbewahrung_tage=30)

    schreibe_eskalation(ziel, "a", [], _abstain(), grenzen=grenzen)
    schreibe_eskalation(ziel, "b", [], _abstain(), grenzen=grenzen)

    assert stat.S_IMODE(ziel.stat().st_mode) == 0o600
    assert [stat.S_IMODE((tmp_path / n).stat().st_mode) for n in _archive(ziel)] == [0o600]


def test_archivzahl_ist_begrenzt_aelteste_fliegt_zuerst(tmp_path):
    ziel = tmp_path / "eskalationen.jsonl"
    grenzen = EskalationsGrenzen(max_bytes=1, max_archive=2, aufbewahrung_tage=30)

    for i in range(6):
        schreibe_eskalation(ziel, f"frage {i}", [], _abstain(), grenzen=grenzen)

    assert len(_archive(ziel)) == 2
    erhalten = "".join((tmp_path / n).read_text(encoding="utf-8") for n in _archive(ziel))
    assert "frage 4" in erhalten
    assert "frage 0" not in erhalten


def test_archive_nach_aufbewahrungsfrist_werden_geloescht(tmp_path):
    ziel = tmp_path / "eskalationen.jsonl"
    alt = tmp_path / "eskalationen.jsonl.20200101T000000000000Z.1"
    alt.write_text("{}\n")
    vor_60_tagen = (datetime.now(timezone.utc) - timedelta(days=60)).timestamp()
    os.utime(alt, (vor_60_tagen, vor_60_tagen))
    grenzen = EskalationsGrenzen(max_bytes=10_000, max_archive=5, aufbewahrung_tage=30)

    schreibe_eskalation(ziel, "neu", [], _abstain(), grenzen=grenzen)

    assert not alt.exists()


def test_aktive_datei_aelter_als_frist_wird_rotiert_und_geloescht(tmp_path):
    ziel = tmp_path / "eskalationen.jsonl"
    alt = datetime.now(timezone.utc) - timedelta(days=40)
    satz = {"zeitpunkt": alt.isoformat(), "frage": "alt"}
    ziel.write_text(json.dumps(satz) + "\n", encoding="utf-8")
    os.utime(ziel, (alt.timestamp(), alt.timestamp()))
    grenzen = EskalationsGrenzen(max_bytes=10_000, max_archive=5, aufbewahrung_tage=30)

    schreibe_eskalation(ziel, "neu", [], _abstain(), grenzen=grenzen)

    inhalt = ziel.read_text(encoding="utf-8")
    assert "neu" in inhalt
    assert "alt" not in inhalt
    assert _archive(ziel) == []


def test_rotationsfehler_kippt_das_schreiben_nicht(tmp_path, monkeypatch, caplog):
    ziel = tmp_path / "eskalationen.jsonl"
    ziel.write_text("x" * 100)
    grenzen = EskalationsGrenzen(max_bytes=10, max_archive=5, aufbewahrung_tage=30)

    def kaputt(*a, **k):
        raise OSError("kein Zugriff")

    monkeypatch.setattr(os, "replace", kaputt)

    with caplog.at_level(logging.ERROR):
        assert schreibe_eskalation(ziel, "f", [], _abstain(), grenzen=grenzen) is True

    assert "Rotation" in caplog.text


def test_grenzen_validieren_ihre_werte():
    for kaputt in ({"max_bytes": 0}, {"max_archive": -1}, {"aufbewahrung_tage": 0}):
        with pytest.raises(ValueError):
            EskalationsGrenzen(**kaputt)


def _archivname(ziel, tage_alt=0, pid=1):
    zeit = datetime.now(timezone.utc) - timedelta(days=tage_alt)
    return ziel.with_name(f"{ziel.name}.{zeit:%Y%m%dT%H%M%S%fZ}.{pid}")


def _zeile(tage_alt, naiv=False):
    zeit = datetime.now(timezone.utc) - timedelta(days=tage_alt)
    if naiv:
        zeit = zeit.replace(tzinfo=None)
    return json.dumps({"zeitpunkt": zeit.isoformat(), "frage": "x"}) + "\n"


def test_zeitstempel_ohne_zeitzone_verhindert_den_append_nicht(tmp_path):
    ziel = tmp_path / "eskalationen.jsonl"
    ziel.write_text(_zeile(1, naiv=True), encoding="utf-8")
    grenzen = EskalationsGrenzen(max_bytes=10_000, max_archive=5, aufbewahrung_tage=30)

    assert schreibe_eskalation(ziel, "neu", [], _abstain(), grenzen=grenzen) is True
    assert schreibe_eskalation(ziel, "neu2", [], _abstain(), grenzen=grenzen) is True


def test_zeitstempel_ohne_zeitzone_gilt_als_utc_und_wird_nach_frist_rotiert(tmp_path):
    ziel = tmp_path / "eskalationen.jsonl"
    ziel.write_text(_zeile(40, naiv=True), encoding="utf-8")
    grenzen = EskalationsGrenzen(max_bytes=10_000, max_archive=5, aufbewahrung_tage=30)

    schreibe_eskalation(ziel, "neu", [], _abstain(), grenzen=grenzen)

    assert _archive(ziel) == []
    assert "neu" in ziel.read_text(encoding="utf-8")


def test_fremde_dateien_zaehlen_nicht_als_archiv(tmp_path):
    ziel = tmp_path / "eskalationen.jsonl"
    fremd = tmp_path / "eskalationen.jsonl.bak"
    fremd.write_text("sicher")
    grenzen = EskalationsGrenzen(max_bytes=1, max_archive=1, aufbewahrung_tage=30)

    for i in range(4):
        schreibe_eskalation(ziel, f"f{i}", [], _abstain(), grenzen=grenzen)

    assert fremd.read_text() == "sicher"
    assert len([n for n in _archive(ziel) if not n.endswith(".bak")]) == 1


def test_sonderdatei_wird_nie_rotiert(tmp_path, monkeypatch):
    grenzen = EskalationsGrenzen(max_bytes=1, max_archive=0, aufbewahrung_tage=1)
    aufrufe = []
    monkeypatch.setattr(os, "replace", lambda *a, **k: aufrufe.append(a))

    assert schreibe_eskalation(os.devnull, "f", [], _abstain(), grenzen=grenzen) is True
    assert aufrufe == []


def test_parallele_threads_verlieren_keine_zeile(tmp_path):
    ziel = tmp_path / "eskalationen.jsonl"
    grenzen = EskalationsGrenzen(max_bytes=2000, max_archive=1000, aufbewahrung_tage=30)

    def lauf():
        for i in range(50):
            assert schreibe_eskalation(ziel, f"f{i}", [], _abstain(), grenzen=grenzen)

    threads = [threading.Thread(target=lauf) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    gesamt = ziel.read_text(encoding="utf-8").count("\n") + sum(
        (tmp_path / n).read_text(encoding="utf-8").count("\n") for n in _archive(ziel)
    )
    assert gesamt == 400


def test_verdraengung_ueber_max_archive_loggt_warnung(tmp_path, caplog):
    ziel = tmp_path / "eskalationen.jsonl"
    grenzen = EskalationsGrenzen(max_bytes=1, max_archive=1, aufbewahrung_tage=30)

    with caplog.at_level(logging.WARNING):
        for i in range(4):
            schreibe_eskalation(ziel, f"f{i}", [], _abstain(), grenzen=grenzen)

    assert "verdrängt" in caplog.text


def test_aktive_datei_zwischen_halber_und_voller_frist_wird_rotiert_und_bleibt(tmp_path):
    ziel = tmp_path / "eskalationen.jsonl"
    ziel.write_text(_zeile(20), encoding="utf-8")
    grenzen = EskalationsGrenzen(max_bytes=10_000, max_archive=5, aufbewahrung_tage=30)

    schreibe_eskalation(ziel, "neu", [], _abstain(), grenzen=grenzen)

    assert len(_archive(ziel)) == 1
    assert "neu" in ziel.read_text(encoding="utf-8")


def test_bereinigung_ohne_append_loescht_ueberfaelliges_archiv(tmp_path):
    ziel = tmp_path / "eskalationen.jsonl"
    alt = _archivname(ziel, tage_alt=40)
    alt.write_text(_zeile(40), encoding="utf-8")
    jung = _archivname(ziel, tage_alt=1, pid=2)
    jung.write_text(_zeile(1), encoding="utf-8")

    bereinige_eskalationen(ziel, EskalationsGrenzen(max_bytes=10_000, max_archive=5, aufbewahrung_tage=30))

    assert not alt.exists()
    assert jung.exists()


def test_dateiname_mit_glob_zeichen_wird_woertlich_behandelt(tmp_path):
    ziel = tmp_path / "eskalationen[prod].jsonl"
    grenzen = EskalationsGrenzen(max_bytes=1, max_archive=1, aufbewahrung_tage=30)

    for i in range(5):
        schreibe_eskalation(ziel, f"f{i}", [], _abstain(), grenzen=grenzen)

    archive = [p for p in tmp_path.iterdir() if p != ziel]
    assert len(archive) == 1


def test_verzeichnis_mit_archivnamen_bricht_die_bereinigung_nicht_ab(tmp_path):
    ziel = tmp_path / "eskalationen.jsonl"
    (_archivname(ziel, tage_alt=1)).mkdir()
    alt = _archivname(ziel, tage_alt=40, pid=2)
    alt.write_text(_zeile(40), encoding="utf-8")

    bereinige_eskalationen(ziel, EskalationsGrenzen(max_bytes=10_000, max_archive=5, aufbewahrung_tage=30))

    assert not alt.exists()


JETZT = datetime(2026, 11, 4, tzinfo=timezone.utc)


def test_zusatz_wird_additiv_in_den_datensatz_gemischt():
    entscheidung = GateDecision(Outcome.ABSTAIN, (AbstainReason.UNGROUNDED_CLAIM,), GateScores())
    ohne = eskalationsdatensatz("F", [], entscheidung, JETZT)

    mit = eskalationsdatensatz("F", [], entscheidung, JETZT, zusatz={"antwort": "A"})

    assert mit == {**ohne, "antwort": "A"}


def test_ohne_zusatz_hat_der_datensatz_keine_grounding_felder():
    entscheidung = GateDecision(Outcome.ABSTAIN, (AbstainReason.LOW_RELEVANCE,), GateScores())

    satz = eskalationsdatensatz("F", [], entscheidung, JETZT)

    assert set(satz) == {"zeitpunkt", "stichtag", "frage", "evidenz", "scores", "reasons", "segment"}


def test_schreibe_eskalation_reicht_zusatz_durch(tmp_path):
    ziel = tmp_path / "e.jsonl"
    entscheidung = GateDecision(Outcome.ABSTAIN, (AbstainReason.UNGROUNDED_CLAIM,), GateScores())

    assert schreibe_eskalation(ziel, "F", [], entscheidung, zusatz={"antwort": "A"})

    assert json.loads(ziel.read_text(encoding="utf-8"))["antwort"] == "A"


def test_zaehle_verwurf_kippt_bei_zaehlfehler_nichts(monkeypatch):
    import gate as gate_modul
    import metrics

    def kaputt(*a):
        raise RuntimeError("kaputt")

    monkeypatch.setattr(metrics.METRIKEN, "zaehle_verworfen", kaputt)
    entscheidung = GateDecision(Outcome.ABSTAIN, (AbstainReason.UNGROUNDED_CLAIM,), GateScores())

    gate_modul.zaehle_verwurf(entscheidung, [])


STICHTAG = date(2026, 6, 15)


def fassung(score=0.9, quelle="dora", file_name="a.md", segment=None, **metadaten):
    metadata = {"quelle": quelle, "file_name": file_name, **metadaten}
    if segment:
        metadata["segment"] = segment
    return SimpleNamespace(score=score, text=f"Text {file_name}", metadata=metadata)


ABGELAUFEN = {"gueltig_bis": "2026-06-14"}
GUELTIG = {"gueltig_ab": "2026-01-01"}


def gueltigkeits_gate():
    return GueltigkeitsGate(uhr=lambda: STICHTAG)


def test_nur_nutzbare_treffer_engen_nicht_ein():
    nodes = [fassung(**GUELTIG), fassung(file_name="b.md")]

    entscheidung = gueltigkeits_gate().entscheide("f", nodes)

    assert entscheidung.outcome is Outcome.ANSWER
    assert entscheidung.evidenz is None


def test_nur_abgelaufene_treffer_sind_outdated():
    entscheidung = gueltigkeits_gate().entscheide("f", [fassung(**ABGELAUFEN)])

    assert entscheidung.reasons == (AbstainReason.OUTDATED_SOURCE,)
    assert entscheidung.evidenz is None


def test_unbekannt_ersetzt_keine_gueltige_fassung():
    entscheidung = gueltigkeits_gate().entscheide("f", [fassung(**ABGELAUFEN), fassung(file_name="b.md")])

    assert entscheidung.reasons == (AbstainReason.OUTDATED_SOURCE,)


def test_mischfall_engt_auf_gueltig_und_unbekannt_ein():
    alt, neu, ohne = fassung(**ABGELAUFEN), fassung(file_name="b.md", **GUELTIG), fassung(file_name="c.md")

    entscheidung = gueltigkeits_gate().entscheide("f", [alt, neu, ohne])

    assert entscheidung.outcome is Outcome.ANSWER
    assert entscheidung.evidenz == (neu, ohne)


@pytest.mark.parametrize("metadaten", [{"gueltig_ab": "2026-06-16"}, {"gueltig_ab": "kaputt"}])
def test_noch_nicht_gueltig_und_kaputte_metadaten_sind_outdated(metadaten):
    entscheidung = gueltigkeits_gate().entscheide("f", [fassung(**metadaten)])

    assert entscheidung.reasons == (AbstainReason.OUTDATED_SOURCE,)


def test_uhr_wird_pro_entscheidung_genau_einmal_gefragt():
    aufrufe = []

    def uhr():
        aufrufe.append(1)
        return STICHTAG

    GueltigkeitsGate(uhr=uhr).entscheide("f", [fassung(**GUELTIG), fassung(**ABGELAUFEN)])

    assert len(aufrufe) == 1


def test_dieselbe_instanz_sieht_den_tageswechsel():
    tage = iter([date(2026, 6, 14), date(2026, 6, 15)])
    gate = GueltigkeitsGate(uhr=lambda: next(tage))
    nodes = [fassung(gueltig_bis="2026-06-14")]

    assert gate.entscheide("f", nodes).outcome is Outcome.ANSWER
    assert gate.entscheide("f", nodes).outcome is Outcome.ABSTAIN


class SiehtNodes:
    def __init__(self, entscheidung):
        self.entscheidung = entscheidung
        self.gesehen = None

    def entscheide(self, frage, nodes):
        self.gesehen = nodes
        return self.entscheidung


ANTWORT = GateDecision(Outcome.ANSWER, (), GateScores())


def test_folgegate_sieht_die_eingeengten_nodes():
    alt, neu = fassung(**ABGELAUFEN), fassung(file_name="b.md", **GUELTIG)
    folge = SiehtNodes(ANTWORT)

    entscheidung = Kaskade([gueltigkeits_gate(), folge]).entscheide("f", [alt, neu])

    assert folge.gesehen == (neu,)
    assert entscheidung.evidenz == (neu,)


def test_evidenz_bleibt_bei_spaeterem_abstain_erhalten():
    alt, neu = fassung(**ABGELAUFEN), fassung(file_name="b.md", **GUELTIG)
    nein = GateDecision(Outcome.ABSTAIN, (AbstainReason.NOT_ANSWERABLE,), GateScores())

    entscheidung = Kaskade([gueltigkeits_gate(), SiehtNodes(nein)]).entscheide("f", [alt, neu])

    assert entscheidung.verweigert
    assert entscheidung.evidenz == (neu,)


def test_kaskade_ohne_einengung_hat_keine_evidenz():
    assert Kaskade([SiehtNodes(ANTWORT)]).entscheide("f", [fassung()]).evidenz is None


def test_konflikt_score_ueberlebt_die_kaskade():
    mit = GateDecision(Outcome.ANSWER, (), GateScores(konflikt=0.3))

    assert Kaskade([SiehtNodes(mit), SiehtNodes(ANTWORT)]).entscheide("f", []).scores.konflikt == 0.3


def test_genutzte_nodes_folgt_der_evidenz():
    nodes = [fassung()]

    assert GateDecision(Outcome.ANSWER, (), GateScores()).genutzte_nodes(nodes) == nodes
    assert GateDecision(Outcome.ANSWER, (), GateScores(), ()).genutzte_nodes(nodes) == []


def echte_kaskade():
    score = RetrievalScoreGate(0.62)
    return Kaskade([score, gueltigkeits_gate(), score, FassungsGate()])


def test_themenfremd_und_abgelaufen_meldet_low_relevance():
    entscheidung = echte_kaskade().entscheide("f", [fassung(score=0.3, **ABGELAUFEN)])

    assert entscheidung.reasons == (AbstainReason.LOW_RELEVANCE,)


def test_hoher_score_nur_auf_abgelaufenem_winkt_schwache_gueltige_nicht_durch():
    nodes = [fassung(score=0.9, **ABGELAUFEN), fassung(score=0.3, file_name="b.md", **GUELTIG)]

    entscheidung = echte_kaskade().entscheide("f", nodes)

    assert entscheidung.reasons == (AbstainReason.LOW_RELEVANCE,)
    assert entscheidung.scores.relevance == 0.9


@pytest.mark.parametrize(
    "nodes",
    [
        [fassung(gueltig_ab="2026-01-01"), fassung(file_name="a.md", gueltig_ab="2026-01-01")],
        [fassung(gueltig_ab="2026-01-01"), fassung(file_name="b.md", gueltig_ab="2026-01-01")],
        [fassung(), fassung(file_name="b.md")],
        [fassung(gueltig_ab="2026-01-01"), fassung(quelle="nis2", file_name="b.md", gueltig_ab="2025-01-01")],
    ],
    ids=["ein_dokument", "gleiche_angaben_zwei_dateien", "ohne_angaben", "verschiedene_quellen"],
)
def test_keine_mehrdeutige_fassung(nodes):
    assert FassungsGate().entscheide("f", nodes).outcome is Outcome.ANSWER


@pytest.mark.parametrize(
    "nodes",
    [
        [fassung(gueltig_ab="2025-01-01"), fassung(file_name="b.md", gueltig_ab="2026-01-01")],
        [fassung(gueltig_ab="2026-01-01"), fassung(file_name="b.md")],
        [fassung(fassung="1"), fassung(file_name="b.md", fassung="2")],
        [fassung(quelle="DORA ", fassung="1"), fassung(quelle="dora", file_name="b.md", fassung="2")],
    ],
    ids=["zwei_datierte", "datiert_und_ohne", "zwei_fassungen", "titel_nur_schreibweise"],
)
def test_mehrdeutige_fassung_ist_datenfehler_kein_normkonflikt(nodes):
    assert FassungsGate().entscheide("f", nodes).reasons == (AbstainReason.AMBIGUOUS_VERSION,)


def test_datensatz_fuehrt_alle_nodes_mit_status_und_stichtag():
    alt = fassung(segment="auslegung", gueltig_bis="2026-06-14", fassung="1")
    neu = fassung(file_name="b.md", segment="gesetz")
    entscheidung = GateDecision(
        Outcome.ABSTAIN, (AbstainReason.NOT_ANSWERABLE,), GateScores(konflikt=0.2), (neu,)
    )

    satz = eskalationsdatensatz("f", [alt, neu], entscheidung, datetime(2026, 6, 30, 22, 30, tzinfo=timezone.utc))

    assert satz["stichtag"] == "2026-07-01"
    assert satz["evidenz"][0]["gueltigkeit"] == "abgelaufen"
    assert satz["evidenz"][0]["gueltig_bis"] == "2026-06-14"
    assert satz["evidenz"][0]["fassung"] == "1"
    assert satz["evidenz"][1]["gueltigkeit"] == "unbekannt"
    assert "gueltig_ab" not in satz["evidenz"][1]
    assert satz["scores"]["konflikt"] == 0.2
    assert satz["segment"] == "gesetz"
    json.dumps(satz)


def test_metrik_segment_kommt_aus_den_genutzten_nodes(tmp_path, monkeypatch):
    import metrics

    monkeypatch.setattr(metrics, "METRIKEN", metrics.Metriken())
    neu = fassung(file_name="b.md", segment="gesetz", **GUELTIG)
    alt = fassung(segment="auslegung", **ABGELAUFEN)

    entscheide_und_eskaliere(gueltigkeits_gate(), tmp_path / "e.jsonl", "f", [alt, neu])

    assert set(metrics.METRIKEN.schnappschuss()["nach_segment"]) == {"gesetz"}


def test_gemischte_reasons_erscheinen_generisch_in_den_metriken(tmp_path, monkeypatch):
    import metrics

    monkeypatch.setattr(metrics, "METRIKEN", metrics.Metriken())

    entscheide_und_eskaliere(gueltigkeits_gate(), tmp_path / "e.jsonl", "f", [fassung(**ABGELAUFEN)])

    assert metrics.METRIKEN.schnappschuss()["eskalationen_nach_reason"] == {"OUTDATED_SOURCE": 1}
