# 0004 — Rollenteilung zwischen LlamaIndex, LangChain und LangGraph

Status: akzeptiert, mit offener Schuld

## Kontext

Drei Frameworks in einem 50-Zeilen-Agenten wirkt nach Overkill. Die Frage "wieso
LlamaIndex *und* LangGraph?" ist berechtigt und muss beantwortbar sein, ohne auf
Marketing-Material zu zeigen.

## Entscheidung

Jedes Framework hat genau eine Aufgabe:

| Framework | Aufgabe | Wo im Code |
|---|---|---|
| LlamaIndex | Dokumente laden, chunken, embedden, `retrieve` | `rag.py`, `retriever` in `agent.py` |
| LangChain | ChatModel-Abstraktion und PromptTemplate | `llm`, `prompt` in `agent.py` |
| LangGraph | Ablaufsteuerung, Zustand zwischen den Schritten | `StateGraph` in `agent.py` |

LlamaIndex weiß nichts vom LLM. LangChain weiß nichts vom Ablauf. LangGraph weiß
nichts davon, wie retrievt oder formuliert wird.

## Offene Schuld: LangGraph verdient seinen Platz noch nicht

Der Graph ist aktuell eine gerade Linie: `retrieve → answer → END`, keine
Verzweigung. Die eigentliche Entscheidung — das Verweigern bei dünner Beleglage
([0002](0002-abstain-statt-raten.md)) — steckt als gewöhnliches `if` **innerhalb**
des `answer`-Knotens. Dafür braucht niemand ein Graph-Framework.

LangGraph rechtfertigt sich erst, wenn `abstain` ein eigener Knoten ist und die
Kante dahin eine bedingte Kante (`add_conditional_edges`). Dann steht die
Verweigerung sichtbar im Graphen, statt sich in einem Funktionsrumpf zu
verstecken — und der Graph lässt sich als Bild zeigen, statt als Behauptung.

Solange das nicht so ist, ist "LangGraph im Einsatz" zwar wahr, aber schwach.
Wer damit ins Fachgespräch geht, sollte diesen Absatz kennen.
