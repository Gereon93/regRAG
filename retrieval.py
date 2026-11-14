"""Retriever mit Überabruf gegen Verdrängung durch abgelöste Fassungen (docs/adr/0015, #53).

Ohne schwere Imports, damit die CI ihn testen kann; die Regel selbst liegt in `gueltigkeit.waehle_treffer`.
"""

import gueltigkeit

TOP_K = 3


class UeberabrufRetriever:
    """Holt `top_k * faktor` Kandidaten und wählt daraus mit `gueltigkeit.waehle_treffer`.

    Gewählt wird am Stichtag der Anfrage wie im Gate (#50, #70); ohne ihn am Tag der `uhr`, je Abruf bestimmt.

    Eine Klasse statt einer Funktion, weil `/chat` und `evaluation/run.py` einen Retriever mit `retrieve` erwarten.
    """

    def __init__(self, index, top_k, faktor, uhr=gueltigkeit.heute_berlin):
        if faktor < 1:
            raise ValueError("faktor muss mindestens 1 sein.")
        self.top_k = top_k
        self.uhr = uhr
        self.kandidaten = index.as_retriever(similarity_top_k=top_k * faktor)

    def retrieve(self, frage, stichtag=None):
        kandidaten = self.kandidaten.retrieve(frage)
        return gueltigkeit.waehle_treffer(kandidaten, self.top_k, stichtag or self.uhr())
