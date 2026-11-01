import hashlib
import json
import sys
from types import SimpleNamespace

import httpx
import pytest

import config
from evaluation import eval_report, vergleich
from evaluation.eval_report import (
    erzeuge_bericht,
    erzeuge_laya_bericht,
    hole_laya_zeilen,
    schreibe,
)
from evaluation.evalset import Kosten, eval_version, teile
from laya import LayaGate, SystemOneClient
from manifest import lade_manifest, schreibe_manifest

KOSTEN = Kosten(falschantwort=10.0, eskalation=1.0)


def knoten(score, segment):
    return SimpleNamespace(score=score, text="Evidenz", metadata={"segment": segment})


def fall(id, erwartet, segment, gruppe=None):
    return {
        "id": id,
        "frage": f"Frage {id}?",
        "erwartet": erwartet,
        "segment": segment,
        "kategorie": "k",
        "gruppe": gruppe or id,
    }


class Szenario:
    """n=100: answer/abstain je Segment, Fälle unter dem Guard, ausserhalb-Fälle unter 'verordnung'."""

    def __init__(self, mit_ausserhalb=True, ausserhalb_prob=0.7):
        self.faelle, self.treffer, self.prob, self.aufrufe = [], {}, {}, []
        for i in range(25):
            self.add(f"va{i}", "answer", "verordnung", "verordnung", 0.9, 0.6 + (i % 4) * 0.1)
            self.add(f"vn{i}", "abstain", "verordnung", "verordnung", 0.9, 0.1 + (i % 4) * 0.05)
        for i in range(10):
            self.add(f"ga{i}", "answer", "gesetz", "gesetz", 0.9, 0.8)
            self.add(f"gn{i}", "abstain", "gesetz", "gesetz", 0.9, 0.2)
        for i in range(10):
            self.add(f"g{i}", "answer", "gesetz", "gesetz", 0.3, 0.99)  # unter dem Guard
        if mit_ausserhalb:
            for i in range(20):
                self.add(f"x{i}", "abstain", "ausserhalb", "verordnung", 0.9, ausserhalb_prob)

    def add(self, id, erwartet, segment, laufzeit, top, prob):
        f = fall(id, erwartet, segment)
        self.faelle.append(f)
        self.treffer[f["frage"]] = [knoten(top, laufzeit)]
        self.prob[f["frage"]] = prob

    @property
    def retriever(self):
        return SimpleNamespace(retrieve=lambda frage: self.treffer[frage])

    def laya(self, routing="multilingual", ausfall=False):
        self.aufrufe = []

        def handler(request):
            if ausfall:
                return httpx.Response(503)
            frage = json.loads(request.content)["state"]["frage"]
            self.aufrufe.append(frage)
            body = {"answers": {"answerable": {"noul": self.prob[frage]}}, "routing": {"model": routing}}
            return httpx.Response(200, json=body)

        client = SystemOneClient("http://laya.test", transport=httpx.MockTransport(handler))
        return LayaGate(client, lambda s: 0.5, modell="multilingual", modell_streng=True)


