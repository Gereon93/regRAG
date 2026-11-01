# Spec: Gate: Eskalationsdatei begrenzen (Rotation, Aufbewahrung, Längenlimit /chat)

**Issue:** #34 (https://github.com/Gereon93/regRAG/issues/34)
**Status:** approved
**Datum:** 2026-10-31 (Grill: 2026-11-01)
**Branch:** Gereon93/gate-eskalationsdatei-begrenzen-rotation-aufbewa

---

## 1. Problem & Fachlicher Intent
- `chroma/eskalationen.jsonl` wächst mit jeder Verweigerung unbegrenzt; `/chat` ist unauthentifiziert, `Frage.frage` ohne Längenlimit; Fragen können personenbezogen sein, ein Löschkonzept fehlt.
- Ziel: Platzbedarf hart begrenzt, kein Datensatz älter als die Frist, Löschweg dokumentiert, zu lange Fragen → 422.
- Non-Goals (Issue erlaubt "und/oder"):
  - Rate-Limit auf `/chat` und Request-Body-Größenlimit → **Folge-Issue wird in diesem PR angelegt**, Nummer steht im ADR 0010. Das Längenlimit greift erst nach dem Parsen des vollständigen Bodys; ein riesiger Body wird trotzdem gelesen. Das gehört ins Folge-Issue.
  - Auth, Eskalations-Queue, Löschen einzelner Datensätze per API.

## 2. Funktionsweise
1. `Frage.frage`: `min_length=1`, `max_length=MAX_FRAGE_ZEICHEN` (2000 Zeichen, Unicode-Codepoints, nicht Bytes) → 422, kein stilles Abschneiden. `min_length=1` deckt sich mit der UI (sendet nie leer); reine Leerzeichen bleiben erlaubt (wie bisher).
   - UI: `<input id="frage">` bekommt `maxlength="2000"`, damit die UI keinen 422 erzeugt (der Stream-Reader in `fragen()` prüft `res.ok` nicht und bliebe sonst ohne Meldung hängen).
2. `EskalationsGrenzen(max_bytes, max_archive, aufbewahrung_tage)`, Defaults 5 MiB / 5 / 90, per `REGRAG_ESKALATION_MAX_BYTES|_MAX_ARCHIVE|_AUFBEWAHRUNG_TAGE` überschreibbar.
   - Wird **einmal beim Import** von `agent.py` gebaut (Fail-fast bei ungültigen Werten), nicht pro Anfrage. Sonst wirft jede `/chat`-Anfrage `ValueError` → 500.
3. Ist `ziel` vorhanden, aber **keine reguläre Datei** (`os.devnull`, FIFO, Gerät): keine Rotation, kein Archiv-Scan, keine Löschung; nur Append. Grund: `evaluation/run.py` schreibt nach `/dev/null`; dessen mtime kann älter als die Frist sein → `os.replace("/dev/null", …)`, im Container als root ausführbar.
4. Vor jedem Append, unter einem **modulweiten `threading.Lock`**, der Rotation **und** Append umschließt:
   - Rotation der aktiven Datei, wenn Größe ≥ `max_bytes` **oder** ältester Datensatz älter als **Frist/2**. Ziel: `<name>.<UTC %Y%m%dT%H%M%S%fZ>.<pid>` per `os.replace`. Existiert der Name schon, wird nicht überschrieben; die Rotation entfällt bis zum nächsten Append.
   - Archive mit ältestem Datensatz älter als Frist löschen (log INFO: Anzahl, keine Inhalte).
   - Archive über `max_archive` (älteste nach Name zuerst) löschen (log **WARNING**: Anzahl. Datensätze gehen vor Fristende verloren, das ist ein Flut-Signal).
