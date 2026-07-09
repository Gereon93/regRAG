from llama_index.core import VectorStoreIndex, SimpleDirectoryReader, Settings
from llama_index.embeddings.huggingface import HuggingFaceEmbedding

Settings.embed_model = HuggingFaceEmbedding(model_name="BAAI/bge-m3")

docs = SimpleDirectoryReader("docs_md").load_data()
index = VectorStoreIndex.from_documents(docs)

if __name__ == "__main__":
    from llama_index.llms.openai_like import OpenAILike

    Settings.llm = OpenAILike(model="google/gemma-4-12b",
    api_base="http://localhost:1234/v1", api_key="lm-studio", is_chat_model=True,  timeout=300)

    engine = index.as_query_engine(similarity_top_k=2)

    resp = engine.query("Welche Anforderungen stellt DORA an das IKT-Risikomanagement?")
    print(resp)
    print("\n--- Quellen ---")
    for n in resp.source_nodes:
        print(round(n.score, 2), n.metadata)