@pytest.fixture
def chroma(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHROMA_VERZEICHNIS", str(tmp_path / "chroma"))
    return tmp_path / "chroma"


def test_laya_wird_unter_dem_guard_nicht_aufgerufen():
    s = Szenario()
    zeilen = hole_laya_zeilen(s.faelle, s.retriever, s.laya(), config.MIN_RETRIEVAL_SCORE)
    unter = {z["id"] for z in zeilen if z["top"] < config.MIN_RETRIEVAL_SCORE}
    assert unter and all(z["laya_roh"] is None for z in zeilen if z["id"] in unter)
    assert len(s.aufrufe) == len(zeilen) - len(unter)
    assert all(z["laya_roh"] is not None for z in zeilen if z["id"] not in unter)


def test_fall_ohne_treffer_ruft_laya_nie_auf():
    leer = SimpleNamespace(retrieve=lambda frage: [])
    s = Szenario()
    zeilen = hole_laya_zeilen(s.faelle[:3], leer, s.laya(), 0.62)
    assert s.aufrufe == []
    assert [(z["top"], z["laya_roh"], z["segment_laufzeit"]) for z in zeilen] == [(None, None, "unbekannt")] * 3


def test_ausserhalb_fall_landet_unter_dem_laufzeitsegment():
    s = Szenario()
    zeilen = {z["id"]: z for z in hole_laya_zeilen(s.faelle, s.retriever, s.laya(), 0.62)}
    assert zeilen["x0"]["segment"] == "ausserhalb"
    assert zeilen["x0"]["segment_laufzeit"] == "verordnung"
    assert zeilen["x0"]["gruppe"] == "x0"


def test_judge_fehler_bricht_den_report_ab():
    s = Szenario()
    with pytest.raises(SystemExit):
        hole_laya_zeilen(s.faelle, s.retriever, s.laya(ausfall=True), 0.62)


def test_fremdes_routing_modell_bricht_ab():
    s = Szenario()
    with pytest.raises(SystemExit):
        erzeuge_laya_bericht(s.faelle, s.retriever, s.laya(routing="anderes"), KOSTEN, None)


def test_report_enthaelt_laya_teil_und_pflichtfelder_fuer_den_vergleich(chroma):
    s = Szenario()
    b, _ = erzeuge_laya_bericht(s.faelle, s.retriever, s.laya(), KOSTEN, "rev1")
    assert b["eval_version"] == eval_version(s.faelle)
    assert b["min_retrieval_score"] == config.MIN_RETRIEVAL_SCORE
    assert b["kosten"] == {"falschantwort": 10.0, "eskalation": 1.0}
    laya = b["laya"]
    assert laya["checkpoint"] == {"modell": "multilingual", "revision": "rev1"}
    assert laya["index_fingerprint"] is None
    assert laya["fallback_schwelle"] == max(laya["schwellen"].values())
    assert "unbekannt" not in laya["schwellen"]
    assert {f["id"] for f in laya["test_faelle"]} == {f["id"] for f in teile(s.faelle)["test"]}
    assert set(laya["test_faelle"][0]) == {"id", "gruppe", "kosten"}
    assert "Evidenz" in laya["definition"]
    assert set(laya["ece"]) == {"vorher", "nachher"}


def test_laya_sieht_keine_train_faelle(chroma):
    s = Szenario()
    laya = s.laya()
    erzeuge_laya_bericht(s.faelle, s.retriever, laya, KOSTEN, None)
    splits = teile(s.faelle)
    train = {f["frage"] for f in splits["train"]}
    kal = {f["frage"] for f in splits["calibration"]}
    test = {f["frage"] for f in splits["test"]}
    assert not set(s.aufrufe) & train
    assert set(s.aufrufe) & kal
    assert set(s.aufrufe) & test


def test_schwellen_kommen_nur_aus_calibration_zeilen_mit_laya_score(chroma):
    """Guard-verworfene Calibration-Fälle (prob 0.99, würde die Schwelle drücken) dürfen sie nicht beeinflussen."""
    s = Szenario()
    b, _ = erzeuge_laya_bericht(s.faelle, s.retriever, s.laya(), KOSTEN, None)
    for fall_ in s.faelle:
        if fall_["id"].startswith("g") and fall_["id"][1].isdigit():
            s.prob[fall_["frage"]] = 0.0
    b2, _ = erzeuge_laya_bericht(s.faelle, s.retriever, s.laya(), KOSTEN, None)
    assert b["laya"]["schwellen"] == b2["laya"]["schwellen"]
    assert b["laya"]["temperatur"] == b2["laya"]["temperatur"]


def test_ausserhalb_fall_macht_die_schwelle_des_laufzeitsegments_strenger(chroma):
    ohne = Szenario(mit_ausserhalb=False)
    mit = Szenario(mit_ausserhalb=True)
    schwelle_ohne = erzeuge_laya_bericht(ohne.faelle, ohne.retriever, ohne.laya(), KOSTEN, None)[0]["laya"]["schwellen"]
    schwelle_mit = erzeuge_laya_bericht(mit.faelle, mit.retriever, mit.laya(), KOSTEN, None)[0]["laya"]["schwellen"]
    assert schwelle_mit["verordnung"] > schwelle_ohne["verordnung"]


def test_unbekannt_zeilen_gehen_in_den_fit_aber_nicht_in_die_schwellen(chroma):
    s = Szenario()
    basis, _ = erzeuge_laya_bericht(s.faelle, s.retriever, s.laya(), KOSTEN, None)
    for i in range(40):  # gemischte Evidenz -> segment_laufzeit unbekannt
        erwartet = "answer" if i % 2 else "abstain"
        s.add(f"u{i}", erwartet, "verordnung", "verordnung", 0.9, 0.95 if erwartet == "answer" else 0.05)
        s.treffer[f"Frage u{i}?"] = [knoten(0.9, "verordnung"), knoten(0.9, "gesetz")]
    b, _ = erzeuge_laya_bericht(s.faelle, s.retriever, s.laya(), KOSTEN, None)
    assert "unbekannt" not in b["laya"]["schwellen"]
    assert set(b["laya"]["schwellen"]) == set(basis["laya"]["schwellen"])
    assert b["laya"]["fallback_schwelle"] == max(b["laya"]["schwellen"].values())
    assert b["laya"]["temperatur"] != basis["laya"]["temperatur"]  # unbekannt-Zeilen gingen in den Fit ein
    assert "unbekannt" in b["laya"]["segmente"]


def test_ohne_laya_score_in_einem_segment_aus_segmente_bricht_ab(chroma):
    s = Szenario()
    for k in s.treffer:
        s.treffer[k] = [knoten(0.1, "verordnung")]
    with pytest.raises(SystemExit, match="Laya"):
        erzeuge_laya_bericht(s.faelle, s.retriever, s.laya(), KOSTEN, None)


def test_warnung_bei_answer_fall_mit_abweichendem_segment(chroma):
    s = Szenario()
    s.add("abw", "answer", "gesetz", "verordnung", 0.9, 0.8)
    b, _ = erzeuge_laya_bericht(s.faelle, s.retriever, s.laya(), KOSTEN, None)
    assert any("abw" in w and "gesetz" in w and "verordnung" in w for w in b["laya"]["warnungen"])


def test_manifest_wird_nur_mit_revision_erzeugt_und_ist_ladbar(chroma, tmp_path):
    s = Szenario()
    _, ohne = erzeuge_laya_bericht(s.faelle, s.retriever, s.laya(), KOSTEN, None)
    assert ohne is None
    b, manifest = erzeuge_laya_bericht(s.faelle, s.retriever, s.laya(), KOSTEN, "abc123")
    geladen = lade_manifest(schreibe_manifest(manifest, tmp_path / "manifest"), config)
    assert geladen.eval_version == b["eval_version"]
    assert geladen.checkpoint_revision == "abc123"
    assert geladen.schwellen == b["laya"]["schwellen"]
    assert geladen.fallback_schwelle == b["laya"]["fallback_schwelle"]
    assert geladen.temperatur == b["laya"]["temperatur"]


def test_index_fingerprint_ist_sha256_der_datei(chroma):
    chroma.mkdir()
    (chroma / "fingerprint.json").write_bytes(b'{"a": 1}')
    s = Szenario()
    b, _ = erzeuge_laya_bericht(s.faelle, s.retriever, s.laya(), KOSTEN, None)
    assert b["laya"]["index_fingerprint"] == hashlib.sha256(b'{"a": 1}').hexdigest()


def test_laya_report_enthaelt_den_31_report_unveraendert(chroma):
    s = Szenario()
    erwartet = erzeuge_bericht(s.faelle, s.retriever, KOSTEN)
    b, _ = erzeuge_laya_bericht(s.faelle, s.retriever, s.laya(), KOSTEN, None)
    rest = {k: v for k, v in b.items() if k not in ("laya", "min_retrieval_score")}
    erwartet["zeitpunkt"] = rest["zeitpunkt"]
    assert json.dumps(rest, sort_keys=True) == json.dumps(erwartet, sort_keys=True)


def vorbereitet(s, tmp_path, monkeypatch, laya_url):
    index = SimpleNamespace(as_retriever=lambda similarity_top_k: s.retriever)
    monkeypatch.setitem(sys.modules, "rag", SimpleNamespace(index=index))
    monkeypatch.setitem(sys.modules, "rag.index", index)
    monkeypatch.setattr(eval_report, "lade_faelle", lambda pfad: s.faelle)
    monkeypatch.setattr(eval_report, "baue_laya_gate", lambda: s.laya())
    monkeypatch.setattr(config, "LAYA_URL", laya_url)
    monkeypatch.setattr(config, "EVAL_BERICHT_VERZEICHNIS", str(tmp_path / "reports"))
    monkeypatch.setattr(eval_report, "MANIFEST_VERZEICHNIS", tmp_path / "manifest")


def test_main_ohne_laya_url_schreibt_den_31_report(tmp_path, monkeypatch, chroma):
    s = Szenario()
    vorbereitet(s, tmp_path, monkeypatch, "")
    eval_report.main([])
    datei = tmp_path / "reports" / f"bericht-{eval_version(s.faelle)[7:19]}.json"
    assert "laya" not in json.loads(datei.read_text())
    assert s.aufrufe == []
    assert not (tmp_path / "manifest").exists()


def test_main_mit_laya_ohne_revision_schreibt_kein_manifest_und_hinweis(tmp_path, monkeypatch, capsys, chroma):
    s = Szenario()
    vorbereitet(s, tmp_path, monkeypatch, "http://laya.test")
    eval_report.main([])
    assert "checkpoint-revision" in capsys.readouterr().err
    assert not (tmp_path / "manifest").exists()
    assert len(list((tmp_path / "reports").glob("bericht-*laya*.json"))) == 1
    eval_report.main(["--checkpoint-revision", "r9"])
    assert len(list((tmp_path / "manifest").glob("gate-manifest-*.json"))) == 1
    assert len(list((tmp_path / "reports").glob("bericht-*laya*.json"))) == 2  # Revision im Namen: keine Kollision


def test_schreibe_unterscheidet_laya_reports_nach_revision(tmp_path):
    b = {"eval_version": "sha256:" + "ab" * 32, "laya": {"checkpoint": {"revision": "r/1"}}}
    assert schreibe(b, tmp_path).name == "bericht-abababababab-laya-r_1.json"


def test_vergleich_cli_vergleicht_zwei_laya_reports(chroma, tmp_path, capsys):
    s = Szenario()
    b, _ = erzeuge_laya_bericht(s.faelle, s.retriever, s.laya(), KOSTEN, None)
    pfad = tmp_path / "a.json"
    pfad.write_text(json.dumps(b), encoding="utf-8")
    vergleich.main([str(pfad), str(pfad)])
    assert json.loads(capsys.readouterr().out)["differenz"] == 0


def test_vergleich_cli_bricht_bei_unvergleichbaren_reports_ab(chroma, tmp_path):
    s = Szenario()
    b, _ = erzeuge_laya_bericht(s.faelle, s.retriever, s.laya(), KOSTEN, None)
    a, c = tmp_path / "a.json", tmp_path / "c.json"
    a.write_text(json.dumps(b), encoding="utf-8")
    c.write_text(json.dumps({**b, "eval_version": "sha256:x"}), encoding="utf-8")
    with pytest.raises(SystemExit, match="nicht vergleichbar"):
        vergleich.main([str(a), str(c)])
