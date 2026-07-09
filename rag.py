import os
from contextlib import suppress

import chromadb
from llama_index.core import (
    Settings,
    SimpleDirectoryReader,
    StorageContext,
    VectorStoreIndex,
)
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.vector_stores.chroma import ChromaVectorStore

EMBEDDING_MODELL = "BAAI/bge-m3"
DOKUMENTE_VERZEICHNIS = "docs_md"
CHROMA_VERZEICHNIS = "chroma"
COLLECTION = "dora"
DISTANZMETRIK_WIE_IN_MEMORY = {"hnsw:space": "cosine"}  # docs/adr/0003
NEU_BAUEN = os.getenv("REGRAG_INDEX_NEU_BAUEN") == "1"

Settings.embed_model = HuggingFaceEmbedding(model_name=EMBEDDING_MODELL)


def _collection(client):
    if NEU_BAUEN:
        with suppress(Exception):
            client.delete_collection(COLLECTION)
    return client.get_or_create_collection(COLLECTION, metadata=DISTANZMETRIK_WIE_IN_MEMORY)


def lade_oder_baue_index():
    client = chromadb.PersistentClient(path=CHROMA_VERZEICHNIS)
    collection = _collection(client)
    vector_store = ChromaVectorStore(chroma_collection=collection)

    if collection.count() > 0:
        return VectorStoreIndex.from_vector_store(vector_store)

    dokumente = SimpleDirectoryReader(DOKUMENTE_VERZEICHNIS).load_data()
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    return VectorStoreIndex.from_documents(dokumente, storage_context=storage_context)


index = lade_oder_baue_index()

if __name__ == "__main__":
    from llama_index.llms.openai_like import OpenAILike

    Settings.llm = OpenAILike(model="google/gemma-4-12b",
        api_base="http://localhost:1234/v1", api_key="lm-studio",
        is_chat_model=True, timeout=300)

    engine = index.as_query_engine(similarity_top_k=2)

    resp = engine.query("Welche Anforderungen stellt DORA an das IKT-Risikomanagement?")
    print(resp)
    print("\n--- Quellen ---")
    for n in resp.source_nodes:
        print(round(n.score, 2), n.metadata.get("file_name"))
