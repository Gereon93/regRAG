"""Sidecar-Segment -> Metadaten -> Node -> segment_von -> Schwelle im LayaGate (Spec #32, Abschnitt 4).

rag.py importiert chromadb/llama_index, die CI nicht installiert; sie werden hier gestubbt.
"""

import importlib
import json
import sys
import types
from types import SimpleNamespace

import httpx
import pytest

import config
from gate import UNBEKANNT, Outcome
from laya import LayaGate, SystemOneClient

SCHWELLEN = {"verordnung": 0.4, "gesetz": 0.6}
ABGELEITETE_KEYS = ["file_path", "file_name"]


class FakeReader:
    def __init__(self, input_files, file_metadata):
        self.input_files = input_files
        self.file_metadata = file_metadata

    def load_data(self):
        return [
            SimpleNamespace(
                metadata=self.file_metadata(pfad),
                excluded_embed_metadata_keys=list(ABGELEITETE_KEYS),
                excluded_llm_metadata_keys=list(ABGELEITETE_KEYS),
            )
            for pfad in self.input_files
        ]


class FakeIndex:
    def __init__(self):
        self.eingefuegt = []

    def insert(self, dok):
        self.eingefuegt.append(dok)


def _stub(name, **attrs):
    modul = types.ModuleType(name)
    for schluessel, wert in attrs.items():
        setattr(modul, schluessel, wert)
    return modul


@pytest.fixture
def rag(tmp_path, monkeypatch):
    # rag baut beim Import den Index: Verzeichnisse umlenken, Chroma/Embedding stubben
    monkeypatch.setattr(config, "CHROMA_VERZEICHNIS", str(tmp_path / "chroma"))
    monkeypatch.setattr(config, "DOKUMENTE_VERZEICHNIS", str(tmp_path / "leer"))
    fake_collection = SimpleNamespace(count=lambda: 0, delete=lambda **kw: None)
    fake_client = SimpleNamespace(
        get_or_create_collection=lambda *a, **kw: fake_collection, delete_collection=lambda *a, **kw: None
    )
    stubs = {
        "embedding": _stub("embedding", create_embed_model=lambda device=None: None),
        "chromadb": _stub("chromadb", PersistentClient=lambda path: fake_client),
        "llama_index": _stub("llama_index"),
        "llama_index.core": _stub(
            "llama_index.core",
            Settings=SimpleNamespace(),
            SimpleDirectoryReader=FakeReader,
            StorageContext=SimpleNamespace(from_defaults=lambda **kw: None),
            VectorStoreIndex=SimpleNamespace(from_vector_store=lambda *a, **kw: None),
        ),
        "llama_index.vector_stores": _stub("llama_index.vector_stores"),
        "llama_index.vector_stores.chroma": _stub("llama_index.vector_stores.chroma", ChromaVectorStore=lambda **kw: None),
    }
    alt = {name: sys.modules.get(name) for name in [*stubs, "rag"]}
    sys.modules.pop("rag", None)
    sys.modules.update(stubs)
    try:
        modul = importlib.import_module("rag")
        modul.FINGERPRINT_DATEI = tmp_path / "chroma" / "fingerprint.json"
        modul.index = FakeIndex()
        modul._collection = SimpleNamespace(delete=lambda **kw: None)
        yield modul
    finally:
        for name, vorher in alt.items():
            if vorher is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = vorher


def _dokument(tmp_path, sidecar):
    md = tmp_path / "dok.md"
    md.write_text("# Text\n", encoding="utf-8")
    if sidecar is not None:
        md.with_suffix(".source.json").write_text(json.dumps(sidecar), encoding="utf-8")
    return md


def _laya(wert):
    def handler(request):
        return httpx.Response(200, json={"answers": {"answerable": {"noul": wert}}})

    client = SystemOneClient("http://laya.test:8000", transport=httpx.MockTransport(handler))
    return LayaGate(client, lambda segment: SCHWELLEN.get(segment, 0.9))


def _node(dok):
    return SimpleNamespace(score=0.9, text="t", metadata=dok.metadata)


def test_sidecar_segment_landet_in_metadaten_und_bestimmt_die_laya_schwelle(rag, tmp_path):
    rag.indexiere(_dokument(tmp_path, {"titel": "DORA", "pdf": "d.pdf", "segment": "verordnung"}))

    (dok,) = rag.index.eingefuegt
    assert dok.metadata["segment"] == "verordnung"
    # 0.5 liegt ueber der Verordnungs-Schwelle 0.4, aber unter dem Fallback 0.9
    assert _laya(0.5).entscheide("f", [_node(dok)]).outcome is Outcome.ANSWER


@pytest.mark.parametrize("sidecar", [None, {"titel": "X"}, {"segment": "quatsch"}], ids=["ohne_sidecar", "ohne_segment", "unbekannt"])
def test_fehlendes_oder_unbekanntes_segment_wird_unbekannt_und_nutzt_den_fallback(rag, tmp_path, sidecar):
    rag.indexiere(_dokument(tmp_path, sidecar))

    (dok,) = rag.index.eingefuegt
    assert dok.metadata["segment"] == UNBEKANNT
    assert _laya(0.5).entscheide("f", [_node(dok)]).outcome is Outcome.ABSTAIN


def test_segment_fliesst_nicht_in_den_embedding_text_und_das_llm_prompt(rag, tmp_path):
    rag.indexiere(_dokument(tmp_path, {"segment": "gesetz"}))

    (dok,) = rag.index.eingefuegt
    assert "segment" in dok.excluded_embed_metadata_keys
    assert "segment" in dok.excluded_llm_metadata_keys
    assert set(ABGELEITETE_KEYS) <= set(dok.excluded_embed_metadata_keys)
    assert set(ABGELEITETE_KEYS) <= set(dok.excluded_llm_metadata_keys)


def test_upload_sidecar_schreibt_nie_ein_segment(tmp_path):
    """Invariante: Uploads bekommen nie ein Segment ausser unbekannt (Feld fehlt -> UNBEKANNT)."""
    stubs = {
        "pymupdf": _stub("pymupdf", open=lambda p: [SimpleNamespace(get_text=lambda: "VERORDNUNG (EU) 2022/2554")]),
        "pymupdf4llm": _stub("pymupdf4llm", to_markdown=lambda p: "# md"),
    }
    alt = {name: sys.modules.get(name) for name in [*stubs, "convert"]}
    sys.modules.pop("convert", None)
    sys.modules.update(stubs)
    try:
        convert = importlib.import_module("convert")
        md = convert.pdf_nach_markdown(tmp_path / "upload.pdf", tmp_path / "out")
    finally:
        for name, vorher in alt.items():
            if vorher is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = vorher

    daten = json.loads(md.with_suffix(".source.json").read_text(encoding="utf-8"))
    assert "segment" not in daten
