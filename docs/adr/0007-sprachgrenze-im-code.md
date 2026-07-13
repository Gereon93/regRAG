# 0007 — Sprachgrenze im Code: Domäne deutsch, Technik englisch

## Status

Akzeptiert

## Kontext

Der Code mischt beide Sprachen: `lade_oder_baue_index`, `beleglage_zu_schwach`, `ABSTAIN_ANTWORT`
neben `index`, `retriever`, `chunk`, `fingerprint`. Das wirkt beim Lesen zufällig, war aber nie
begründet — es ist gewachsen. Zwei Auswege standen zur Wahl: alles englisch, oder die Grenze
bewusst ziehen.

Alles englisch scheitert an der Domäne. Der Gegenstand ist deutschsprachige Regulatorik: DORA liegt
auf Deutsch vor, die Nutzertexte sind deutsch, und die Fachbegriffe der Compliance-Seite —
Beleglage, Quelle, Fundstelle, Abstain — haben keine verlustfreie englische Entsprechung, die hier
irgendjemand benutzen würde. Ein `evidence_too_weak` wäre eine Übersetzung, keine Fachsprache.

Umgekehrt scheitert alles deutsch an der Technik. `VectorStoreIndex`, `retrieve`, `embedding`,
`chunk` sind die Begriffe der Bibliotheken; sie einzudeutschen erzeugt eine zweite Sprache, die
niemand in einer Fehlermeldung, einem Stacktrace oder einer LlamaIndex-Doku wiederfindet.

## Entscheidung

Die Sprachgrenze verläuft an der Domänengrenze — nicht an Dateien, nicht an Modulen:

- **Deutsch:** Fachbegriffe der Compliance-Domäne und alles, was der Nutzer sieht.
  `beleglage_zu_schwach`, `quelle`, `ABSTAIN_ANTWORT`, `saeubere_dateiname`, UI-Texte,
  Fehlermeldungen, Commit-Messages, ADRs.
- **Englisch:** Begriffe, die aus den Frameworks stammen oder in ihnen nachgeschlagen werden.
  `index`, `retriever`, `embedding`, `chunk`, `fingerprint`, `node`, `collection`.

Der Test bei jedem neuen Namen: Steht der Begriff so im Verordnungstext oder im Gespräch mit einem
Compliance-Menschen? Dann deutsch. Steht er in der LlamaIndex- oder Chroma-Doku? Dann englisch.

## Konsequenzen

- Die Mischung bleibt — aber sie ist ab jetzt eine Aussage über die Domänengrenze und nicht mehr
  Zufall. Wo sie verletzt wird, ist das ein Review-Befund.
- Grenzfälle bleiben (`_quelle_metadata`, `loesche_nodes`). Die Regel entscheidet sie: der Kopf des
  Namens folgt dem, was der Bezeichner *ist* — Metadaten einer Quelle, gelöschte Nodes.
- Ein späterer Umzug auf durchgängiges Englisch bleibt möglich, kostet dann aber die Fachsprache
  der Domäne. Diese Entscheidung ist der Preis, den wir dafür bewusst nicht zahlen.
