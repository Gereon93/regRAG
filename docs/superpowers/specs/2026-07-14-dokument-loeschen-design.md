# Dokumente wieder aus dem Index entfernen (Issue #16)

## Ziel

Ein hochgeladenes Dokument über die Web-UI wieder loswerden: Nodes aus Chroma, `.md` und
`.source.json` aus `docs_md/`, Eintrag aus `fingerprint.json`. Heute geht das nur, indem man die
`.md` von Hand aus dem Volume löscht.

## Grundsatzentscheidung: RegRAG ist korpus-agnostisch

DORA ist der **Seed** des Korpus, nicht sein Zweck. Der Name meint nicht „DORA-RAG", sondern ein
System, das nur antwortet, was die indexierten Dokumente belegen (ADR 0002: Abstain statt Raten).
Wer DORA löscht und das BGB hochlädt, bekommt ein BGB-RAG — mit denselben Garantien.

Daraus folgt: **kein Sonderschutz für DORA.** Es ist löschbar wie jedes andere Dokument.

Das erzwingt eine Korrektur in `entrypoint.sh`. Heute:

```sh
if ! ls docs_md/*.md >/dev/null 2>&1 && [ -f "docs/CELEX_32022R2554_DE_TXT.pdf" ]; then
  python convert.py
fi
```

Das zieht DORA bei **jedem** Start nach, sobald der Korpus leer ist. Löschen wäre also nur so lange
dauerhaft, wie noch ein anderes Dokument im Volume liegt — abhängig von etwas, das mit DORA nichts
zu tun hat. Ersetzt durch einen einmaligen Bootstrap-Marker im `docs_md`-Volume:

```sh
if [ ! -f docs_md/.bootstrap ] && [ -f "docs/CELEX_32022R2554_DE_TXT.pdf" ]; then
  python convert.py
  touch docs_md/.bootstrap
fi
```

- Frisches Volume → DORA wird erzeugt.
- DORA gelöscht → bleibt gelöscht, auch bei leerem Korpus, auch über `docker compose restart`.
- `docker compose down -v` → Volume weg → DORA kommt wieder.
- Leerer Korpus ohne PDF ist kein Fehlerfall: der Index ist leer, jede Frage endet im Abstain.

Der Marker liegt im Volume (nicht im Image), weil er Zustand des Korpus beschreibt, nicht des Builds.
Festgehalten als ADR 0008.

## Komponenten

### `dokumente.py` — `saeubere_md_name(name)`

Zwilling von `saeubere_dateiname`: `Path(...).name` gegen Traversal, `_UNERLAUBT`-Ersetzung,
führende Punkte weg, muss auf `.md` enden, Rest darf nicht leer sein — sonst `UploadFehler(400)`.
Der Löschpfad säubert genauso wie der Uploadpfad; ein Request kann nicht aus `docs_md/` ausbrechen.

### `rag.py` — `loesche_dokument(md_name)`

Reihenfolge ist die eigentliche Anforderung:

1. Eintrag aus `fingerprint.json` entfernen und schreiben
2. `collection.delete(where={"file_name": md_name})` (vorhandenes `loesche_nodes`)
3. `.md` und `.source.json` löschen

Bricht der Prozess zwischen zwei Schritten ab, findet der nächste Start eine Datei ohne
Fingerprint-Eintrag. `diff()` schickt sie durch `indexiere()`, das selbst mit `loesche_nodes()`
beginnt. Schlimmster Fall: einmal zu viel indexiert. Nie verwaiste Nodes im Index, nie ein
Dokument in der Liste, dessen Nodes fehlen.

Die umgekehrte Reihenfolge (erst Nodes, zuletzt Fingerprint) hätte genau diesen stillen Defekt:
Datei und Hash blieben stehen, `diff()` sähe keinen Unterschied, das Dokument stünde ohne Nodes in
der Liste.

### `web/main.py` — `DELETE /documents/{datei}` → 204

- `409`, solange `NAMEN_IN_ARBEIT` nicht leer ist. Grund ist nicht Chroma, sondern das
  read-modify-write auf `fingerprint.json`: `_indexiere()` und Löschen würden sich gegenseitig
  überschreiben. Meldung: „Es läuft gerade eine Indexierung."
- `400` bei ungültigem Namen (aus `UploadFehler`).
- `404`, wenn die `.md` nicht existiert.
- sonst `rag.loesche_dokument(name)`, Antwort `204`.

`GET /documents` bleibt unverändert — es gibt keinen Sonderfall mehr, den die UI kennen müsste.

### UI (`web/static/index.html`)

Jede Zeile der Dokumentliste bekommt rechts einen „Entfernen"-Knopf. Klick → `confirm()` mit dem
Titel des Dokuments („… wirklich aus dem Index entfernen?"). Bei Bestätigung `DELETE`, danach
`ladeDokumente()`. Fehler landen in derselben Statuszeile wie beim Upload.

Der bestehende Hinweistext („Nur frei verwendbare Regulatorik hochladen …") bleibt; ergänzt um den
Satz, dass DORA nach dem Löschen nur bei frischem Volume zurückkommt.

## Tests

`tests/test_dokumente.py`:

- `saeubere_md_name("../../etc/passwd.md") == "passwd.md"`, absolute Pfade, Backslash-Pfade
- Sonderzeichen werden ersetzt
- Nicht-`.md` wird mit 400 abgelehnt
- `".md"` (leerer Stamm) wird abgelehnt

Die schweren Pfade (`rag`, `web`) bleiben ungetestet wie bisher — sie importieren
Embedding-Modelle und laufen in der CI nicht.

## Akzeptanz

Dokument hochladen → Frage wird beantwortet → Dokument löschen → dieselbe Frage endet im Abstain,
das Dokument verschwindet aus `GET /documents`, und `docker compose restart` bringt es nicht zurück.
