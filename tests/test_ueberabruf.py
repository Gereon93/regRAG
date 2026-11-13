"""Überabruf gegen Verdrängung durch abgelöste Fassungen (Issue #53, ADR 0015).

Der Fixture-Korpus ist fast textgleich: Die neue Fassung ersetzt "Erstmeldung" durch "Erstanzeige" und ändert
die Frist. Jedes Chunk der alten Fassung liegt damit näher an der Frage als jedes der neuen.
"""

import json
import math
import re
import sys
from collections import Counter
from datetime import date
from types import SimpleNamespace

import pytest

import config
import gueltigkeit
import retrieval
from evaluation import verdraengung
from evaluation.verdraengung import baue_messgate, miss
from gate import AbstainReason, Outcome

STICHTAG = date(2026, 6, 15)
ALT = {"quelle": "Meldeverordnung", "file_name": "alt.md", "gueltig_bis": "2026-06-14"}
NEU = {"quelle": "Meldeverordnung", "file_name": "neu.md", "gueltig_ab": "2026-06-15"}
FREMD = {"quelle": "Leitlinie", "file_name": "fremd.md"}

FRAGE = "Binnen welcher Frist ist die Erstmeldung eines schwerwiegenden IKT-Vorfalls zu übermitteln?"
FREMDE_FRAGE = "Wer billigt die Strategie für die digitale Resilienz?"
ABSAETZE = (
    "Kreditinstitute: Die {wort} eines schwerwiegenden IKT-Vorfalls ist binnen {frist} Stunden zu übermitteln.",
    (
        "Versicherungen: Die {wort} eines schwerwiegenden IKT-Vorfalls ist binnen {frist} Stunden an die Aufsicht zu"
        " übermitteln."
    ),
    (
        "Wertpapierfirmen: Die {wort} eines schwerwiegenden IKT-Vorfalls ist binnen {frist} Stunden elektronisch zu"
        " übermitteln."
    ),
)
ALTE_FASSUNG = tuple((text.format(wort="Erstmeldung", frist=4), ALT) for text in ABSAETZE)
NEUE_FASSUNG = tuple((text.format(wort="Erstanzeige", frist=24), NEU) for text in ABSAETZE)
FREMDER_TEXT = (("Kapitel 2: Das Leitungsorgan billigt die Strategie für die digitale Resilienz.", FREMD),)
KORPUS = ALTE_FASSUNG + NEUE_FASSUNG + FREMDER_TEXT
MIN_SCORE = 0.3


def _woerter(text):
    return Counter(re.findall(r"\w+", text.lower()))


def _cosinus(a, b):
    skalar = sum(a[w] * b[w] for w in a)
    return skalar / math.sqrt(sum(v * v for v in a.values()) * sum(v * v for v in b.values()))


class FixtureIndex:
    """Deterministischer Bag-of-Words-Index: der Score entsteht aus dem Text, nicht aus Handwerten."""

    def __init__(self, korpus=KORPUS):
        self.korpus = korpus
        self.abrufe = []

    def as_retriever(self, similarity_top_k):
        def retrieve(frage):
            self.abrufe.append(similarity_top_k)
            treffer = [
                SimpleNamespace(score=_cosinus(_woerter(frage), _woerter(text)), text=text, metadata=dict(meta))
                for text, meta in self.korpus
            ]
            return sorted(treffer, key=lambda n: -n.score)[:similarity_top_k]

        return SimpleNamespace(retrieve=retrieve)


def _gate(min_score=MIN_SCORE):
    return baue_messgate(min_score, STICHTAG)


def _ueberabruf(index=None, faktor=3):
    return retrieval.UeberabrufRetriever(index or FixtureIndex(), top_k=3, faktor=faktor, uhr=lambda: STICHTAG)


def n(score, **metadata):
    return SimpleNamespace(score=score, text=f"t{score}", metadata=metadata)


def _status(nodes):
    return [gueltigkeit.status_von(x.metadata, STICHTAG) for x in nodes]


def test_ohne_datierte_treffer_bleibt_es_das_top_k():
    kandidaten = [n(0.9 - i / 10, **FREMD) for i in range(6)]

    assert gueltigkeit.waehle_treffer(kandidaten, 3, STICHTAG) == kandidaten[:3]


def test_verdraengt_bleiben_die_abgeloesten_vorne_und_nutzbare_fuellen_auf():
    alt = [n(0.9, **ALT), n(0.89, **ALT), n(0.88, **ALT)]
    neu = [n(0.8, **NEU), n(0.79, **NEU), n(0.78, **NEU)]

    auswahl = gueltigkeit.waehle_treffer([*alt, *neu, n(0.5, **FREMD)], 3, STICHTAG)

    assert auswahl == [*alt, *neu]


