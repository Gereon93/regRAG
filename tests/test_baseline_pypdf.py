"""pypdf-Baseline (ADR 0001) in der CI: pypdf ist gestubbt, geprüft wird die erzeugte Korpus-Datei."""

import importlib
import json
import runpy
import sys
from types import ModuleType, SimpleNamespace
from typing import ClassVar

import pytest


class FakeReader:
    """Drei Seiten, eine davon ohne extrahierbaren Text (pypdf liefert dann None)."""

    gelesen: ClassVar[list] = []

    def __init__(self, pfad):
        FakeReader.gelesen.append(pfad)
        self.pages = [
            SimpleNamespace(extract_text=lambda: "Artikel 1"),
            SimpleNamespace(extract_text=lambda: None),
            SimpleNamespace(extract_text=lambda: "Artikel 2"),
        ]


@pytest.fixture
def pypdf_stub(monkeypatch):
    FakeReader.gelesen = []
    modul = ModuleType("pypdf")
    modul.PdfReader = FakeReader
    monkeypatch.setitem(sys.modules, "pypdf", modul)
    monkeypatch.delitem(sys.modules, "evaluation.baseline_pypdf", raising=False)
    yield
    sys.modules.pop("evaluation.baseline_pypdf", None)


def test_pdf_nach_text_schreibt_seiten_und_sidecar(pypdf_stub, tmp_path):
    baseline = importlib.import_module("evaluation.baseline_pypdf")
    ausgabe = tmp_path / "neu" / "korpus"

    md_pfad = baseline.pdf_nach_text(tmp_path / "dora.pdf", ausgabe)

    assert md_pfad == ausgabe / "dora.md"
    assert md_pfad.read_text(encoding="utf-8") == "Artikel 1\n\nArtikel 2"
    assert FakeReader.gelesen == [str(tmp_path / "dora.pdf")]
    sidecar = json.loads((ausgabe / "dora.source.json").read_text(encoding="utf-8"))
    assert sidecar == {
        "titel": "Verordnung (EU) 2022/2554 (DORA), pypdf-Baseline",
        "pdf": "dora.pdf",
    }


def test_hauptprogramm_erzeugt_baseline_im_standardverzeichnis(
    pypdf_stub, tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)

    runpy.run_module("evaluation.baseline_pypdf", run_name="__main__")

    md_pfad = tmp_path / "docs_md_pypdf" / "CELEX_32022R2554_DE_TXT.md"
    assert md_pfad.exists()
    assert FakeReader.gelesen == ["docs/CELEX_32022R2554_DE_TXT.pdf"]
    assert (
        "Text: docs_md_pypdf/CELEX_32022R2554_DE_TXT.md - 20 Zeichen"
        in capsys.readouterr().out
    )