5. Bereinigung (Frist + Anzahl, inklusive Rotation der aktiven Datei bei Frist-Überschreitung) läuft zusätzlich **einmal beim Start** von `web/main.py` (Lifespan oder Import), damit eine Frist auch ohne neue Verweigerung greift.
6. Archiv-Erkennung: nur Namen, die `re.fullmatch(re.escape(name) + r"\.\d{8}T\d{12}Z\.\d+", …)` erfüllen. Fremde Dateien (`.bak`, `.lock`, Editor-Kopien) werden weder gezählt noch gelöscht.
7. Ältester Zeitpunkt einer Datei: erste Zeile mit gültigem `zeitpunkt` (Zeilen der Reihe nach lesen). Ein **naiver** Zeitstempel gilt als UTC (`replace(tzinfo=utc)`). Gibt es keine gültige Zeile, gilt die mtime. Ist die Datei inzwischen verschwunden (`FileNotFoundError`), wird sie übersprungen.

## 3. Invarianten
- Größe je Datei ≤ `max_bytes` − 1 + eine Zeile (eine Zeile ≤ ca. 50 KB: Frage 2000 + top-k × 2000 Zeichen, JSON-Escaping). Gesamt ≤ (`max_archive` + 1) × (`max_bytes` + eine Zeile). Das gilt auch, wenn `max_bytes` kleiner als eine Zeile ist: dann eine Zeile pro Datei.
- Aufbewahrung: Ein Datensatz wird gelöscht, sobald der älteste Datensatz seiner Datei die Frist überschreitet und danach geschrieben oder gestartet wird. Durch die Rotation bei Frist/2 lebt jeder Datensatz **mindestens Frist/2** (Ausnahme: Verdrängung durch `max_archive`, siehe WARNING) und **höchstens Frist bis zum nächsten Append oder Neustart**. Diese Lücke ohne Verkehr ist im ADR benannt.
- Dateirechte bleiben `0600` (Rename erhält sie, neue Datei per `os.open(..., 0o600)`).
- Rotations- oder Bereinigungsfehler (`OSError`) wird geloggt und kippt weder Append noch ABSTAIN. Ein Zeitstempel-/Parsingproblem darf **nie** den Append verhindern (vorher: naive erste Zeile → `TypeError` → jede weitere Vormerkung schlug dauerhaft fehl, nachgewiesen).
- Innerhalb eines Prozesses (uvicorn: ein Worker, Threadpool via `asyncio.to_thread`) gibt es keine Doppelrotation und keine Namenskollision (Lock).

## 4. Randfälle
- Mehrere Prozesse (z. B. CLI `agent.py` parallel zum Server): Race wird akzeptiert, kein `flock`. `os.replace` verliert keine Daten. Ein paralleler Schreiber, der seinen fd vor dem Rename geöffnet hat, schreibt ins Archiv-Inode. Die PID im Namen verhindert eine Kollision. Folgen: früh rotierte kleine Datei, belegt einen Archivplatz, `FileNotFoundError` als geloggter Rotationsfehler. Im ADR dokumentiert.
- `max_archive=0` → Rotation löscht effektiv (WARNING). Werte < 1 für Bytes/Tage, < 0 für Archive → `ValueError` beim Start. Nicht-Ganzzahl in Env → `ValueError` beim Import von `config`.
- Naiver oder kaputter `zeitpunkt`, leere erste Zeile, abgeschnittene Zeile → nächste gültige Zeile, sonst mtime; Append gelingt.
- Fremddateien `<name>.bak` o. ä. neben dem Datensatz bleiben unangetastet.
- Uhr springt zurück (NTP): Archivreihenfolge nach Name kann kurz falsch sein. Akzeptiert.
- `ziel` existiert nicht, Archive schon → Bereinigung läuft trotzdem.