def test_verdraengt_auch_neben_unbekannten_treffern():
    kandidaten = [n(0.9, **ALT), n(0.85, **FREMD), n(0.84, **FREMD), n(0.8, **NEU), n(0.7, **FREMD)]

    assert gueltigkeit.waehle_treffer(kandidaten, 3, STICHTAG) == kandidaten[:4]


def test_top_k_mit_gueltiger_fassung_bleibt_unveraendert():
    kandidaten = [n(0.9, **ALT), n(0.85, **NEU), n(0.84, **FREMD), n(0.8, **NEU)]

    assert gueltigkeit.waehle_treffer(kandidaten, 3, STICHTAG) == kandidaten[:3]


def test_reichen_die_kandidaten_nicht_kommen_alle():
    kandidaten = [n(0.9, **ALT), n(0.8, **NEU)]

    assert gueltigkeit.waehle_treffer(kandidaten, 3, STICHTAG) == kandidaten
    assert gueltigkeit.waehle_treffer([], 3, STICHTAG) == []


@pytest.mark.parametrize("nicht_nutzbar", [{"gueltig_ab": "2026-13-01"}, {"gueltig_ab": "2026-07-01"}])
def test_ungueltige_und_kuenftige_fassungen_verdraengen_ebenso(nicht_nutzbar):
    kandidaten = [n(0.9, **nicht_nutzbar), n(0.8, **NEU)]

    assert gueltigkeit.waehle_treffer(kandidaten, 1, STICHTAG) == kandidaten


def test_retriever_holt_top_k_mal_faktor_kandidaten():
    index = FixtureIndex()

    retrieval.UeberabrufRetriever(index, top_k=3, faktor=3).retrieve(FRAGE)

    assert index.abrufe == [9]


def test_retriever_bestimmt_den_stichtag_je_abruf():
    tage = iter([date(2026, 6, 14), STICHTAG])
    retriever = retrieval.UeberabrufRetriever(FixtureIndex(), top_k=3, faktor=3, uhr=lambda: next(tage))

    am_vortag, am_stichtag = retriever.retrieve(FRAGE), retriever.retrieve(FRAGE)

    assert len(am_vortag) == 3
    assert len(am_stichtag) == 6


def test_faktor_eins_ist_das_heutige_verhalten():
    index = FixtureIndex()

    treffer = _ueberabruf(index, faktor=1).retrieve(FRAGE)

    assert treffer == index.as_retriever(similarity_top_k=3).retrieve(FRAGE)


@pytest.mark.parametrize("faktor", [0, -1])
def test_faktor_unter_eins_ist_ein_fehler(faktor):
    with pytest.raises(ValueError, match="faktor"):
        retrieval.UeberabrufRetriever(FixtureIndex(), top_k=3, faktor=faktor)


def test_fixture_bei_top_k_3_verdraengt_die_alte_fassung_die_gueltige():
    treffer = FixtureIndex().as_retriever(similarity_top_k=3).retrieve(FRAGE)

    assert _status(treffer) == ["abgelaufen"] * 3
    assert _gate().entscheide(FRAGE, treffer).reasons == (AbstainReason.OUTDATED_SOURCE,)


def test_fixture_mit_ueberabruf_antwortet_nur_aus_der_gueltigen_fassung():
    treffer = _ueberabruf().retrieve(FRAGE)

    entscheidung = _gate().entscheide(FRAGE, treffer)

    assert entscheidung.outcome is Outcome.ANSWER
    assert sorted(x.text for x in entscheidung.genutzte_nodes(treffer)) == sorted(text for text, _ in NEUE_FASSUNG)


def test_fixture_ohne_gueltige_fassung_unter_den_kandidaten_bleibt_outdated_source():
    treffer = _ueberabruf(FixtureIndex(ALTE_FASSUNG + FREMDER_TEXT)).retrieve(FRAGE)

    entscheidung = _gate().entscheide(FRAGE, treffer)

    assert entscheidung.reasons == (AbstainReason.OUTDATED_SOURCE,)
    assert _status(entscheidung.genutzte_nodes(treffer)) == ["abgelaufen"] * 3 + ["unbekannt"]


def test_nach_dem_filter_entscheidet_der_score_der_gueltigen_fassung():
    """Der beste Score sinkt durch das Filtern: aus OUTDATED_SOURCE kann LOW_RELEVANCE werden (ADR 0015)."""
    kandidaten = [n(0.9, **ALT), n(0.88, **ALT), n(0.86, **ALT), n(0.5, **NEU)]
    treffer = gueltigkeit.waehle_treffer(kandidaten, 3, STICHTAG)

    vorher = _gate(min_score=0.62).entscheide(FRAGE, kandidaten[:3])
    nachher = _gate(min_score=0.62).entscheide(FRAGE, treffer)

    assert vorher.reasons == (AbstainReason.OUTDATED_SOURCE,)
    assert nachher.reasons == (AbstainReason.LOW_RELEVANCE,)
    assert nachher.scores.relevance == 0.9


