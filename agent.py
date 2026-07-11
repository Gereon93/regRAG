from typing import List, TypedDict

import openai
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph

import config
from rag import index

ABSTAIN_ANTWORT = "Nicht eindeutig in DORA belegt."

retriever = index.as_retriever(similarity_top_k=3)

llm = ChatOpenAI(model=config.LLM_MODELL, base_url=config.LLM_BASE_URL,
                 api_key=config.LLM_API_KEY, temperature=config.LLM_TEMPERATURE,
                 timeout=config.LLM_TIMEOUT, max_retries=1)

prompt = ChatPromptTemplate.from_messages([
    ("system", "Beantworte NUR anhand des Kontexts und nenne die Quelle. Reicht der Kontext nicht, sag das ehrlich."),
    ("human", "Kontext:\n{kontext}\n\nFrage: {frage}"),
])


class S(TypedDict):
    frage: str
    nodes: List
    antwort: str


def retrieve(s):
    s["nodes"] = retriever.retrieve(s["frage"])
    return s


def beleglage_zu_schwach(nodes):
    return not nodes or nodes[0].score < config.MIN_RETRIEVAL_SCORE  # docs/adr/0002


def naechster_schritt(s):
    return "abstain" if beleglage_zu_schwach(s["nodes"]) else "answer"


def abstain(s):
    s["antwort"] = ABSTAIN_ANTWORT
    return s


def answer(s):
    kontext = "\n\n".join(n.text for n in s["nodes"])
    try:
        s["antwort"] = llm.invoke(
            prompt.format_messages(kontext=kontext, frage=s["frage"])
        ).content
    except (openai.APIConnectionError, openai.APITimeoutError) as e:
        s["antwort"] = f"LLM nicht erreichbar ({type(e).__name__}) — bitte erneut versuchen."
    return s


def _baue_graph():
    g = StateGraph(S)
    g.add_node("retrieve", retrieve)
    g.add_node("answer", answer)
    g.add_node("abstain", abstain)
    g.set_entry_point("retrieve")
    g.add_conditional_edges("retrieve", naechster_schritt,
                            {"answer": "answer", "abstain": "abstain"})
    g.add_edge("answer", END)
    g.add_edge("abstain", END)
    return g.compile()


app = _baue_graph()

if __name__ == "__main__":
    out = app.invoke({"frage": "Welche Anforderungen stellt DORA an das IKT-Risikomanagement?"})
    print(out["antwort"])
    print("\n--- Quellen ---")
    for n in out["nodes"]:
        print(round(n.score, 2), n.metadata.get("file_name"))
