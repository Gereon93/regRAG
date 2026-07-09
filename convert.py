import pymupdf4llm, pathlib

md = pymupdf4llm.to_markdown("docs/CELEX_32022R2554_DE_TXT.pdf")
pathlib.Path("docs_md").mkdir(exist_ok=True)
pathlib.Path("docs_md/dora.md").write_text(md, encoding="utf-8")

print("Zeichen:", len(md))
print("--- Erste 500 Zeichen ---")
print(md[:500])
