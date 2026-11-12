# 0016 — Rate-Limit und Body-Grenze für `/chat`

Status: akzeptiert (Issue [#44](../../../../issues/44), Folge aus #34)

## Kontext

`/chat` ist ohne Authentifizierung erreichbar. Das Längenlimit auf `Frage.frage` (2000 Zeichen, 422) greift
erst, nachdem FastAPI den vollständigen Body gelesen und geparst hat. Jede Verweigerung schreibt einen
Eskalationsdatensatz ([0010](0010-decision-gate-port-und-eskalationsdatensatz.md)); dessen Datei ist in Größe,
Archivzahl und Alter begrenzt. Eine Flut von Anfragen verdrängt daher ungeprüfte Datensätze vor Fristende,
obwohl die Antwort "zur Prüfung vorgemerkt" verspricht.

## Entscheidung

- Eine reine ASGI-Middleware (`web/schutz.py:ChatSchutz`) sitzt vor `/chat` und nur dort. `/upload` hat seine
  eigene, größere Grenze (`REGRAG_UPLOAD_MAX_MB`).
- **Reihenfolge:** erst Rate-Limit, dann Body-Grenze. Auch eine zu große Anfrage zählt also gegen das Limit.
- **Body-Grenze:** `MAX_CHAT_BYTES = 16 * MAX_FRAGE_ZEICHEN` (32 000 Bytes). Der Faktor deckt den ungünstigsten
  gültigen Body: 2000 Zeichen außerhalb der BMP, als `😀` escaped, sind 12 Bytes je Zeichen. Ein
  `Content-Length` über der Grenze wird mit 413 abgelehnt, ohne den Body zu lesen; ohne `Content-Length`
  (chunked) bricht das Lesen beim ersten Überschreiten ab. Der gepufferte Body wird der App unverändert
  weitergereicht.
- **Rate-Limit:** `REGRAG_CHAT_MAX_PRO_MINUTE` (Default 10, mindestens 1) Anfragen je Client-IP in einem festen
  Minutenfenster; darüber 429 mit `Retry-After` bis zum Fensterende. Der Zähler liegt im Prozess und wird bei
  jedem Fensterwechsel ganz geleert. Der Speicher ist damit auf die Clients einer Minute begrenzt.
- **Schlüssel ist `scope["client"]`.** Compose veröffentlicht uvicorn direkt auf Port 8000, ohne Reverse-Proxy
  und ohne Authentifizierung. `X-Forwarded-For` wertet nur uvicorn selbst aus, und nur von Adressen in
  `--forwarded-allow-ips` (Default `127.0.0.1`). Ein Client kann seinen Schlüssel also nicht per Header wählen.
- Die UI zeigt bei 413/429 das `detail` der Antwort als Fehler statt eines hängenden Cursors.

## Konsequenzen

- 10 pro Minute ist ein **ungemessener** Startwert: Eine Antwort dauert lokal 1 bis 3 Minuten, ein Mensch
  stellt kaum mehr als eine Frage pro Minute. Er ist konfigurierbar, nicht kalibriert.
- Das feste Fenster lässt an der Fenstergrenze bis zu doppelt so viele Anfragen in kurzer Folge zu. Für den
  Zweck (Flut begrenzen, nicht exakt dosieren) ist das hinnehmbar; ein Token-Bucket wäre mehr Code ohne
  erkennbaren Nutzen.
- Der Zähler gilt je Prozess. Mehrere uvicorn-Worker oder Repliken multiplizieren das Limit; Compose startet
  einen.
- Steht künftig ein Reverse-Proxy davor, sehen ohne `--forwarded-allow-ips` alle Clients wie eine IP aus und
  teilen sich das Limit. Dann gehört das Rate-Limit in den Proxy oder der Proxy in `--forwarded-allow-ips`.
- Clients hinter einem gemeinsamen NAT teilen sich ein Limit. Eine Flut aus vielen Adressen verhindert das
  Limit nicht; das braucht Authentifizierung (nicht im Scope von #44).