## 5. Akzeptanz & Tests
- [ ] `tests/test_gate.py` (vorhanden, grün): Rotation bei Größe, Archivzahl, Frist (Archiv + aktive Datei), Rechte 0600, Rotationsfehler, Validierung.
- [ ] `tests/test_gate.py` (neu):
  - naive `zeitpunkt`-Zeile → Append gelingt weiter (`True`), Datei wird nach Frist korrekt behandelt
  - `<name>.bak` wird weder gelöscht noch auf `max_archive` angerechnet
  - `os.devnull` mit Mini-Grenzen → `os.replace` wird nie aufgerufen, Rückgabe `True`
  - parallele Threads (z. B. 8 × 50 Appends, `max_bytes` klein, `max_archive` groß) → Summe der Zeilen = Zahl der Appends, keine Archiv-Kollision
  - Verdrängung über `max_archive` loggt WARNING
  - aktive Datei mit ältestem Satz zwischen Frist/2 und Frist → rotiert, Archiv bleibt erhalten
  - Bereinigung ohne Append (Start-Funktion) löscht überfälliges Archiv
- [ ] `tests/test_web_gate.py`: 422 bei 2001 Zeichen und bei `""`; 2000 Zeichen ok, auch 2000 × `"ä"` (Zeichen, nicht Bytes).
- [ ] `tests/test_config.py`: drei Env-Variablen, Defaults, Override.
- [ ] ADR 0010: Abschnitt Aufbewahrung/Löschweg (automatisch: Rotation, Frist, Start-Bereinigung; manuell: Server stoppen, `rm chroma/eskalationen.jsonl chroma/eskalationen.jsonl.*`, Einzellöschung per `jq`-Filter bei gestopptem Server). Satz "fehlen noch (Issue #34)" ersetzen. Rate-Limit- und Body-Limit-Folge-Issue verlinken, Mehrprozess-Race und Flut-Verdrängung benennen. Frist 90 Tage als **technischer Startwert, keine Rechtsfestlegung** kennzeichnen.
- [ ] README/env.example: Variablen + Verweis ADR.
- [ ] UI `maxlength="2000"`. Screenshot am PR laut globaler Regel nur, wenn sichtbar. Das Attribut ist unsichtbar, also nicht nötig.
- [ ] `ruff check .` (ruff==0.16.3) und `pytest` lokal grün (aktuell rot: F821, siehe Grill).

## 6. Adversarial Grill-Protokoll

**Skill:** /grill · **Runde:** 1 · **Prüfer:** Claude Opus 5.5 (claude-opus-5-5) · **Datum:** 2026-11-01

### Gefundene Lücken (im Entwurf, belegt)
1. **Blocker, Laufzeit:** `agent.py` nutzt `EskalationsGrenzen` ohne Import → `NameError` in jeder `pruefe()` → `/chat` 500 (ruff F821, reproduziert).
2. **Blocker, Datenschutz/Robustheit:** naiver `zeitpunkt` in erster Zeile → `TypeError` beim Vergleich mit tz-aware `frist`, nicht vom inneren `except OSError` gefangen → Append übersprungen; jede weitere Vormerkung `False`, dauerhaft (reproduziert).
3. **Blocker, Datenverlust:** Glob `<name>.*` zählt Fremddateien. Mit `max_archive=1` blieb `e.jsonl.bak` stehen, das echte Archiv wurde gelöscht (reproduziert). Umgekehrt werden Fremddateien nach Frist gelöscht.
4. **Hoch:** `os.devnull`: `exists()` true, Größe 0, aber mtime-Fallback kann älter als Frist sein → `os.replace("/dev/null", …)`, im Container als root (kein `USER` im Dockerfile) möglich. `is_file()`-Schutz Pflicht.
5. **Hoch:** Threadpool-Race in einem Prozess: Doppelrotation (kleine Archive verdrängen volle), `FileNotFoundError`-Lärm, Namenskollision bei gleicher Mikrosekunde → `os.replace` überschreibt Archiv still.
6. **Hoch:** Ungültige Grenzen werden pro Anfrage gebaut → `ValueError` → 500 bei jeder Anfrage statt Fail-fast beim Start.
7. **Mittel:** Frist greift nur beim nächsten Append; ohne Verweigerung bleiben überfällige Daten unbegrenzt liegen. Invariante "kein Datensatz überlebt die Frist" war falsch.
8. **Mittel:** Löschung nach ältestem Satz bei Rotation erst nach Frist → die neuesten Sätze der Datei werden sofort mitgelöscht (unter Umständen Stunden alt, ungeprüft).
9. **Mittel:** Invariante "+ eine Zeile" galt nur gesamt, nicht je Datei; `max_bytes` < Zeile nicht betrachtet.
10. **Mittel:** UI prüft `res.ok` nicht → 422 ließe den Chat ohne Meldung hängen.
11. **Niedrig:** Längenlimit begrenzt nicht die Body-Größe (Body wird voll geparst).

