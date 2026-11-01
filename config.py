import math
import os
from urllib.parse import urlsplit

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
ESKALATION_DATEI = os.getenv("REGRAG_ESKALATION_DATEI") or os.path.join(CHROMA_VERZEICHNIS, "eskalationen.jsonl")

LAYA_URL = os.getenv("REGRAG_LAYA_URL", "").strip()
LAYA_API_KEY = os.getenv("REGRAG_LAYA_API_KEY", "")
LAYA_TIMEOUT = float(os.getenv("REGRAG_LAYA_TIMEOUT", "30"))
LAYA_MAX_LEN = int(os.getenv("REGRAG_LAYA_MAX_LEN", "4096"))
LAYA_LANG = os.getenv("REGRAG_LAYA_LANG", "de")
LAYA_MIN_ANSWERABLE = float(os.getenv("REGRAG_LAYA_MIN_ANSWERABLE", "0.5"))
LAYA_MODELL = os.getenv("REGRAG_LAYA_MODELL", "multilingual").strip()
GATE_MANIFEST = os.getenv("REGRAG_GATE_MANIFEST", "").strip()

if not LAYA_MODELL:
    raise ValueError("REGRAG_LAYA_MODELL darf nicht leer sein.")
if not 0 <= LAYA_MIN_ANSWERABLE <= 1:
    raise ValueError("REGRAG_LAYA_MIN_ANSWERABLE muss zwischen 0 und 1 liegen.")
if urlsplit(LAYA_URL).username or urlsplit(LAYA_URL).password:
    raise ValueError("REGRAG_LAYA_URL darf keine Zugangsdaten enthalten; der Key gehört in REGRAG_LAYA_API_KEY.")

JUDGE_MODELL = os.getenv("REGRAG_JUDGE_MODELL") or "qwen/qwen3.5-9b"
JUDGE_BASE_URL = os.getenv("REGRAG_JUDGE_BASE_URL") or LLM_BASE_URL
JUDGE_API_KEY = os.getenv("REGRAG_JUDGE_API_KEY") or LLM_API_KEY
JUDGE_TIMEOUT = float(os.getenv("REGRAG_JUDGE_TIMEOUT") or LLM_TIMEOUT)

KOSTEN_FALSCHANTWORT = float(os.getenv("REGRAG_KOSTEN_FALSCHANTWORT", "10"))
KOSTEN_ESKALATION = float(os.getenv("REGRAG_KOSTEN_ESKALATION", "1"))
if not all(math.isfinite(k) and k >= 0 for k in (KOSTEN_FALSCHANTWORT, KOSTEN_ESKALATION)) or not (
    KOSTEN_FALSCHANTWORT or KOSTEN_ESKALATION
):
    raise ValueError("REGRAG_KOSTEN_* müssen endlich und >= 0 sein, mindestens eines > 0.")
EVAL_BERICHT_VERZEICHNIS = os.getenv("REGRAG_EVAL_BERICHT_DIR", "evaluation/reports")
