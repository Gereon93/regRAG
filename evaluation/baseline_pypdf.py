"""Baseline für ADR 0001: extrahiert dieselbe PDF mit pypdf statt pymupdf4llm.

Erzeugt einen zweiten Korpus, der über dieselbe Indexierungs-Pipeline läuft.
So wird der Retrieval-Unterschied zwischen naiver PDF-Extraktion und
Markdown-Konvertierung messbar, statt nur plausibel zu sein.

    python -m evaluation.baseline_pypdf
    REGRAG_DOCS_DIR=docs_md_pypdf REGRAG_CHROMA_DIR=chroma_pypdf \
        REGRAG_COLLECTION=dora_pypdf python -m evaluation.calibrate
"""

import json
import pathlib

from pypdf import PdfReader

PDF = pathlib.Path("docs/CELEX_32022R2554_DE_TXT.pdf")
AUSGABE = pathlib.Path("docs_md_pypdf")


def pdf_nach_text(pdf_pfad, ausgabe=AUSGABE):
    """Seitenweiser Text, wie ihn ein naiver PDF-Reader liefert — ohne Spaltenerkennung."""
    pdf_pfad = pathlib.Path(pdf_pfad)
    ausgabe = pathlib.Path(ausgabe)
    ausgabe.mkdir(parents=True, exist_ok=True)

    seiten = [seite.extract_text() or "" for seite in PdfReader(str(pdf_pfad)).pages]
    text = "\n".join(seiten)

    md_pfad = ausgabe / f"{pdf_pfad.stem}.md"
    md_pfad.write_text(text, encoding="utf-8")
    (ausgabe / f"{pdf_pfad.stem}.source.json").write_text(
        json.dumps({"titel": "Verordnung (EU) 2022/2554 (DORA), pypdf-Baseline",
                    "pdf": pdf_pfad.name}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return md_pfad


if __name__ == "__main__":
    pfad = pdf_nach_text(PDF)
    print("Text:", pfad, "-", len(pfad.read_text(encoding="utf-8")), "Zeichen")
