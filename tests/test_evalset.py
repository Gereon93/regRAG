import json

import pytest

from evaluation.evalset import (
    STANDARD_FAELLE,
    Kosten,
    bericht,
    eval_version,
    lade_faelle,
    schwellen_pro_segment,
    teile,
    wilson,
)

KOSTEN = Kosten(falschantwort=10.0, eskalation=1.0)


def fall(id, erwartet="answer", segment="verordnung", gruppe=None, kategorie="beantwortbar"):
    return {
        "id": id,
        "frage": f"Frage {id}?",
        "erwartet": erwartet,
        "segment": segment,
        "kategorie": kategorie,
        "gruppe": gruppe or id,
    }


def schreibe(tmp_path, faelle):
    pfad = tmp_path / "set.jsonl"
    pfad.write_text("".join(json.dumps(f) + "\n" for f in faelle), encoding="utf-8")
    return pfad


def test_lade_faelle_liest_jsonl(tmp_path):
    pfad = schreibe(tmp_path, [fall("a"), fall("b", erwartet="abstain")])
    assert [f["id"] for f in lade_faelle(pfad)] == ["a", "b"]


DOPPELTE_ID = {"id": "a"}


@pytest.mark.parametrize(
    "kaputt",
    [{"erwartet": "maybe"}, DOPPELTE_ID, {"segment": ""}, {"frage": ""}],
)
def test_lade_faelle_lehnt_ungueltige_faelle_ab(tmp_path, kaputt):
    pfad = schreibe(tmp_path, [fall("a"), {**fall("b"), **kaputt}])
    with pytest.raises(ValueError):
        lade_faelle(pfad)


def test_lade_faelle_lehnt_fehlendes_pflichtfeld_ab(tmp_path):
    ohne_segment = {k: v for k, v in fall("a").items() if k != "segment"}
    with pytest.raises(ValueError):
        lade_faelle(schreibe(tmp_path, [ohne_segment]))


def test_gruppe_faellt_auf_id_zurueck(tmp_path):
    ohne_gruppe = {k: v for k, v in fall("a").items() if k != "gruppe"}
    assert lade_faelle(schreibe(tmp_path, [ohne_gruppe]))[0]["gruppe"] == "a"


def test_standard_set_ist_valide_und_splittbar():
    faelle = lade_faelle(STANDARD_FAELLE)
    assert len(faelle) >= 14
    assert {f["erwartet"] for f in faelle} == {"answer", "abstain"}
    assert eval_version(faelle).startswith("sha256:")


def test_eval_version_aendert_sich_mit_inhalt_nicht_mit_reihenfolge():
    a, b = fall("a"), fall("b")
    assert eval_version([a, b]) == eval_version([b, a])
    assert eval_version([a, b]) != eval_version([a, {**b, "erwartet": "abstain"}])
    assert eval_version([a, b]) != eval_version([a, {**b, "gruppe": "andere"}])
    assert eval_version([a]).startswith("sha256:")


def test_teile_ist_deterministisch_und_vollstaendig():
    faelle = [fall(f"f{i}") for i in range(100)]
    splits = teile(faelle)
    assert teile(faelle) == splits
    assert sorted(f["id"] for s in splits.values() for f in s) == sorted(f["id"] for f in faelle)
    assert set(splits) == {"train", "calibration", "test"}
    assert all(splits[s] for s in splits)


def test_teile_stabil_beim_hinzufuegen_neuer_faelle():
    faelle = [fall(f"f{i}") for i in range(50)]
    vorher = {f["id"]: s for s, fs in teile(faelle).items() for f in fs}
    nachher = {f["id"]: s for s, fs in teile(faelle + [fall("neu")]).items() for f in fs}
    assert all(nachher[i] == s for i, s in vorher.items())


def test_teile_haelt_gruppen_zusammen():
    faelle = [fall(f"f{i}", gruppe=f"g{i % 10}") for i in range(100)]
    ort = {}
    for split, fs in teile(faelle).items():
        for f in fs:
            ort.setdefault(f["gruppe"], set()).add(split)
    assert all(len(s) == 1 for s in ort.values())


def zeile(segment, erwartet, top):
    return {"segment": segment, "erwartet": erwartet, "top": top}


def zeilen():
    return [
        zeile("verordnung", "answer", 0.90),
        zeile("verordnung", "answer", 0.80),
        zeile("verordnung", "abstain", 0.70),
        zeile("verordnung", "abstain", 0.40),
        zeile("ausserhalb", "abstain", 0.30),
        zeile("ausserhalb", "abstain", 0.20),
    ]


def ueberlappende_zeilen():
    return [
        zeile("verordnung", "answer", 0.90),
        zeile("verordnung", "answer", 0.80),
        zeile("verordnung", "abstain", 0.85),
        zeile("verordnung", "abstain", 0.40),
    ]


def test_schwelle_minimiert_kosten_nicht_accuracy():
    """Eine Falschantwort kostet 10, eine Eskalation 1: lieber 0.80 eskalieren als 0.85 falsch beantworten."""
    t = schwellen_pro_segment(ueberlappende_zeilen(), KOSTEN)
    assert 0.85 < t["verordnung"] <= 0.90