def _zeitgeber(zeitpunkte):
    zeiten = iter(zeitpunkte)
    return lambda: next(zeiten)


def test_messung_zaehlt_verdraengung_und_behebung_am_fixture():
    index = FixtureIndex()

    bericht = miss(
        [FRAGE, FREMDE_FRAGE],
        index.as_retriever(similarity_top_k=3),
        index.as_retriever(similarity_top_k=9),
        top_k=3,
        gate=_gate(),
        stichtag=STICHTAG,
        zeit=_zeitgeber([0.0, 0.1, 0.1, 0.4, 1.0, 1.3, 1.3, 1.5]),
    )

    z = bericht["zusammenfassung"]
    assert (z["n"], z["verdraengt"], z["gueltige_unter_kandidaten"], z["gueltige_in_auswahl"]) == (2, 1, 1, 1)
    assert z["top_k_abweichend"] == 0
    assert z["wechsel"] == {"OUTDATED_SOURCE -> ANSWER": 1}
    assert z["latenz_sekunden"]["top_k"]["median"] == pytest.approx(0.15)
    assert z["latenz_sekunden"]["kandidaten"]["median"] == pytest.approx(0.3)
    assert z["latenz_sekunden"]["differenz_median"] == pytest.approx(0.15)
    assert (z["stichtag"], z["top_k"]) == ("2026-06-15", 3)
    zeile = bericht["zeilen"][0]
    assert zeile["vorher"] == {"reasons": ["OUTDATED_SOURCE"], "bester_nutzbarer_score": None}
    assert zeile["nachher"]["reasons"] == []
    assert zeile["nachher"]["bester_nutzbarer_score"] < zeile["top_score"]
    assert bericht["zeilen"][1]["vorher"] == bericht["zeilen"][1]["nachher"]


def test_messung_meldet_eigenen_top_k_abruf_der_abweicht():
    index = FixtureIndex()
    anders = SimpleNamespace(retrieve=lambda frage: list(reversed(index.as_retriever(3).retrieve(frage))))

    bericht = miss([FRAGE], anders, index.as_retriever(9), 3, _gate(), STICHTAG, _zeitgeber([0] * 4))

    assert bericht["zeilen"][0]["top_k_abweichend"] is True


def test_messung_am_vortag_findet_keine_verdraengung():
    index = FixtureIndex()

    bericht = miss(
        [FRAGE], index.as_retriever(3), index.as_retriever(9), 3, baue_messgate(MIN_SCORE, date(2026, 6, 14)),
        date(2026, 6, 14), _zeitgeber([0] * 4),
    )

    assert bericht["zusammenfassung"]["verdraengt"] == 0
    assert bericht["zeilen"][0]["vorher"]["reasons"] == []


def test_messung_ohne_treffer_hat_keinen_score():
    leer = SimpleNamespace(retrieve=lambda frage: [])

    bericht = miss([FRAGE], leer, leer, 3, _gate(), STICHTAG, _zeitgeber([0] * 4))

    zeile = bericht["zeilen"][0]
    assert zeile["top_score"] is None
    assert zeile["vorher"] == {"reasons": ["LOW_RELEVANCE"], "bester_nutzbarer_score": None}


def test_main_liest_fragen_datei_und_gibt_json_aus(tmp_path, monkeypatch, capsys):
    index = FixtureIndex()
    monkeypatch.setitem(sys.modules, "rag", SimpleNamespace(index=index))
    monkeypatch.setattr(config, "MIN_RETRIEVAL_SCORE", MIN_SCORE)
    fragen = tmp_path / "fragen.txt"
    fragen.write_text(f"{FRAGE}\n\n", encoding="utf-8")

    verdraengung.main(["--fragen", str(fragen), "--faktor", "2", "--stichtag", "2026-06-15"])

    bericht = json.loads(capsys.readouterr().out)
    assert bericht["zusammenfassung"]["n"] == 1
    assert bericht["zusammenfassung"]["gueltige_in_auswahl"] == 1
    assert bericht["zusammenfassung"]["faktor"] == 2
    assert sorted(set(index.abrufe)) == [3, 6]


def test_messung_gueltige_hinter_dem_k_ten_nutzbaren_ist_nicht_behoben():
    kandidaten = [n(0.9, **ALT), n(0.85, **FREMD), n(0.84, **FREMD), n(0.83, **FREMD), n(0.8, **NEU)]
    fest = SimpleNamespace(retrieve=lambda frage: kandidaten)

    bericht = miss([FRAGE], fest, fest, 3, _gate(), STICHTAG, _zeitgeber([0] * 4))

    zeile = bericht["zeilen"][0]
    assert (zeile["verdraengt"], zeile["gueltige_unter_kandidaten"], zeile["gueltige_in_auswahl"]) == (True, True, False)
    assert zeile["nachher"]["reasons"] == ["OUTDATED_SOURCE"]
