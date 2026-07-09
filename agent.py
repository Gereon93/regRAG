from typing import TypedDict, List
from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

from rag import index

LM_STUDIO_BASE_URL = "http://localhost:1234/v1"
LOKALES_CHAT_MODELL = "google/gemma-4-12b"
MIN_RETRIEVAL_SCORE = 0.4  # docs/adr/0002
ABSTAIN_ANTWORT = "Nicht eindeutig in DORA belegt."

retriever = index.as_retriever(similarity_top_k=3)

llm = ChatOpenAI(model=LOKALES_CHAT_MODELL, base_url=LM_STUDIO_BASE_URL,
                 api_key="lm-studio", temperature=0)

prompt = ChatPromptTemplate.from_messages([
    ("system", "Beantworte NUR anhand des Kontexts und nenne die Quelle. Reicht der Kontext nicht, sag das ehrlich."),
    ("human", "Kontext:\n{kontext}\n\nFrage: {frage}"),
])

class S(TypedDict):
    frage: str; nodes: List; antwort: str

def retrieve(s):
    s["nodes"] = retriever.retrieve(s["frage"])
    return s

def beleglage_zu_schwach(nodes):
    return not nodes or nodes[0].score < MIN_RETRIEVAL_SCORE

def answer(s):
    if beleglage_zu_schwach(s["nodes"]):
        s["antwort"] = ABSTAIN_ANTWORT
        return s
    kontext = "\n\n".join(n.text for n in s["nodes"])
    try:
        antwort = llm.invoke(prompt.format_messages(kontext=kontext, frage=s["frage"])).content
    except Exception as e:
        antwort = f"Konnte nicht antworten ({type(e).__name__}) — bitte erneut versuchen."
    s["antwort"] = antwort
    return s

g = StateGraph(S)
g.add_node("retrieve", retrieve)
g.add_node("answer", answer)
g.set_entry_point("retrieve")
g.add_edge("retrieve", "answer")
g.add_edge("answer", END)
app = g.compile()

if __name__ == "__main__":
    out = app.invoke({"frage": "Welche Anforderungen stellt DORA an das IKT-Risikomanagement?"})
    print(out["antwort"])
    print("\n--- Quellen ---")
    for n in out["nodes"]:
        print(round(n.score, 2), n.metadata.get("file_name"))