def test_schwelle_gewichtung_ist_config():
    billig = Kosten(falschantwort=0.5, eskalation=1.0)
    assert schwellen_pro_segment(ueberlappende_zeilen(), billig)["verordnung"] < 0.85


def test_schwelle_segment_ohne_falschantwort_risiko_laesst_alles_durch():
    nur_antwortbar = [zeile("x", "answer", 0.5), zeile("x", "answer", 0.9)]
    assert schwellen_pro_segment(nur_antwortbar, KOSTEN)["x"] <= 0.5


def test_schwelle_segment_nur_unbeantwortbar_verweigert_alles():
    nur_unbeantwortbar = [zeile("x", "abstain", 0.5), zeile("x", "abstain", 0.9)]
    assert schwellen_pro_segment(nur_unbeantwortbar, KOSTEN)["x"] > 0.9


def test_schwelle_gleichstand_waehlt_die_hoehere():
    gleich = [zeile("x", "answer", 0.4), zeile("x", "abstain", 0.6)]
    schwellen = schwellen_pro_segment(gleich, Kosten(falschantwort=1.0, eskalation=1.0))
    assert schwellen["x"] > 0.6


def test_kosten_lehnen_negative_gewichte_ab():
    with pytest.raises(ValueError):
        Kosten(falschantwort=-1.0, eskalation=1.0)


def test_wilson_grenzfaelle():
    assert wilson(0, 0) == (0.0, 1.0)
    lo, hi = wilson(0, 10)
    assert lo == 0.0 and 0.2 < hi < 0.4
    lo, hi = wilson(50, 100)
    assert lo < 0.5 < hi and hi - lo < 0.21


def test_bericht_pro_segment_mit_ci_und_version():
    schwellen = {"verordnung": 0.75, "ausserhalb": 0.5}
    b = bericht(zeilen(), schwellen, KOSTEN, version="sha256:abc")
    assert b["eval_version"] == "sha256:abc"
    assert b["schwellen"] == schwellen
    assert b["kosten"] == {"falschantwort": 10.0, "eskalation": 1.0}
    v = b["segmente"]["verordnung"]
    assert (v["n"], v["beantwortet"], v["falschantworten"], v["eskaliert"]) == (4, 2, 0, 2)
    assert v["coverage"]["wert"] == 0.5
    assert v["eskalationsrate"]["wert"] == 0.5
    assert v["falschantwortrate"]["wert"] == 0.0
    lo, hi = v["falschantwortrate"]["ci95"]
    assert lo == 0.0 and hi > 0.0
    assert b["gesamt"]["n"] == 6


def test_bericht_falschantwortrate_ist_anteil_an_beantworteten():
    b = bericht(zeilen(), {"verordnung": 0.5, "ausserhalb": 0.99}, KOSTEN, version="v")
    v = b["segmente"]["verordnung"]
    assert v["beantwortet"] == 3 and v["falschantworten"] == 1
    assert v["falschantwortrate"]["wert"] == pytest.approx(1 / 3)


def test_bericht_ohne_beantwortete_hat_keine_falschantwortrate():
    b = bericht(zeilen(), {"verordnung": 0.99, "ausserhalb": 0.99}, KOSTEN, version="v")
    assert b["segmente"]["verordnung"]["falschantwortrate"]["wert"] is None


def test_bericht_segment_ohne_schwelle_ist_fehler():
    with pytest.raises(KeyError):
        bericht(zeilen(), {"verordnung": 0.5}, KOSTEN, version="v")


class FesterRetriever:
    def __init__(self, scores):
        self.scores = scores

    def retrieve(self, frage):
        from types import SimpleNamespace

        return [SimpleNamespace(score=self.scores[frage])]


def test_erzeuge_bericht_waehlt_schwelle_auf_calibration_und_misst_auf_test():
    from evaluation.eval_report import erzeuge_bericht

    faelle = []
    for i in range(60):
        faelle.append(fall(f"a{i}", erwartet="answer"))
        faelle.append(fall(f"n{i}", erwartet="abstain"))
    scores = {f["frage"]: (0.9 if f["erwartet"] == "answer" else 0.2) for f in faelle}
    ergebnis = erzeuge_bericht(faelle, FesterRetriever(scores), KOSTEN)
    assert ergebnis["split"] == "test"
    assert ergebnis["eval_version"] == eval_version(faelle)
    assert 0.2 < ergebnis["schwellen"]["verordnung"] <= 0.9
    assert ergebnis["gesamt"]["falschantworten"] == 0
    assert ergebnis["gesamt"]["n"] == ergebnis["faelle"]["test"]


def test_erzeuge_bericht_bricht_bei_leeren_splits_ab():
    from evaluation.eval_report import erzeuge_bericht

    with pytest.raises(SystemExit):
        erzeuge_bericht([fall("a")], FesterRetriever({"Frage a?": 0.9}), KOSTEN)


def test_schreibe_legt_artefakt_mit_version_im_namen_an(tmp_path):
    from evaluation.eval_report import schreibe

    datei = schreibe({"eval_version": "sha256:" + "ab" * 32}, tmp_path / "reports")
    assert datei.name == "bericht-abababababab.json"
    assert json.loads(datei.read_text())["eval_version"].startswith("sha256:")
