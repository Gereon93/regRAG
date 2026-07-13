# 0006 — Inkrementeller Index-Merge statt Voll-Rebuild

## Status

Akzeptiert

## Kontext

Der Fingerprint deckte den gesamten Korpus in einem einzigen Hash ab. Jede Änderung — auch ein
einzelnes neues Dokument — löschte die Collection und embeddete alles neu: rund fünf Minuten pro
Dokument, linear wachsend. Mit dem Upload über die Web-UI (Issue #4) ist das nicht mehr tragbar.
Der Nutzer wartete sonst darauf, dass Dokumente neu indexiert werden, die sich nicht geändert haben.

## Entscheidung

Der Fingerprint wird zur Map `{dateiname: sha256}` über Markdown plus Quellen-Sidecar.

Start und Upload teilen sich denselben Merge-Pfad: `rag.indexiere(md_pfad)` entfernt vorhandene
Nodes des Dokuments über `collection.delete(where={"file_name": ...})` und fügt die neuen per
`index.insert()` ein. Der Startup-Abgleich fährt lediglich den Diff aus `dokumente.diff()` ab —
er kennt keinen eigenen Indexierungscode.

Ein Voll-Rebuild bleibt für genau zwei Fälle: Wechsel des Embedding-Modells oder der Distanzmetrik
(dort sind die vorhandenen Vektoren tatsächlich wertlos) sowie `REGRAG_INDEX_NEU_BAUEN=1`.

## Konsequenzen

- Ein Upload kostet nur die Embedding-Zeit des neuen Dokuments. Gemessen: Kaltstart mit DORA 360 s,
  Neustart mit DORA und NIS2 31 s.
- Der Löschpfad hängt am Metadatenfeld `file_name`. Es muss an jedem Node gesetzt sein — der Reader
  in `rag._quelle_metadata` garantiert das.
- Der Fingerprint ist zugleich das Inventar: was darin steht, ist indexiert. Weicht er von
  `docs_md/` ab, holt der nächste Start die Differenz nach — auch nach einem Absturz mitten in der
  Indexierung.
- Die Konsistenz ist nicht transaktional. Stirbt der Prozess zwischen `index.insert()` und dem
  Schreiben des Fingerprints, wird das Dokument beim nächsten Start erneut indexiert; der
  Löschschritt am Anfang von `indexiere()` verhindert dabei Dubletten.
