import json
import pathlib
import re

import pymupdf
import pymupdf4llm

PDF = pathlib.Path("docs/CELEX_32022R2554_DE_TXT.pdf")
AUSGABE = pathlib.Path("docs_md")

if not PDF.exists():
    raise FileNotFoundError(
        f"{PDF} nicht gefunden. DORA-PDF von "
        "https://eur-lex.europa.eu/legal-content/DE/TXT/PDF/?uri=CELEX:32022R2554 "
        "herunterladen und nach docs/CELEX_32022R2554_DE_TXT.pdf speichern."
    )


def dokument_titel(pdf_pfad, fallback):
    text = pymupdf.open(pdf_pfad)[0].get_text()
    nummer = re.search(r"VERORDNUNG \(EU\)\s*([0-9]+/[0-9]+)", text)
    return f"Verordnung (EU) {nummer.group(1)} (DORA)" if nummer else fallback


md = pymupdf4llm.to_markdown(str(PDF))

AUSGABE.mkdir(exist_ok=True)
for alt in AUSGABE.glob("*.md"):
    alt.unlink()

stamm = PDF.stem
(AUSGABE / f"{stamm}.md").write_text(md, encoding="utf-8")
(AUSGABE / f"{stamm}.source.json").write_text(
    json.dumps({"titel": dokument_titel(PDF, PDF.name), "pdf": PDF.name},
               ensure_ascii=False, indent=2),
    encoding="utf-8",
)

print("Titel:", dokument_titel(PDF, PDF.name))
print("Zeichen:", len(md))
