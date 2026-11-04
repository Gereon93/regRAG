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
from gueltigkeit import MetadatenFehler
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


def _konvertiere(tmp_path, **kwargs):
    stubs = {
        "pymupdf": _stub("pymupdf", open=lambda p: [SimpleNamespace(get_text=lambda: "VERORDNUNG (EU) 2022/2554")]),
        "pymupdf4llm": _stub("pymupdf4llm", to_markdown=lambda p: "# md"),
    }
    alt = {name: sys.modules.get(name) for name in [*stubs, "convert"]}
    sys.modules.pop("convert", None)
    sys.modules.update(stubs)
    try:
        convert = importlib.import_module("convert")
        md = convert.pdf_nach_markdown(tmp_path / "upload.pdf", tmp_path / "out", **kwargs)
    finally:
        for name, vorher in alt.items():
            if vorher is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = vorher
    return md.with_suffix(".source.json")


def test_upload_sidecar_schreibt_nie_ein_segment(tmp_path):
    """Invariante: Uploads bekommen nie ein Segment ausser unbekannt (Feld fehlt -> UNBEKANNT)."""
    daten = json.loads(_konvertiere(tmp_path).read_text(encoding="utf-8"))
    assert "segment" not in daten


def test_upload_sidecar_uebernimmt_gueltigkeit(tmp_path):
    sidecar = _konvertiere(tmp_path, metadaten={"gueltig_ab": "2026-01-01", "fassung": "2"})

    daten = json.loads(sidecar.read_text(encoding="utf-8"))
    assert daten["gueltig_ab"] == "2026-01-01"
    assert daten["fassung"] == "2"
    assert "titel" in daten


def test_upload_sidecar_ohne_metadaten_bleibt_byte_identisch(tmp_path):
    ohne = _konvertiere(tmp_path).read_bytes()

    assert _konvertiere(tmp_path, metadaten={}).read_bytes() == ohne


def test_gueltigkeit_landet_in_den_metadaten_und_fehlende_felder_fehlen(rag, tmp_path):
    rag.indexiere(_dokument(tmp_path, {"gueltig_ab": "2026-01-01", "fassung": " 2 "}))

    (dok,) = rag.index.eingefuegt
    assert dok.metadata["gueltig_ab"] == "2026-01-01"
    assert dok.metadata["fassung"] == "2"
    assert "gueltig_bis" not in dok.metadata


def test_ohne_sidecar_gibt_es_keine_gueltigkeitsschluessel(rag, tmp_path):
    rag.indexiere(_dokument(tmp_path, None))

    (dok,) = rag.index.eingefuegt
    assert not {"gueltig_ab", "gueltig_bis", "fassung"} & set(dok.metadata)


def test_gate_metadaten_fliessen_weder_in_embedding_noch_llm(rag, tmp_path):
    rag.indexiere(_dokument(tmp_path, {"gueltig_ab": "2026-01-01"}))

    (dok,) = rag.index.eingefuegt
    assert set(rag.GATE_METADATEN) == {"segment", "gueltig_ab", "gueltig_bis", "fassung"}
    assert set(rag.GATE_METADATEN) <= set(dok.excluded_embed_metadata_keys)
    assert set(rag.GATE_METADATEN) <= set(dok.excluded_llm_metadata_keys)
    assert set(ABGELEITETE_KEYS) <= set(dok.excluded_embed_metadata_keys)


@pytest.mark.parametrize(
    "inhalt",
    ['{"gueltig_ab": "2026-02-30"}', "{kaputt", '["liste"]'],
    ids=["datum", "json", "liste"],
)
def test_kaputter_sidecar_wirft_metadatenfehler_mit_dateiname(rag, tmp_path, inhalt):
    md = _dokument(tmp_path, None)
    md.with_suffix(".source.json").write_text(inhalt, encoding="utf-8")

    with pytest.raises(MetadatenFehler, match="dok.md"):
        rag._quelle_metadata(md)


@pytest.fixture
def start(rag, tmp_path, monkeypatch):
    verzeichnis = tmp_path / "docs"
    verzeichnis.mkdir()
    monkeypatch.setattr(config, "DOKUMENTE_VERZEICHNIS", str(verzeichnis))
    geloescht = []
    collection = SimpleNamespace(count=lambda: 1, delete=lambda where: geloescht.append(where["file_name"]))
    client = SimpleNamespace(
        get_or_create_collection=lambda *a, **kw: collection, delete_collection=lambda *a, **kw: None
    )
    monkeypatch.setattr(sys.modules["chromadb"], "PersistentClient", lambda path: client)

    def schreibe(name, sidecar):
        md = verzeichnis / f"{name}.md"
        md.write_text(f"# {name}\n", encoding="utf-8")
        md.with_suffix(".source.json").write_text(sidecar, encoding="utf-8")

    def lauf():
        fake = FakeIndex()
        monkeypatch.setattr(rag.VectorStoreIndex, "from_vector_store", lambda *a, **kw: fake)
        rag.lade_oder_baue_index()
        return [dok.metadata["file_name"] for dok in fake.eingefuegt]

    return SimpleNamespace(schreibe=schreibe, lauf=lauf, geloescht=geloescht)


GUT = '{"gueltig_ab": "2026-01-01"}'
KAPUTT = '{"gueltig_ab": "2026-02-30"}'


def test_start_ueberspringt_kaputtes_dokument_und_nimmt_den_altstand_heraus(rag, start, caplog):
    start.schreibe("a", GUT)
    start.schreibe("b", GUT)
    assert sorted(start.lauf()) == ["a.md", "b.md"]
    start.geloescht.clear()

    start.schreibe("a", KAPUTT)
    eingefuegt = start.lauf()

    assert eingefuegt == []
    assert "a.md" in start.geloescht
    assert any(r.levelname == "ERROR" and "a.md" in r.getMessage() for r in caplog.records)
    fingerprint = json.loads(rag.FINGERPRINT_DATEI.read_text(encoding="utf-8"))
    assert set(fingerprint["dokumente"]) == {"b.md"}


def test_kaputtes_dokument_kostet_beim_naechsten_start_nichts_und_repariert_sich(start):
    start.schreibe("a", GUT)
    start.schreibe("b", GUT)
    start.lauf()
    start.schreibe("a", KAPUTT)
    start.lauf()

    assert start.lauf() == []

    start.schreibe("a", '{"gueltig_ab": "2026-02-28"}')
    assert start.lauf() == ["a.md"]
