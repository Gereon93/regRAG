# 0008 — Korpus-agnostisch: DORA ist nur der Seed

## Status

Akzeptiert

## Kontext

RegRAG wurde für DORA (Verordnung (EU) 2022/2554) gebaut, aber nichts im System ist an DORA
gebunden. Retriever, Guard, Abstain-Kante ([0002](0002-abstain-statt-raten.md)) und der
Fingerprint-Diff ([0006](0006-inkrementeller-index-merge-statt-voll-rebuild.md)) arbeiten über
beliebige Markdown-Dokumente, nicht über einen bestimmten Rechtstext. Das "Reg" im Namen meint
diese Bindung an die Belege, nicht die Domäne DORA: das System antwortet nur, was in den
indexierten Dokumenten steht, und verweigert sonst ehrlich statt zu raten.

Mit dem Upload ([#4](https://github.com/Gereon93/regRAG/issues/4)) und jetzt dem Löschen
([#16](https://github.com/Gereon93/regRAG/issues/16)) ist der Korpus vollständig austauschbar.
Bislang zog `entrypoint.sh` DORA aber bei **jedem** Start neu,
sobald `docs_md/` keine `.md`-Datei enthielt — auch dann, wenn jemand DORA gerade bewusst über
`DELETE /documents/{datei}` entfernt hatte. Ein Löschen war damit nicht dauerhaft: der nächste
Neustart holte DORA zurück.

## Entscheidung

DORA ist der Seed des Korpus, kein geschütztes Dokument. Es ist löschbar wie jedes andere
hochgeladene PDF. `entrypoint.sh` legt es genau einmal an, gesteuert über die Marker-Datei
`docs_md/.bootstrap` im `docs_md`-Volume: existiert sie, unterbleibt die erneute Konvertierung —
unabhängig davon, ob `docs_md/` danach noch Markdown-Dateien enthält oder leer ist, weil sie
gelöscht wurden.

Ein leerer Korpus ist damit kein Fehlerzustand mehr, sondern ein gültiger: jede Frage endet über
den Guard im Abstain. Nur der Fall "frisches Volume und kein DORA-PDF vorhanden" bekommt noch
einen Hinweis auf stdout, bricht den Start aber nicht mehr ab.

## Konsequenzen

- Löschen ist dauerhaft. `docker compose restart` bringt ein gelöschtes Dokument nicht zurück.
- Ein frisches Volume (`docker compose down -v`) stellt DORA wieder her, weil `.bootstrap` mit dem
  Volume verschwindet.
- Ein leerer Korpus ist ein gültiger Zustand, kein Absturzgrund — konsequent zu Abstain
  ([0002](0002-abstain-statt-raten.md)).
- Wer stattdessen das BGB oder MaRisk hochlädt und DORA löscht, bekommt ein gleichwertiges RAG mit
  denselben Belegpflicht- und Abstain-Garantien — RegRAG ist damit korpus-agnostisch, nicht an
  eine Verordnung gebunden.
- Geseedet wird nur in einen leeren Korpus. Der Marker wird beim ersten Start aber in jedem Fall
  gesetzt — auch wenn nicht geseedet wurde. Sonst gäbe es zwei Löcher: ein bestehendes Volume von
  vor diesem Branch (hat `CELEX_32022R2554_DE_TXT.md`, aber kein `.bootstrap`) bekäme nie einen
  Marker und würde DORA nach dem Löschen wiederbringen; und wer ohne gemountetes PDF startet,
  eigene Dokumente hochlädt und das DORA-PDF später nachreicht, bekäme DORA ungefragt in einen
  fremden Korpus geschoben.
- Preis dieser Entscheidung: Wer beim ersten Start das PDF vergisst, holt DORA nicht durch
  Nachreichen des PDFs zurück, sondern nur über ein frisches Volume (`docker compose down -v`).
