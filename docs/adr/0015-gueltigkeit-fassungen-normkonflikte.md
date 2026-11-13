# 0015 — Gültigkeitsmetadaten, mehrdeutige Fassungen und Normkonflikte

Status: akzeptiert; Gültigkeit und Fassungsprüfung im Betrieb, **Konflikt-Judge experimentell, Nutzen unbelegt**
(Issue [#30](../../../../issues/30), Teil von #26)

## Kontext

Quellen trugen nur `titel`, `pdf` und `segment`. Das Gate konnte weder eine abgelöste Fassung erkennen noch zwei
gleichzeitig gültige Fassungen oder widersprechende Normen bemerken. `segment` ist bereits die Normstufe
([0013](0013-laya-kalibrierung-segmentschwellen-manifest.md)), ein neues Feld gibt es dafür nicht. Der Sidecar geht
schon in den Fingerprint ein ([0006](0006-inkrementeller-index-merge-statt-voll-rebuild.md)).

## Entscheidung

### Schema und Fehler

- Optional im Sidecar: `gueltig_ab`, `gueltig_bis` (`YYYY-MM-DD`, beide inklusive), `fassung` (1 bis 100 Zeichen).
- `gueltig_ab` ist der erste Tag, an dem die Fassung **anzuwenden** ist, nicht das Inkrafttreten (DORA: in Kraft seit
  16.01.2023, anzuwenden ab 17.01.2025). `gueltig_bis` ist der letzte Anwendungstag.
- `gueltigkeit.normalisiere` ist die eine Validierung für Sidecar und Upload. `date.fromisoformat` allein genügt nicht,
  es nimmt `20260101` und `2026-W01-1` an.
- **Fail closed pro Dokument, nicht pro Dienst:** Ein kaputter Sidecar (Feld, JSON, kein Objekt) wirft
  `MetadatenFehler`. Der Start loggt ERROR und macht weiter. Die Sidecar-Änderung ändert den Hash, die alten Nodes sind
  also schon entfernt; das Dokument fehlt im Fingerprint, jeder Start versucht es erneut und scheitert vor jedem
  Embedding. Ein Tippfehler nimmt das Dokument aus der Suche, `GET /documents` zeigt es weiter; das Signal ist das Log.
- Fehlende Felder werden als Node-Metadatum weggelassen, nie `None`. `GATE_METADATEN` fließen weder in den
  Embedding-Text noch ins LLM-Prompt, sonst ändern sich Scores und Chunk-Grenzen.
- `/upload` erfasst die drei Felder, nie `segment`: `gueltig` und `unbekannt` werden gleich behandelt, ein Uploader
  gewinnt nichts. Multipart wird vor dem Handler geparst, die Prüfung spart also keine Bytes, liefert aber die
  Feldmeldung.

### Stichtag und Nutzbarkeit

- Stichtag ist heute in Europe/Berlin, je Entscheidung genau einmal bestimmt, nie prozessweit fixiert. `BERLIN` wird beim
  Import gebaut, damit fehlende Zeitzonendaten den Start brechen, nicht die Anfrage; `tzdata` steht deshalb in
  `requirements.txt`.
- **Stichtag pro Anfrage** ([#50](../../../../issues/50)): Der Port ist `entscheide(frage, nodes, stichtag=None)`, `None`
  heißt heute. Jede Gate-Implementierung nimmt das Argument an, nur `GueltigkeitsGate` nutzt es, `Kaskade` reicht es
  positional an jeden Gate weiter (wer es nicht braucht, nennt es `_stichtag`). `entscheide_und_eskaliere` und `pruefe_antwort_und_eskaliere` geben es an Gate und Datensatz;
  `stichtag` im Eskalationsdatensatz ist dann der angefragte Tag, der Gültigkeitsstatus der Evidenz gilt für ihn.
  Eingang: `POST /chat` Feld `stichtag`, CLI `python agent.py "Frage" --stichtag YYYY-MM-DD`, Datumsfeld in der UI.
  Validiert wird mit `gueltigkeit.parse_datum` wie die Sidecar-Felder, Unsinn ist 422 bzw. Exitcode 2. Künftige Tage
  sind zulässig ("was gilt ab 2027"). Ein optionales Argument statt eines Kontextobjekts, weil nur ein Wert durchgereicht
  wird und der Default das bisherige Verhalten exakt erhält.
- **Kein Stichtag aus dem Fragetext:** Welches Datum einer Frage der Stichtag ist, ist oft nicht entscheidbar ("seit",
  "bis", zwei Daten, nur ein Jahr), und ein stiller Fehlgriff wählt die falsche Fassung. Der Stichtag kommt nur explizit;
  Erkennung mit sichtbarer Rückmeldung ist [#63](../../../../issues/63).
- `gueltig` ist nutzbar. `unbekannt` ist nutzbar, **gilt aber nicht als gültige Fassung.** Liegt ein nicht nutzbarer
  Treffer vor und kein `gueltig`-Treffer, ist die Entscheidung `OUTDATED_SOURCE`, auch neben `unbekannt`-Treffern. Das
  kostet Antworten und ist gewollt (Falschantwort ≫ Eskalation). Liegen nur nutzbare Treffer vor, läuft alles wie vor #30.
- Die Regel gilt über alle Treffer, nicht pro Quelle: Ein abgelaufener MaRisk-Chunk unter den Treffern einer DORA-Frage
  verweigert auch dann, wenn die DORA-Treffer `unbekannt` sind. Pro Quelle würde eine Frage zur alten MaRisk aus einem
  themenfremden Treffer beantwortet. Datiert der Betreiber nur Altfassungen, steigt so die Fehl-Eskalation; ob die Regel
  pro Quelle besser trägt, entscheidet eine Messung ([#58](../../../../issues/58)).

### Einengender Gate statt Dekorator

`GueltigkeitsGate` ist ein gewöhnlicher Gate. Er liefert `GateDecision.evidenz`, `Kaskade` reicht die Einengung an die
Folgegates weiter, `genutzte_nodes` ist die einzige Stelle, die die Regel kennt. Kontext, Quellenliste, Segment und
Metriken nutzen sie. Der Eskalationsdatensatz führt weiter **alle** Treffer samt Status: Der Prüfer muss die abgelöste
Fundstelle sehen. Bei `OUTDATED_SOURCE` zeigt die Quellenliste die abgelehnten Treffer.

### Reihenfolge

Score, Gültigkeit, Score, Fassung, Laya `answerable`, Konflikt-Judge.

- Score zuerst: Eine themenfremde Frage an einem abgelösten Dokument meldet `LOW_RELEVANCE`, sonst verfälscht sie das
  Signal "veralteter Korpus".
- Score erneut nach dem Filter: Sonst winkte der hohe Score einer veralteten Fassung eine schwache gültige durch.
  `relevance` bleibt das Maximum aller Treffer, `LOW_RELEVANCE` kann also neben einem Wert über der Schwelle stehen.
- Kostenlose Prüfungen vor Laya, `answerable` vor den Paaraufrufen.

### Mehrdeutige Fassung: `AMBIGUOUS_VERSION`

Zwei gleichzeitig nutzbare Fassungen derselben Quelle sind ein **Datenfehler**, kein Normkonflikt: Es ist nicht bekannt,
dass sie sich widersprechen, und die Reaktion ist eine andere (Sidecar pflegen). `CONFLICTING_PROVISIONS` hätte Metrik und
Labels verfälscht. `FassungsGate` gruppiert nach normiertem Titel und vergleicht `(gueltig_ab, gueltig_bis, fassung)`.
Das fängt auch den wahrscheinlichsten Betreiberfehler: zwei datierte Fassungen ohne `gueltig_bis`.

**Grenze Titel-Identität:** Titel sind Freitext. Zwei Fassungen werden nur erkannt, wenn der Betreiber `titel` angleicht;
falsch negativ ist die Grenze. Eine PDF mit gleichem Titel und abweichender `fassung` kann per Upload `AMBIGUOUS_VERSION`
für diese Norm erzwingen. Das verweigert nur und ist nicht schlimmer als die offene Upload-Fläche.

### Normkonflikt: `LayaKonfliktGate`

- **Ohne ihn gibt es keine Normkonflikterkennung.** Verschiedene Segmente im Top-k sind der Normalfall, nicht der
  Widerspruch; eine deterministische Regel eskalierte die meisten Antworten. Ohne Laya erkennt eine Standardinstallation
  daher keine widersprechenden Normen. Der Integrationstest belegt mit Fake-Judge die Mechanik, nicht den Nutzen.
- **Default aus:** `REGRAG_LAYA_KONFLIKT_SCHWELLE` hat keinen Default, weil es keinen gemessenen Wert gibt. Laya liegt
  zero-shot nahe Zufall ([0011](0011-laya-als-lokaler-answerability-judge.md)); ein Wert wie 0,5 flaggte etwa jedes
  zweite Paar. Ohne `REGRAG_LAYA_URL` ist die Variable ein Startfehler.
- Paare nur aus verschiedenen `file_name`, sequentiell, Abbruch beim ersten Konflikt. Jeder Ausfall ist
  `JUDGE_UNAVAILABLE`, nie ANSWER. Der Konflikt wird nicht aufgelöst.
- **Keine Manifest-Bindung:** Kalibrierte Konfliktschwellen setzen Labels voraus, ein Schema-Bump ohne sie wäre Fassade.
  `baue_gate` warnt beim Einschalten.
- **Annahme, nicht verifiziert:** `laya-serve` nimmt die Frage `conflict` und den State `{"frage", "a", "b"}` an. Der
  Contract-Test läuft gegen `httpx.MockTransport`, `tests/test_laya_live.py` prüft das gegen einen echten Server.

## Verworfen

- **Dekorator um eine innere Kaskade plus `Pruefung.evidenz`:** zwei Schachtelungsebenen, zwei Kopien der Regel.
- **Filter in `entscheide_und_eskaliere`:** `guard_report` sähe ihn nicht, `LOW_RELEVANCE` vor `OUTDATED_SOURCE` wäre
  nicht machbar.
- **Metadaten-Update ohne Re-Embedding:** `collection.update` ändert nur das Flachfeld, der Retriever liest weiter den
  alten Wert aus `_node_content`. Eine Sidecar-Änderung bettet das Dokument neu ein (Minuten, Start blockiert).

## Konsequenzen

- **Frühere Rechtslage:** Nur mit explizitem Stichtag (#50). Ohne ihn wird eine Frage nach einem früheren Stand aus der
  heute gültigen Fassung beantwortet, auch wenn sie ein Datum nennt ([#63](../../../../issues/63)).
- Ohne gepflegte `gueltig_ab`/`gueltig_bis` bleibt alles `unbekannt`, der Filter wirkt nie. `unbekannt` ist kein Beleg für
  Aktualität.
- **Verdrängung:** Abgelöste und gültige Fassung sind fast textgleich. Bei `k=3` können alle Treffer aus der abgelösten
  kommen, dann `OUTDATED_SOURCE`, obwohl die gültige im Index liegt ([#53](../../../../issues/53)). Bis dahin hilft es, die
  abgelöste Fassung zu löschen.
- **Kosten:** Der Konflikt-Judge macht bis zu 3 zusätzliche Laya-Aufrufe je Anfrage auf dem ANSWER-Pfad, je bis
  `LAYA_TIMEOUT`, quadratisch in `similarity_top_k`. Kein Cap im Code.
- **Eval:** `eval_report.hole_laya_zeilen` ruft `answerable_roh` auf ungefilterten Nodes und kalibriert auch auf abgelöster
  Evidenz; `index_fingerprint` erfasst den Stichtag nicht ([#54](../../../../issues/54)).
- Erfassung in der UI fehlt, nur API und Sidecar ([#51](../../../../issues/51)). Labels und Kalibrierung des
  Konflikt-Judges: [#52](../../../../issues/52).
- Zwischen Entscheidung und Schreiben des Datensatzes kann ein Mitternachtswechsel liegen. Status und Entscheidung weichen
  dann um einen Tag ab; der `stichtag` steht im Datensatz.
- Prompt-Injection im Uploadtext kann den Konflikt-Judge Richtung "kein Konflikt" schieben.

## Qodana: `PyTypeHintsInspection` in `gate.py`

Der Report zeigt `SDKs.json` mit leeren `roots`: Der Linter kennt kein Python 3.12 und meldet `float | None` in den
Dataclass-Feldern als ungültig. Die Inspektion ist deshalb nur für `gate.py` abgeschaltet (`qodana.yaml`), nicht global; seit #50 auch für `agent.py` und
`web/main.py`, die mit `date | None` denselben Fehlalarm auslösen.
