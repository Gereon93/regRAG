from rag import index
from evaluation.dataset import FAELLE

retriever = index.as_retriever(similarity_top_k=3)


def top_scores():
    zeilen = []
    for fall in FAELLE:
        nodes = retriever.retrieve(fall["frage"])
        top = nodes[0].score if nodes else 0.0
        zeilen.append({"frage": fall["frage"], "erwartet": fall["erwartet"], "top": top})
    return zeilen


def bester_schwellwert(zeilen):
    werte = sorted({z["top"] for z in zeilen})
    kandidaten = [(a + b) / 2 for a, b in zip(werte, werte[1:])]
    bestes = (0.0, -1)
    for schwelle in kandidaten:
        korrekt = sum(
            (z["top"] >= schwelle) == (z["erwartet"] == "answer") for z in zeilen
        )
        if korrekt > bestes[1]:
            bestes = (schwelle, korrekt)
    return bestes


if __name__ == "__main__":
    zeilen = top_scores()
    zeilen.sort(key=lambda z: z["top"], reverse=True)
    for z in zeilen:
        print(f"{z['top']:.3f}  {z['erwartet']:8s}  {z['frage'][:60]}")

    antwortbar = [z["top"] for z in zeilen if z["erwartet"] == "answer"]
    abstain = [z["top"] for z in zeilen if z["erwartet"] == "abstain"]
    print(f"\nAntwortbar : min={min(antwortbar):.3f}  max={max(antwortbar):.3f}")
    print(f"Off-Topic  : min={min(abstain):.3f}  max={max(abstain):.3f}")
    luecke_unten, luecke_oben = min(antwortbar), max(abstain)
    print(f"Trennlücke : [{luecke_oben:.3f} .. {luecke_unten:.3f}]  "
          f"Mitte={((luecke_oben + luecke_unten) / 2):.3f}")
    schwelle, korrekt = bester_schwellwert(zeilen)
    print(f"Bester Schwellwert: {schwelle:.3f}  ({korrekt}/{len(zeilen)} korrekt)")