### Entscheidungen
- (a) Rate-Limit: **nicht in diesem PR**. Das Issue erlaubt "und/oder", das Längenlimit erfüllt den Scope, die Datei ist jetzt hart begrenzt. Folge-Issue (Rate-Limit + Body-Limit) wird angelegt und im ADR verlinkt.
- (b) 90 Tage Default: **ja, als technischer Startwert** gekennzeichnet. Die rechtlich verbindliche Frist ist Betreiberentscheidung, eskaliert an Gereon.
- (c) Granularität: **nach ältestem Datensatz** (datenschutzsicher, Invariante hält) **plus Rotation bei Frist/2** → Mindestlebensdauer Frist/2, keine Sofortlöschung frischer Sätze. Nach neuester Änderungszeit verworfen: verletzt die Frist.
- (d) Verdrängung über `max_archive`: **WARNING loggen** (Anzahl, keine Inhalte). Das ist Verlust vor Fristende und ein Flut-Signal. Fristlöschung: INFO.
- (e) `min_length=1`: **behalten**. Deckungsgleich mit der UI (sendet nie leer), reversibel, im PR-Body als kleine Scope-Erweiterung nennen.
- (f) Mehrprozess: **Race akzeptieren, kein `flock`** (Lockdatei kollidiert mit Archiv-Erkennung, nicht portabel). **Aber:** In-Prozess-`threading.Lock` Pflicht, dazu PID im Archivnamen und kein Überschreiben.
- (g) Sonderdateien: **Pflicht**. Ist `ziel` vorhanden und keine reguläre Datei → weder rotieren noch bereinigen.
- Race Rotation ↔ `os.open(O_APPEND)`: verlustfrei (fd vor Rename schreibt ins Archiv-Inode, danach entsteht eine neue Datei). Durch den Lock im Prozess entfällt der Fall ganz.
- Glob-Fremdtreffer: Regex-Fullmatch auf das eigene Namensschema.
- tz-naiv: als UTC interpretieren. Kein Parsing-Fehler darf den Append blockieren.

### Eingearbeitete Korrekturen
§1 Non-Goals präzisiert (Folge-Issue Pflicht, Body-Limit). §2 neu: Fail-fast-Grenzen, Sonderdatei-Schutz, Lock, Frist/2-Rotation, PID-Name ohne Überschreiben, Logstufen, Start-Bereinigung, Regex-Archiv-Erkennung, robustes Zeitstempel-Parsing, UI-`maxlength`. §3 Invarianten korrigiert (Größe je Datei, Aufbewahrungsfenster Frist/2 bis Frist + Lücke ohne Verkehr, Parsing blockiert nie). §4 Randfälle ergänzt. §5 Testliste um 7 Gate-Tests, Unicode-Grenzfall und ADR-Inhalte erweitert.

### Eskalation an den User
- Rechtlich verbindliche Aufbewahrungsfrist bzw. Rechtsgrundlage für die Speicherung personenbezogener Fragen (DSGVO Art. 5 Abs. 1 lit. e). 90 Tage sind technisch gewählt und konfigurierbar; das blockiert die Umsetzung nicht.

### Verdikt
**approved** (2026-11-01, Claude Opus 5.5). Kein offener Spec-Mangel. Der Code-Entwurf ist noch **nicht** abnahmefähig: Lücken 1–10 müssen laut §2–§5 umgesetzt und getestet werden.
