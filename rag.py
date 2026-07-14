import json
import os
from contextlib import suppress
from pathlib import Path

import chromadb
from llama_index.core import (
    Settings,
    SimpleDirectoryReader,
    StorageContext,
    VectorStoreIndex,
)
from llama_index.vector_stores.chroma import ChromaVectorStore

import config
import dokumente

DISTANZMETRIK_WIE_IN_MEMORY = {"hnsw:space": "cosine"}  # docs/adr/0003
METRIK = DISTANZMETRIK_WIE_IN_MEMORY["hnsw:space"]
NEU_BAUEN = os.getenv("REGRAG_INDEX_NEU_BAUEN") == "1"
FINGERPRINT_DATEI = Path(config.CHROMA_VERZEICHNIS) / "fingerprint.json"

index = None
_collection = None


def _embed_model():
    from llama_index.embeddings.huggingface import HuggingFaceEmbedding

    Settings.embed_model = HuggingFaceEmbedding(model_name=config.EMBEDDING_MODELL)


def _md_dateien():
    pfad = Path(config.DOKUMENTE_VERZEICHNIS)
    return sorted(pfad.glob("*.md")) if pfad.is_dir() else []


def _quelle_metadata(pfad):
    p = Path(pfad)
    sidecar = p.with_suffix(".source.json")
    daten = json.loads(sidecar.read_text()) if sidecar.exists() else {}
    return {
        "file_name": p.name,
        "quelle": daten.get("titel", p.name),
        "pdf": daten.get("pdf"),
    }


def _fingerprint_lesen():
    if not FINGERPRINT_DATEI.exists():
        return {}
    return json.loads(FINGERPRINT_DATEI.read_text())


def _fingerprint_schreiben(fp):
    FINGERPRINT_DATEI.parent.mkdir(parents=True, exist_ok=True)
    FINGERPRINT_DATEI.write_text(json.dumps(fp, indent=2, ensure_ascii=False))


def loesche_nodes(dateiname):
    _collection.delete(where={"file_name": dateiname})


def loesche_dokument(md_name):
    """Fingerprint zuerst, dann Nodes, dann Dateien — ein Abbruch führt höchstens zu einmal zu viel indexieren."""
    fp = _fingerprint_lesen() or dokumente.leerer_fingerprint(config.EMBEDDING_MODELL, METRIK)
    fp["dokumente"].pop(md_name, None)
    _fingerprint_schreiben(fp)

    loesche_nodes(md_name)

    md_pfad = Path(config.DOKUMENTE_VERZEICHNIS) / md_name
    md_pfad.unlink(missing_ok=True)
    md_pfad.with_suffix(".source.json").unlink(missing_ok=True)


def indexiere(md_pfad):
    """Merged ein Dokument in die bestehende Collection und schreibt den Fingerprint fort."""
    md_pfad = Path(md_pfad)
    loesche_nodes(md_pfad.name)

    for dok in SimpleDirectoryReader(
        input_files=[str(md_pfad)], file_metadata=_quelle_metadata
    ).load_data():
        index.insert(dok)

    fp = _fingerprint_lesen() or dokumente.leerer_fingerprint(config.EMBEDDING_MODELL, METRIK)
    fp["dokumente"][md_pfad.name] = dokumente.datei_hash(md_pfad)
    _fingerprint_schreiben(fp)


def lade_oder_baue_index():
    global index, _collection
    _embed_model()

    neu = dokumente.fingerprint(_md_dateien(), config.EMBEDDING_MODELL, METRIK)
    alt = {} if NEU_BAUEN else _fingerprint_lesen()

    client = chromadb.PersistentClient(path=config.CHROMA_VERZEICHNIS)
    _collection = client.get_or_create_collection(
        config.COLLECTION, metadata=DISTANZMETRIK_WIE_IN_MEMORY
    )
    if _collection.count() == 0:
        alt = {}

    zu_indexieren, zu_loeschen, voll_rebuild = dokumente.diff(alt, neu)

    if voll_rebuild or NEU_BAUEN:
        FINGERPRINT_DATEI.unlink(missing_ok=True)
        with suppress(Exception):
            client.delete_collection(config.COLLECTION)
        _collection = client.get_or_create_collection(
            config.COLLECTION, metadata=DISTANZMETRIK_WIE_IN_MEMORY
        )
        zu_loeschen = []

    vector_store = ChromaVectorStore(chroma_collection=_collection)
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    index = VectorStoreIndex.from_vector_store(vector_store, storage_context=storage_context)

    fp = _fingerprint_lesen() or dokumente.leerer_fingerprint(config.EMBEDDING_MODELL, METRIK)
    for name in zu_loeschen:
        loesche_nodes(name)
        fp["dokumente"].pop(name, None)
    _fingerprint_schreiben(fp)

    for name in zu_indexieren:
        indexiere(Path(config.DOKUMENTE_VERZEICHNIS) / name)

    return index


index = lade_oder_baue_index()

if __name__ == "__main__":
    from llama_index.llms.openai_like import OpenAILike

    Settings.llm = OpenAILike(model=config.LLM_MODELL, api_base=config.LLM_BASE_URL,
        api_key=config.LLM_API_KEY, is_chat_model=True, timeout=config.LLM_TIMEOUT)

    engine = index.as_query_engine(similarity_top_k=2)

    resp = engine.query("Welche Anforderungen stellt DORA an das IKT-Risikomanagement?")
    print(resp)
    print("\n--- Quellen ---")
    for n in resp.source_nodes:
        print(round(n.score, 2), n.metadata.get("file_name"))
