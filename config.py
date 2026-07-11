import os

EMBEDDING_MODELL = os.getenv("REGRAG_EMBEDDING_MODELL", "BAAI/bge-m3")
DOKUMENTE_VERZEICHNIS = os.getenv("REGRAG_DOCS_DIR", "docs_md")
CHROMA_VERZEICHNIS = os.getenv("REGRAG_CHROMA_DIR", "chroma")
COLLECTION = os.getenv("REGRAG_COLLECTION", "dora")

LLM_BASE_URL = os.getenv("REGRAG_LLM_BASE_URL", "http://localhost:1234/v1")
LLM_MODELL = os.getenv("REGRAG_CHAT_MODELL", "google/gemma-4-12b")
LLM_API_KEY = os.getenv("REGRAG_API_KEY", "lm-studio")
LLM_TEMPERATURE = float(os.getenv("REGRAG_TEMPERATURE", "0"))
LLM_TIMEOUT = float(os.getenv("REGRAG_TIMEOUT", "300"))

MIN_RETRIEVAL_SCORE = float(os.getenv("REGRAG_MIN_SCORE", "0.62"))

JUDGE_MODELL = os.getenv("REGRAG_JUDGE_MODELL", "qwen/qwen3.5-9b")

