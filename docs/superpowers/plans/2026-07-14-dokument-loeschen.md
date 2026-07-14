# Dokument-Löschen Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ein indexiertes Dokument über die Web-UI dauerhaft entfernen — Nodes aus Chroma, `.md` und `.source.json` aus `docs_md/`, Eintrag aus `fingerprint.json`.

**Architecture:** Der Löschpfad existiert bereits (`rag.loesche_nodes`, `dokumente.diff`); es fehlt die Bedienung. Neu: Namens-Säuberung für den Löschpfad (`dokumente.saeubere_md_name`), eine Löschfunktion mit crash-sicherer Reihenfolge (`rag.loesche_dokument`), ein `DELETE /documents/{datei}`-Endpunkt und ein Knopf je Zeile in der UI. Dazu ein einmaliger Bootstrap-Marker in `entrypoint.sh`, ohne den das Löschen von DORA beim nächsten Start rückgängig gemacht würde.

**Tech Stack:** Python 3, FastAPI, LlamaIndex, ChromaDB, pytest, Vanilla-JS-Frontend, Docker Compose.

## Global Constraints

- **Sprache im Code ist Deutsch** (ADR 0007): Funktions- und Variablennamen deutsch, HTTP-Pfade und Bibliotheks-APIs englisch. `datei`, `name`, `loesche_*`, nicht `file`, `delete_*`.
- **Bestehende Namen nicht umbenennen.** `loesche_nodes`, `_entferne_fragmente`, `NAMEN_IN_ARBEIT`, `JOBS` bleiben wie sie sind.
- **Kein Sonderschutz für DORA.** Es ist löschbar wie jedes andere Dokument (Spec: „RegRAG ist korpus-agnostisch").
- **Tests laufen ohne schwere Imports.** `tests/` importiert nur `dokumente` und `config` — niemals `rag` oder `web.main` (die laden Embedding-Modelle und bauen beim Import einen Index).
- **Testkommando:** `python -m pytest tests/ -v`
- **Der DORA-Dateiname ist `CELEX_32022R2554_DE_TXT.md`**, das PDF liegt read-only unter `docs/CELEX_32022R2554_DE_TXT.pdf`.

---

### Task 1: `saeubere_md_name` — Namens-Säuberung für den Löschpfad

Ein `DELETE /documents/{datei}` nimmt einen Dateinamen aus einem Request entgegen und macht daraus einen Pfad in `docs_md/`. Ohne Säuberung wäre `DELETE /documents/..%2F..%2Fetc%2Fpasswd` ein Traversal. Die Funktion ist der Zwilling von `saeubere_dateiname` (Zeile 19-26), nur endet sie auf `.md` statt `.pdf`.

**Files:**
- Modify: `dokumente.py` (nach `saeubere_dateiname`, also nach Zeile 26)
- Test: `tests/test_dokumente.py`

**Interfaces:**
- Consumes: `dokumente.UploadFehler(meldung, status)` und `dokumente._UNERLAUBT` — beide existieren bereits.
- Produces: `dokumente.saeubere_md_name(name: str) -> str`. Gibt den gesäuberten Basisnamen mit `.md`-Endung zurück oder wirft `UploadFehler` mit `status == 400`.

- [ ] **Step 1: Write the failing tests**

Ans Ende von `tests/test_dokumente.py` anhängen:

```python
def test_md_name_traversal_wird_auf_basename_reduziert():
    assert dokumente.saeubere_md_name("../../etc/passwd.md") == "passwd.md"
    assert dokumente.saeubere_md_name("/abs/pfad/marisk.md") == "marisk.md"
    assert dokumente.saeubere_md_name("C:\\temp\\eba.md") == "eba.md"


def test_md_name_sonderzeichen_werden_ersetzt():
    assert dokumente.saeubere_md_name("EBA ICT-Guidelines (2019).md") == "EBA_ICT-Guidelines__2019_.md"


def test_md_name_ohne_md_endung_wird_abgelehnt():
    with pytest.raises(dokumente.UploadFehler) as e:
        dokumente.saeubere_md_name("fingerprint.json")
    assert e.value.status == 400


def test_md_name_ohne_stamm_wird_abgelehnt():
    with pytest.raises(dokumente.UploadFehler):
        dokumente.saeubere_md_name(".md")
```

Warum diese vier: der erste Test ist die eigentliche Sicherheitsanforderung aus dem Issue (kein Traversal über den Löschpfad, in allen drei Pfad-Schreibweisen). Der zweite hält fest, dass ein hochgeladenes `EBA ICT-Guidelines (2019).pdf` unter genau dem Namen wiedergefunden wird, den der Upload vergeben hat — die Säuberung muss auf beiden Seiten dieselbe sein. Der dritte verhindert, dass jemand `fingerprint.json` oder `.bootstrap` über den Endpunkt löscht. Der vierte fängt den leeren Stamm ab.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_dokumente.py -k md_name -v`
Expected: 4 Fehler, jeweils `AttributeError: module 'dokumente' has no attribute 'saeubere_md_name'`

- [ ] **Step 3: Write minimal implementation**

In `dokumente.py` direkt unter `saeubere_dateiname` einfügen:

```python
def saeubere_md_name(name):
    roh = Path(str(name).replace("\\", "/")).name
    sauber = _UNERLAUBT.sub("_", roh).lstrip(".")
    if not sauber.lower().endswith(".md"):
        raise UploadFehler("Kein Dokument dieses Namens.", 400)
    if len(sauber) <= len(".md"):
        raise UploadFehler("Dateiname fehlt.", 400)
    return sauber
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/ -v`
Expected: alle Tests grün (die vier neuen plus die 11 bestehenden)

- [ ] **Step 5: Commit**

```bash
git add dokumente.py tests/test_dokumente.py
git commit -m "Dateinamen aus dem Löschpfad genauso säubern wie beim Upload"
```

---

### Task 2: `rag.loesche_dokument` — Fingerprint, Nodes, Dateien

Die Reihenfolge ist die eigentliche Anforderung. Erst der Fingerprint-Eintrag, dann die Nodes, zuletzt die Dateien. Bricht der Prozess dazwischen ab, findet `lade_oder_baue_index()` beim nächsten Start eine `.md` ohne Fingerprint-Eintrag, `diff()` steckt sie in `zu_indexieren`, und `indexiere()` beginnt selbst mit `loesche_nodes()`. Schlimmster Fall: das Dokument wird einmal zu viel indexiert. Die umgekehrte Reihenfolge (Fingerprint zuletzt) hinterließe ein Dokument, das in der Liste steht, dessen Nodes aber fehlen, und dessen unveränderter Hash `diff()` glauben macht, alles sei in Ordnung — ein stiller Defekt.

**Files:**
- Modify: `rag.py` (nach `loesche_nodes`, also nach Zeile 62)
- Test: keiner. `rag.py` baut beim Import einen Chroma-Index und lädt ein Embedding-Modell (Zeile 120: `index = lade_oder_baue_index()`); die CI kann es nicht importieren. Die testbare Logik dieser Aufgabe — die Namens-Säuberung — steckt in Task 1, die Wiederanlauf-Garantie in `diff()`, das bereits getestet ist (`test_diff_erkennt_geloeschte_datei`).

**Interfaces:**
- Consumes: `rag.loesche_nodes(dateiname)`, `rag._fingerprint_lesen()`, `rag._fingerprint_schreiben(fp)`, `dokumente.leerer_fingerprint(...)`, `config.DOKUMENTE_VERZEICHNIS`, `config.EMBEDDING_MODELL`, `rag.METRIK` — alle existieren bereits.
- Produces: `rag.loesche_dokument(md_name: str) -> None`. Erwartet einen bereits gesäuberten Dateinamen (`"marisk.md"`), keinen Pfad.

- [ ] **Step 1: Implementierung schreiben**

In `rag.py` direkt unter `loesche_nodes` (Zeile 60-61) einfügen:

```python
def loesche_dokument(md_name):
    """Fingerprint zuerst, dann Nodes, dann Dateien — ein Abbruch führt höchstens zu einmal zu viel indexieren."""
    fp = _fingerprint_lesen() or dokumente.leerer_fingerprint(config.EMBEDDING_MODELL, METRIK)
    fp["dokumente"].pop(md_name, None)
    _fingerprint_schreiben(fp)

    loesche_nodes(md_name)

    md_pfad = Path(config.DOKUMENTE_VERZEICHNIS) / md_name
    md_pfad.unlink(missing_ok=True)
    md_pfad.with_suffix(".source.json").unlink(missing_ok=True)
```

- [ ] **Step 2: Import prüfen (Syntax, Namen)**

Run: `python -c "import ast, sys; ast.parse(open('rag.py').read())" && python -m pytest tests/ -v`
Expected: kein Syntaxfehler, alle Tests weiterhin grün.

(`import rag` funktioniert lokal nur mit installierten Embedding-Modellen — deshalb hier nur die Syntaxprüfung. Der echte Durchlauf passiert in Task 6.)

- [ ] **Step 3: Commit**

```bash
git add rag.py
git commit -m "loesche_dokument: Fingerprint, Nodes und Dateien in wiederanlauffähiger Reihenfolge entfernen"
```

---

### Task 3: `DELETE /documents/{datei}`

**Files:**
- Modify: `web/main.py` (neuer Endpunkt hinter `dokument_liste`, also nach Zeile 150)

**Interfaces:**
- Consumes: `dokumente.saeubere_md_name(name)` (Task 1), `rag.loesche_dokument(md_name)` (Task 2), `NAMEN_IN_ARBEIT`, `DOKUMENTE`, `dokumente.UploadFehler` — alle vorhanden.
- Produces: HTTP `DELETE /documents/{datei}` → `204 No Content`. Fehler: `400` (ungültiger Name), `404` (kein solches Dokument), `409` (Indexierung läuft).

- [ ] **Step 1: Endpunkt schreiben**

In `web/main.py` unter `dokument_liste()` (nach Zeile 150) einfügen:

```python
@app.delete("/documents/{datei}", status_code=204)
def dokument_loeschen(datei: str):
    try:
        name = dokumente.saeubere_md_name(datei)
    except dokumente.UploadFehler as e:
        raise HTTPException(status_code=e.status, detail=str(e)) from e

    if NAMEN_IN_ARBEIT:
        raise HTTPException(status_code=409, detail="Es läuft gerade eine Indexierung.")
    if not (DOKUMENTE / name).exists():
        raise HTTPException(status_code=404, detail="Dokument nicht gefunden.")

    rag.loesche_dokument(name)
```

Zur `409`-Bedingung: geprüft wird `NAMEN_IN_ARBEIT` als Ganzes, nicht `name in NAMEN_IN_ARBEIT`. Der Grund ist nicht Chroma, sondern `fingerprint.json`: `_indexiere()` liest die Datei, ändert sie und schreibt sie zurück; ein gleichzeitiges Löschen eines *anderen* Dokuments würde in genau diesem Fenster überschrieben. Solange irgendein Job läuft, wird deshalb gar nicht gelöscht.

Zur Reihenfolge der Prüfungen: Name zuerst (ein Traversal-Versuch soll nicht erst am 409 hängenbleiben und dadurch verraten, ob gerade indexiert wird), dann 409, dann 404.

`import rag` steht bereits in Zeile 14, `import dokumente` in Zeile 13.

- [ ] **Step 2: Syntax prüfen**

Run: `python -c "import ast; ast.parse(open('web/main.py').read())"`
Expected: keine Ausgabe, Exit 0.

- [ ] **Step 3: Commit**

```bash
git add web/main.py
git commit -m "DELETE /documents/{datei}: Dokument aus Index, Korpus und Fingerprint entfernen"
```

---

### Task 4: Löschknopf in der UI

**Files:**
- Modify: `web/static/index.html` (CSS im `<style>`-Block; `ladeDokumente()` ab Zeile 376; Hinweistext Zeile 260-263)

**Interfaces:**
- Consumes: `DELETE /documents/{datei}` (Task 3), `GET /documents` (liefert `[{datei, titel}]`, unverändert), die vorhandenen Helfer `el(tag, cls, txt)`, `setzeStatus(text, klasse)`, `ladeDokumente()`.
- Produces: nichts für spätere Tasks.

- [ ] **Step 1: CSS für die Zeile und den Knopf**

Im `<style>`-Block die Regel `.korpus li` (Zeile 219) ersetzen und die neuen Regeln direkt darunter einfügen:

```css
  .korpus li {
    font-family: var(--serif);
    font-size: 15px;
    color: var(--ink);
    padding: 2px 0;
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 12px;
  }
  .korpus .entfernen {
    background: none;
    border: none;
    padding: 2px 4px;
    color: var(--ink-soft);
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    cursor: pointer;
    opacity: 0.6;
    transition: opacity .2s, color .2s;
  }
  .korpus .entfernen:hover { opacity: 1; color: var(--ochre); }
  .korpus .entfernen:disabled { opacity: 0.3; cursor: default; }
```

Der Knopf erbt die globale `button`-Regel (Zeile 183-195, blau gefüllt) — deshalb setzt `.entfernen` `background: none` und `border: none` explizit zurück.

- [ ] **Step 2: `ladeDokumente()` um den Knopf erweitern**

`ladeDokumente()` (Zeile 376-380) ersetzen durch:

```javascript
async function ladeDokumente() {
  const res = await fetch('/documents');
  const liste = await res.json();
  dokumenteListe.replaceChildren(...liste.map((d) => {
    const zeile = el('li');
    zeile.appendChild(el('span', null, d.titel));
    const knopf = el('button', 'entfernen', 'Entfernen');
    knopf.addEventListener('click', () => entferne(d, knopf));
    zeile.appendChild(knopf);
    return zeile;
  }));
}

async function entferne(dokument, knopf) {
  if (!confirm(dokument.titel + ' wirklich aus dem Index entfernen?')) return;
  knopf.disabled = true;
  setzeStatus('Wird entfernt …', 'laeuft');
  const res = await fetch('/documents/' + encodeURIComponent(dokument.datei), { method: 'DELETE' });
  if (!res.ok) {
    const fehler = await res.json().catch(() => ({}));
    knopf.disabled = false;
    return setzeStatus(fehler.detail || 'Entfernen fehlgeschlagen.', 'fehler');
  }
  setzeStatus(dokument.titel + ' wurde entfernt.', 'fertig');
  await ladeDokumente();
}
```

`encodeURIComponent` ist nötig, weil Titel und Dateiname auseinanderfallen können; gelöscht wird über `d.datei` (den `.md`-Namen), angezeigt wird `d.titel`. Der `catch(() => ({}))` fängt eine `204`-artige Antwort ohne JSON-Body ab.

- [ ] **Step 3: Hinweistext ergänzen**

Den `<div class="hinweis">`-Block (Zeile 260-263) ersetzen:

```html
    <div class="hinweis">
      Nur frei verwendbare Regulatorik hochladen (MaRisk, EBA-Guidelines). Keine urheberrechtlich
      geschützten Normtexte wie ISO oder DIN. Entfernte Dokumente bleiben entfernt — auch DORA;
      der Grundkorpus wird nur auf einem frischen Volume neu angelegt.
    </div>
```

- [ ] **Step 4: Manuell prüfen**

Run: `python -m pytest tests/ -v`
Expected: alle Tests grün (die UI hat keine Tests; der Lauf stellt nur sicher, dass nichts anderes kaputtging).

Der echte Durchlauf im Browser passiert in Task 6.

- [ ] **Step 5: Commit**

```bash
git add web/static/index.html
git commit -m "UI: Löschknopf je Dokument, mit Rückfrage"
```

---

### Task 5: Bootstrap-Marker in `entrypoint.sh` + ADR 0008

Ohne diese Änderung ist das Löschen nicht dauerhaft: `entrypoint.sh` zieht DORA bei **jedem** Start nach, sobald `docs_md/` keine `.md` enthält. Wer DORA als einziges Dokument löscht und den Container neu startet, hat es wieder — die Akzeptanzbedingung aus Issue #16 („`docker compose restart` bringt es nicht zurück") wäre verletzt.

**Files:**
- Modify: `entrypoint.sh` (Zeile 4-6 und Zeile 12-17)
- Create: `docs/adr/0008-korpus-agnostisch-dora-ist-nur-der-seed.md`
- Modify: `README.md` (Abschnitt zur Web-UI / zum Korpus)

**Interfaces:**
- Consumes: nichts aus früheren Tasks.
- Produces: die Datei `docs_md/.bootstrap` im `docs_md`-Volume. Sie liegt neben den `.md`-Dateien, wird aber von keinem Glob erfasst: `rag._md_dateien()` und `dokument_liste()` matchen auf `*.md`, `.bootstrap` hat keine Endung. Über `DELETE /documents/{datei}` ist sie nicht erreichbar, weil `saeubere_md_name` alles ohne `.md`-Endung mit 400 ablehnt (Task 1).

- [ ] **Step 1: `entrypoint.sh` umbauen**

Die Bootstrap-Bedingung (Zeile 4-6) ersetzen:

```sh
if [ ! -f docs_md/.bootstrap ] && [ -f "docs/CELEX_32022R2554_DE_TXT.pdf" ]; then
  python convert.py
  mkdir -p docs_md && touch docs_md/.bootstrap
fi
```

Und die Fehlerbedingung darunter (Zeile 12-17) ersetzen. Ein leerer Korpus ist ab jetzt kein Fehler mehr — er ist der legitime Zustand, nachdem jemand alles gelöscht hat, und der Agent antwortet dann konsequent Abstain (ADR 0002). Nur der Fall „frisches Volume **und** kein PDF" verdient noch einen Hinweis, und auch der bricht nicht mehr ab:

```sh
if ! ls docs_md/*.md >/dev/null 2>&1; then
  echo "Hinweis: Der Korpus ist leer — jede Frage endet im Abstain, bis ein Dokument hochgeladen wird."
  echo "Für den DORA-Grundkorpus das PDF von https://eur-lex.europa.eu/legal-content/DE/TXT/PDF/?uri=CELEX:32022R2554"
  echo "nach docs/CELEX_32022R2554_DE_TXT.pdf legen und das docs_md-Volume neu anlegen (docker compose down -v)."
fi

exec uvicorn web.main:app --host 0.0.0.0 --port 8000
```

`mkdir -p docs_md` steht vor dem `touch`, weil `convert.py` das Verzeichnis zwar anlegt, aber nur wenn es läuft — bei fehlendem PDF greift der `if`-Zweig gar nicht erst, dann bleibt auch der Marker aus, und ein später nachgereichtes PDF wird beim nächsten Start noch konvertiert.

- [ ] **Step 2: Prüfen, dass das Skript syntaktisch gültig ist**

Run: `sh -n entrypoint.sh && echo OK`
Expected: `OK`

- [ ] **Step 3: ADR 0008 schreiben**

`docs/adr/0008-korpus-agnostisch-dora-ist-nur-der-seed.md` anlegen. Format an ADR 0006 angleichen (vorher `cat docs/adr/0006-inkrementeller-index-merge-statt-voll-rebuild.md` lesen und Struktur, Überschriften und Ton übernehmen). Inhaltlich:

- **Kontext:** RegRAG wurde für DORA gebaut, aber nichts im System ist an DORA gebunden — Retriever, Guard, Abstain-Kante und Fingerprint-Diff arbeiten über beliebige Markdown-Dokumente. Mit dem Upload (#4) und dem Löschen (#16) ist der Korpus vollständig austauschbar. Das „Reg" im Namen meint die Bindung an die Belege, nicht die Domäne: das System antwortet nur, was in den indexierten Dokumenten steht.
- **Entscheidung:** DORA ist der Seed des Korpus, kein geschütztes Dokument. Es ist löschbar wie jedes andere. `entrypoint.sh` legt es genau einmal an, gesteuert über `docs_md/.bootstrap` im Volume.
- **Konsequenzen:** Löschen ist dauerhaft (`docker compose restart` bringt nichts zurück). Ein frisches Volume (`docker compose down -v`) stellt DORA wieder her. Ein leerer Korpus ist ein gültiger Zustand — jede Frage endet im Abstain. Wer das BGB hochlädt, bekommt ein BGB-RAG mit denselben Garantien.

- [ ] **Step 4: README ergänzen**

Im Abschnitt zur Web-UI / zum Upload einen Absatz anhängen (exakte Stelle beim Schreiben aus `README.md` bestimmen, dort wo der Upload beschrieben ist):

```markdown
Dokumente lassen sich über die Liste unter dem Chat auch wieder entfernen — inklusive DORA.
Der Korpus ist austauschbar; DORA ist nur der Seed, der beim ersten Start eines frischen
`docs_md`-Volumes angelegt wird (siehe [ADR 0008](docs/adr/0008-korpus-agnostisch-dora-ist-nur-der-seed.md)).
Entfernte Dokumente bleiben entfernt, auch über `docker compose restart`. `docker compose down -v`
verwirft das Volume und legt den DORA-Grundkorpus neu an.
```

- [ ] **Step 5: Commit**

```bash
git add entrypoint.sh docs/adr/0008-korpus-agnostisch-dora-ist-nur-der-seed.md README.md
git commit -m "DORA nur einmalig seeden, Korpus-Agnostik als ADR 0008 festhalten"
```

---

### Task 6: Akzeptanz im laufenden Container

Die Tests decken die Namens-Säuberung und den Fingerprint-Diff ab, nicht aber den Zusammenlauf von Chroma, Dateisystem und UI. Diese Aufgabe fährt die Akzeptanzbedingung aus Issue #16 einmal von Hand durch.

**Files:** keine (nur Ausführung; Fixes gehen in die jeweilige Datei aus Task 1-5 und werden dort nachcommittet).

**Interfaces:**
- Consumes: alles aus Task 1-5.
- Produces: nichts.

- [ ] **Step 1: Container frisch starten**

```bash
docker compose down -v
docker compose up --build -d
docker compose logs -f
```

Warten, bis DORA konvertiert und indexiert ist (die Logs zeigen den Fortschritt; das dauert einige Minuten).

- [ ] **Step 2: Ein zweites Dokument hochladen**

`http://localhost:8000` öffnen, über „PDF hinzufügen" eine frei verwendbare Regulatorik-PDF hochladen (z. B. eine EBA-Guideline). Warten, bis der Status „ist durchsuchbar" meldet.

Eine Frage stellen, die nur dieses Dokument beantworten kann. Erwartet: eine Antwort mit dem neuen Dokument in den Fundstellen.

- [ ] **Step 3: Löschen**

In der Dokumentliste auf „Entfernen" beim neuen Dokument klicken, im `confirm()` bestätigen.

Erwartet: Statuszeile meldet „… wurde entfernt.", das Dokument verschwindet aus der Liste.

- [ ] **Step 4: Prüfen, dass es wirklich weg ist**

Dieselbe Frage aus Step 2 erneut stellen.
Erwartet: Abstain („Kein Beleg").

```bash
curl -s localhost:8000/documents
```
Erwartet: das gelöschte Dokument ist nicht mehr in der Liste.

```bash
docker compose exec regrag ls docs_md/
```
Erwartet: weder die `.md` noch die `.source.json` des gelöschten Dokuments; `.bootstrap` ist da.

```bash
docker compose exec regrag cat chroma/fingerprint.json
```
Erwartet: kein Eintrag für das gelöschte Dokument.

- [ ] **Step 5: Neustart bringt es nicht zurück**

```bash
docker compose restart
docker compose logs -f
curl -s localhost:8000/documents
```
Erwartet: das gelöschte Dokument bleibt weg. Es wird nicht neu indexiert.

- [ ] **Step 6: DORA löschen und neu starten**

In der UI DORA entfernen, bestätigen. Dann:

```bash
docker compose restart
curl -s localhost:8000/documents
```
Erwartet: leere Liste (`[]`). DORA kommt **nicht** zurück, obwohl der Korpus leer ist und das PDF gemountet bleibt. Eine DORA-Frage endet im Abstain.

```bash
docker compose down -v && docker compose up -d
```
Erwartet: DORA ist wieder da (frisches Volume, `.bootstrap` fehlt).

- [ ] **Step 7: Traversal-Versuch über den Endpunkt**

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X DELETE 'localhost:8000/documents/..%2F..%2Fetc%2Fpasswd'
curl -s -o /dev/null -w '%{http_code}\n' -X DELETE 'localhost:8000/documents/fingerprint.json'
```
Erwartet: `404` bzw. `400`. Keine Datei außerhalb von `docs_md/` wird angefasst.

- [ ] **Step 8: Ergebnis festhalten**

Läuft alles durch: nichts zu committen, weiter zum PR. Fällt etwas durch: Fix in der betroffenen Datei, Test ergänzen falls die Lücke testbar ist, committen, Step 1-7 erneut.
