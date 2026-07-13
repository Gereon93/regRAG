import json
import pathlib
import re

import pymupdf
import pymupdf4llm

PDF = pathlib.Path("docs/CELEX_32022R2554_DE_TXT.pdf")
AUSGABE = pathlib.Path("docs_md")


def dokument_titel(pdf_pfad, fallback):
    text = pymupdf.open(str(pdf_pfad))[0].get_text()
    nummer = re.search(r"VERORDNUNG \(EU\)\s*([0-9]+/[0-9]+)", text)
    return f"Verordnung (EU) {nummer.group(1)} (DORA)" if nummer else fallback


def pdf_nach_markdown(pdf_pfad, ausgabe=AUSGABE):
    """Konvertiert eine PDF nach <ausgabe>/<stamm>.md samt Quellen-Sidecar, gibt den Markdown-Pfad zurück."""
    pdf_pfad = pathlib.Path(pdf_pfad)
    ausgabe = pathlib.Path(ausgabe)
    ausgabe.mkdir(parents=True, exist_ok=True)

    stamm = pdf_pfad.stem
    md = pymupdf4llm.to_markdown(str(pdf_pfad))
    md_pfad = ausgabe / f"{stamm}.md"
    md_pfad.write_text(md, encoding="utf-8")
    (ausgabe / f"{stamm}.source.json").write_text(
        json.dumps({"titel": dokument_titel(pdf_pfad, stamm), "pdf": pdf_pfad.name},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return md_pfad


if __name__ == "__main__":
    if not PDF.exists():
        raise FileNotFoundError(
            f"{PDF} nicht gefunden. DORA-PDF von "
            "https://eur-lex.europa.eu/legal-content/DE/TXT/PDF/?uri=CELEX:32022R2554 "
            "herunterladen und nach docs/CELEX_32022R2554_DE_TXT.pdf speichern."
        )
    pfad = pdf_nach_markdown(PDF)
    print("Titel:", dokument_titel(PDF, PDF.stem))
    print("Markdown:", pfad, "-", len(pfad.read_text(encoding="utf-8")), "Zeichen")
