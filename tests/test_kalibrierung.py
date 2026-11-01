import math
import random

import pytest

from evaluation.kalibrierung import ece, fitte_temperatur, vergleiche
from gate import kalibriere


def _sigmoid(z):
    return 1 / (1 + math.exp(-z))


class TestFitteTemperatur:
    def test_findet_t_etwa_2(self):
        rng = random.Random(42)
        zs = [rng.gauss(0, 3) for _ in range(4000)]
        p = [_sigmoid(z) for z in zs]
        y = [1 if rng.random() < _sigmoid(z / 2) else 0 for z in zs]
        T, warnungen = fitte_temperatur(p, y)
        assert 1.8 <= T <= 2.2
        assert warnungen == []

    def test_ein_label_nicht_schaetzbar(self):
        assert fitte_temperatur([0.2, 0.7, 0.9], [1, 1, 1]) == (1.0, ["Temperatur nicht schätzbar"])

    def test_leere_eingabe_nicht_schaetzbar(self):
        assert fitte_temperatur([], []) == (1.0, ["Temperatur nicht schätzbar"])

    def test_randoptimum_gibt_randwert_und_warnung(self):
        T, warnungen = fitte_temperatur([0.1, 0.2, 0.8, 0.9], [0, 0, 1, 1])
        assert T == pytest.approx(0.05, rel=1e-3)
        assert len(warnungen) == 1


class TestEce:
    def test_leer_ist_none(self):
        assert ece([], []) is None

    def test_handgerechneter_wert(self):
        # Bins [0,.2) [.2,.4) [.4,.6) [.6,.8) [.8,1]
        p = [0.1, 0.1, 0.9, 0.9, 1.0]
        y = [0, 1, 1, 1, 1]
        r = ece(p, y)
        # Bin0: n=2 konf .1 treffer .5 -> .4*2/5 = .16; Bin4: n=3 konf .9333 treffer 1 -> .0667*3/5=.04
        assert r["ece"] == pytest.approx(0.2)
        assert len(r["bins"]) == 5
        assert r["bins"][0] == {"n": 2, "konfidenz": pytest.approx(0.1), "trefferquote": pytest.approx(0.5)}
        assert r["bins"][4]["n"] == 3
        assert r["bins"][4]["trefferquote"] == pytest.approx(1.0)
        assert r["bins"][1]["n"] == 0


def _report(kosten_je_fall, eval_version="v1"):
    return {
        "eval_version": eval_version,
        "min_retrieval_score": 0.3,
        "kosten": {"fn": 1.0},
        "test_faelle": [{"id": f"f{i}", "gruppe": f"g{i % 12}", "kosten": k} for i, k in enumerate(kosten_je_fall)],
    }


class TestVergleiche:
    def test_kandidat_billiger_signifikant(self):
        a = _report([1.0] * 48)
        b = _report([0.5] * 48)
        r = vergleiche(a, b, b_resamples=500)
        assert r["differenz"] == pytest.approx(-0.5)
        assert r["signifikant"] is True
        assert r["grund"] is None

    def test_gleich_nicht_signifikant(self):
        a = _report([1.0, 0.0] * 24)
        r = vergleiche(a, a, b_resamples=500)
        assert r["differenz"] == 0
        assert r["signifikant"] is False

    def test_seed_stabil(self):
        rng = random.Random(1)
        a = _report([rng.random() for _ in range(48)])
        b = _report([rng.random() for _ in range(48)])
        assert vergleiche(a, b, seed=5, b_resamples=300) == vergleiche(a, b, seed=5, b_resamples=300)

    def test_gruppen_werden_gemeinsam_gezogen(self):
        # Eine Gruppe (g0) traegt die ganze Differenz: Cluster-CI muss breiter sein
        # als bei Einzelfall-Resampling (dort stuende jeder Fall fuer sich).
        n = 48
        a = _report([0.0] * n)
        b = _report([0.0] * n)
        for i in range(n):
            b["test_faelle"][i]["gruppe"] = "g0" if i < 12 else f"g{i}"
            a["test_faelle"][i]["gruppe"] = b["test_faelle"][i]["gruppe"]
            if i < 12:
                b["test_faelle"][i]["kosten"] = -1.0
        cluster = vergleiche(a, b, b_resamples=2000)
        einzeln = vergleiche(
            {**a, "test_faelle": [{**f, "gruppe": f["id"]} for f in a["test_faelle"]]},
            {**b, "test_faelle": [{**f, "gruppe": f["id"]} for f in b["test_faelle"]]},
            b_resamples=2000,
        )
        assert cluster["ci95"][1] - cluster["ci95"][0] > einzeln["ci95"][1] - einzeln["ci95"][0]

    def test_unter_10_gruppen_keine_aussage(self):
        a = {**_report([1.0] * 9), "test_faelle": [{"id": f"f{i}", "gruppe": f"g{i}", "kosten": 1.0} for i in range(9)]}
        b = {**a, "test_faelle": [{**f, "kosten": 0.0} for f in a["test_faelle"]]}
        r = vergleiche(a, b, b_resamples=100)
        assert r["signifikant"] is None
        assert "10" in r["grund"]

    def test_abweichende_eval_version(self):
        with pytest.raises(ValueError):
            vergleiche(_report([1.0] * 12), _report([1.0] * 12, eval_version="v2"))

    def test_abweichende_id_menge(self):
        b = _report([1.0] * 12)
        b["test_faelle"][0]["id"] = "anders"
        with pytest.raises(ValueError):
            vergleiche(_report([1.0] * 12), b)

    def test_default_resamples_schnell(self):
        import time

        rng = random.Random(0)
        a = _report([rng.random() for _ in range(200)])
        b = _report([rng.random() for _ in range(200)])
        t = time.perf_counter()
        vergleiche(a, b)
        assert time.perf_counter() - t < 3


def test_kalibriere_konsistent_mit_fit():
    assert kalibriere(0.7, 1.0) == pytest.approx(0.7)
