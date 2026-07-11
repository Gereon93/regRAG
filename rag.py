import hashlib
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

DISTANZMETRIK_WIE_IN_MEMORY = {"hnsw:space": "cosine"}  # docs/adr/0003
NEU_BAUEN = os.getenv("REGRAG_INDEX_NEU_BAUEN") == "1"
FINGERPRINT_DATEI = Path(config.CHROMA_VERZEICHNIS) / "fingerprint.json"


def _embed_model():
    from llama_index.embeddings.huggingface import HuggingFaceEmbedding

    Settings.embed_model = HuggingFaceEmbedding(model_name=config.EMBEDDING_MODELL)


def _dokumente_verzeichnis():
    pfad = Path(config.DOKUMENTE_VERZEICHNIS)
    md_dateien = sorted(pfad.glob("*.md")) if pfad.is_dir() else []
    if not md_dateien:
        raise FileNotFoundError(
            f"Kein Markdown in {pfad}/ gefunden. Erst 'python convert.py' ausführen."
        )
    return pfad, md_dateien


def _fingerprint(md_dateien):
    h = hashlib.sha256()
    for datei in md_dateien:
        h.update(datei.read_bytes())
    return {
        "dokumente": h.hexdigest(),
        "embedding_modell": config.EMBEDDING_MODELL,
        "metrik": DISTANZMETRIK_WIE_IN_MEMORY["hnsw:space"],
    }


def _fingerprint_passt(erwartet):
    if not FINGERPRINT_DATEI.exists():
        return False
    return json.loads(FINGERPRINT_DATEI.read_text()) == erwartet


def _baue(client, md_dateien, fingerprint):
    FINGERPRINT_DATEI.unlink(missing_ok=True)
    with suppress(Exception):
        client.delete_collection(config.COLLECTION)
    collection = client.get_or_create_collection(
        config.COLLECTION, metadata=DISTANZMETRIK_WIE_IN_MEMORY
    )
    vector_store = ChromaVectorStore(chroma_collection=collection)
    dokumente = SimpleDirectoryReader(
        input_files=[str(p) for p in md_dateien]
    ).load_data()
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    index = VectorStoreIndex.from_documents(dokumente, storage_context=storage_context)
    FINGERPRINT_DATEI.write_text(json.dumps(fingerprint, indent=2))
    return index


def lade_oder_baue_index():
    _embed_model()
    _verzeichnis, md_dateien = _dokumente_verzeichnis()
    fingerprint = _fingerprint(md_dateien)

    client = chromadb.PersistentClient(path=config.CHROMA_VERZEICHNIS)
    collection = client.get_or_create_collection(
        config.COLLECTION, metadata=DISTANZMETRIK_WIE_IN_MEMORY
    )
    vector_store = ChromaVectorStore(chroma_collection=collection)

    aktuell = not NEU_BAUEN and collection.count() > 0 and _fingerprint_passt(fingerprint)
    if aktuell:
        return VectorStoreIndex.from_vector_store(vector_store)

    return _baue(client, md_dateien, fingerprint)


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
