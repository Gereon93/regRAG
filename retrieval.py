"""Retriever mit Überabruf gegen Verdrängung durch abgelöste Fassungen (docs/adr/0015, #53).

Ohne schwere Imports, damit die CI ihn testen kann; die Regel selbst liegt in `gueltigkeit.waehle_treffer`.
"""

import gueltigkeit


class UeberabrufRetriever:
    """Holt `top_k * faktor` Kandidaten und wählt daraus mit `gueltigkeit.waehle_treffer`; Stichtag je Abruf.

    Eine Klasse statt einer Funktion, weil `/chat` und `evaluation/run.py` einen Retriever mit `retrieve` erwarten.
    """

    def __init__(self, index, top_k, faktor, uhr=gueltigkeit.heute_berlin):
        if faktor < 1:
            raise ValueError("faktor muss mindestens 1 sein.")
        self.top_k = top_k
        self.uhr = uhr
        self.kandidaten = index.as_retriever(similarity_top_k=top_k * faktor)

    def retrieve(self, frage):
        return gueltigkeit.waehle_treffer(self.kandidaten.retrieve(frage), self.top_k, self.uhr())
