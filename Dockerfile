FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    HF_HOME=/opt/hf \
    TOKENIZERS_PARALLELISM=false \
    LLAMA_INDEX_CACHE_DIR=/opt/hf/hub

# Das Modell vor den Requirements backen: eine geänderte requirements.txt lädt bge-m3 nicht neu herunter.
# Der Cache gehört dem Laufzeitnutzer, weil llama-index dort schreibt; chown im selben Layer kopiert nichts.
RUN useradd --uid 1000 --create-home regrag \
    && pip install --no-cache-dir huggingface_hub \
    && python -c "from huggingface_hub import snapshot_download; snapshot_download('BAAI/bge-m3')" \
    && chown -R regrag:regrag /opt/hf

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Der Code gehört root und ist für den Laufzeitnutzer nur lesbar. Kein rekursives COPY ., das .env oder lokale
# Artefakte mitnehmen könnte.
COPY --chmod=755 entrypoint.sh ./
COPY *.py ./
COPY web/ web/
COPY evaluation/ evaluation/

# Schreibbar sind nur /app selbst (deepeval legt .deepeval im Arbeitsverzeichnis an), die Volumes und die
# Eval-Ausgaben. chroma und docs_md vorab anlegen, damit neue Volumes beim ersten Start diese Rechte übernehmen.
RUN mkdir -p chroma docs_md evaluation/reports \
    && chown regrag:regrag /app chroma docs_md evaluation/manifest evaluation/reports

USER regrag

EXPOSE 8000
ENTRYPOINT ["./entrypoint.sh"]
