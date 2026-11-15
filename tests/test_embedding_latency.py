import itertools
import json
import math
import sys
from types import ModuleType, SimpleNamespace

import pytest

from evaluation import embedding_latency
from evaluation.embedding_latency import nearest_rank_p95


def test_nearest_rank_p95_bei_180_werten():
    assert nearest_rank_p95(list(range(1, 181))) == 171


class FakeModell:
    """Liefert einen normierten 2-dimensionalen Vektor; `vektor` lässt sich pro Test verbiegen."""

    model_name = "test/modell"

    def __init__(self, device, vektor=(0.6, 0.8)):
        self._device = device
        self._model = SimpleNamespace(get_embedding_dimension=lambda: 2)
        self.vektor = list(vektor)
        self.anfragen = []

    def get_query_embedding(self, frage):
        self.anfragen.append(frage)
        return self.vektor


@pytest.fixture
def torch_stub(monkeypatch):
    torch = ModuleType("torch")
    torch.__version__ = "0.0-test"
    torch.threads = 4
    torch.mps_syncs = 0

    def set_num_threads(n):
        torch.threads = n

    def synchronize():
        torch.mps_syncs += 1

    torch.set_num_threads = set_num_threads
    torch.get_num_threads = lambda: torch.threads
    torch.get_num_interop_threads = lambda: 1
    torch.mps = SimpleNamespace(synchronize=synchronize)
    monkeypatch.setitem(sys.modules, "torch", torch)
    return torch


@pytest.fixture
def messaufbau(monkeypatch, torch_stub):
    """Zwei Fragen plus die künstlich lange; jede Uhr-Abfrage rückt um genau eine Sekunde vor."""
    modelle = []

    def create_embed_model(device=None):
        modelle.append(FakeModell(device))
        return modelle[-1]

    uhr = itertools.count()
    monkeypatch.setattr(embedding_latency, "create_embed_model", create_embed_model)
    monkeypatch.setattr(embedding_latency, "ANTWORTBAR", ["A", "B"])
    monkeypatch.setattr(
        embedding_latency,
        "time",
        SimpleNamespace(perf_counter=lambda: float(next(uhr))),
    )
    return modelle


def test_messen_auf_cpu_misst_alle_fragen_nach_warmup(messaufbau, torch_stub):
    ergebnis = embedding_latency.messen("cpu", threads=2, warmup=2, repetitions=3)

    (modell,) = messaufbau
    lange = "A" + " B" * 8
    assert [s["query"] for s in ergebnis["samples"]] == ["A", "B", lange]
    assert all(s["seconds"] == [1.0, 1.0, 1.0] for s in ergebnis["samples"])
    assert len(modell.anfragen) == 3 * 2 + 3 * 3
    assert ergebnis["model_load_seconds"] == 1.0
    assert ergebnis["summary_seconds"] == {
        "median": 1.0,
        "p95": 1.0,
        "min": 1.0,
        "max": 1.0,
    }
    assert ergebnis["torch_threads"] == 2
    assert torch_stub.mps_syncs == 0
    assert {
        k: ergebnis[k] for k in ("dimension", "device", "model", "torch", "query_count")
    } == {
        "dimension": 2,
        "device": "cpu",
        "model": "test/modell",
        "torch": "0.0-test",
        "query_count": 3,
    }


def test_messen_auf_mps_synchronisiert_nach_laden_und_um_jede_messung(
    messaufbau, torch_stub
):
    fragen, wiederholungen, syncs_pro_messung, syncs_nach_laden = 3, 2, 2, 1

    embedding_latency.messen("mps", threads=None, warmup=0, repetitions=wiederholungen)

    assert (
        torch_stub.mps_syncs
        == syncs_nach_laden + fragen * wiederholungen * syncs_pro_messung
    )
    assert torch_stub.threads == 4


@pytest.mark.parametrize("vektor", [(1.0,), (math.nan, 1.0), (1.0, 1.0)])
def test_messen_bricht_bei_falscher_dimension_endlichkeit_oder_norm_ab(
    messaufbau, monkeypatch, vektor
):
    monkeypatch.setattr(
        embedding_latency,
        "create_embed_model",
        lambda device=None: FakeModell(device, vektor),
    )

    with pytest.raises(RuntimeError, match="Dimension, Endlichkeit oder Norm"):
        embedding_latency.messen("cpu", threads=None, warmup=0, repetitions=1)


def test_main_schreibt_ergebnis_als_json_datei_und_auf_stdout(
    messaufbau, monkeypatch, tmp_path, capsys
):
    ziel = tmp_path / "latenz.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "embedding_latency",
            "--device",
            "cpu",
            "--warmup",
            "0",
            "--repetitions",
            "1",
            "--output",
            str(ziel),
        ],
    )

    embedding_latency.main()

    gespeichert = json.loads(ziel.read_text())
    assert gespeichert["repetitions"] == 1
    assert gespeichert["warmup"] == 0
    assert json.loads(capsys.readouterr().out) == gespeichert
