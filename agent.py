from typing import TypedDict

import openai
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph

import config
from gate import (
    GateDecision,
    abstain_antwort,
    aktiviere_score_log,
    entscheide_und_eskaliere,
    grenzen_aus,
)
from grounding import SYSTEM_PROMPT, baue_kontext, pruefe_antwort_und_eskaliere
from laya import baue_gate
from rag import index

retriever = index.as_retriever(similarity_top_k=3)
gate = baue_gate(config)
ESKALATIONS_GRENZEN = grenzen_aus(config)

llm = ChatOpenAI(model=config.LLM_MODELL, base_url=config.LLM_BASE_URL,
                 api_key=config.LLM_API_KEY, temperature=config.LLM_TEMPERATURE,
                 timeout=config.LLM_TIMEOUT, max_retries=1)

prompt = ChatPromptTemplate.from_messages([
    ("system", SYSTEM_PROMPT),
    ("human", "Kontext:\n{kontext}\n\nFrage: {frage}"),
])


class S(TypedDict):
    frage: str
    treffer: list
    nodes: list
    entscheidung: GateDecision
    vorgemerkt: bool
    antwort: str
    llm_fehler: bool


def pruefe(frage, nodes):
    return entscheide_und_eskaliere(gate, config.ESKALATION_DATEI, frage, nodes, ESKALATIONS_GRENZEN)


def pruefe_grounding(frage, nodes, antwort, vorherige):
    return pruefe_antwort_und_eskaliere(
        config.ESKALATION_DATEI, frage, nodes, antwort, vorherige, ESKALATIONS_GRENZEN
    )


def retrieve(s):
    s["treffer"] = retriever.retrieve(s["frage"])
    pruefung = pruefe(s["frage"], s["treffer"])
    s["nodes"] = pruefung.entscheidung.genutzte_nodes(s["treffer"])
    s["entscheidung"] = pruefung.entscheidung
    s["vorgemerkt"] = pruefung.vorgemerkt
    return s


def naechster_schritt(s):
    return "abstain" if s["entscheidung"].verweigert else "answer"


def abstain(s):
    s["antwort"] = abstain_antwort(s["vorgemerkt"], s["entscheidung"].reasons)
    return s


def answer(s):
    s["llm_fehler"] = False
    try:
        s["antwort"] = llm.invoke(
            prompt.format_messages(kontext=baue_kontext(s["nodes"]), frage=s["frage"])
        ).content
    except (openai.APIConnectionError, openai.APITimeoutError) as e:
        s["llm_fehler"] = True
        s["antwort"] = f"LLM nicht erreichbar ({type(e).__name__}) — bitte erneut versuchen."
    return s


def ground(s):
    if s["llm_fehler"]:
        return s
    pruefung = pruefe_grounding(s["frage"], s["treffer"], s["antwort"], s["entscheidung"])
    if pruefung.verweigert:
        s["entscheidung"] = pruefung.entscheidung
        s["vorgemerkt"] = pruefung.vorgemerkt
        s["antwort"] = abstain_antwort(pruefung.vorgemerkt, pruefung.entscheidung.reasons)
    return s


def _baue_graph():
    g = StateGraph(S)
    g.add_node("retrieve", retrieve)
    g.add_node("answer", answer)
    g.add_node("ground", ground)
    g.add_node("abstain", abstain)
    g.set_entry_point("retrieve")
    g.add_conditional_edges("retrieve", naechster_schritt,
                            {"answer": "answer", "abstain": "abstain"})
    g.add_edge("answer", "ground")
    g.add_edge("ground", END)
    g.add_edge("abstain", END)
    return g.compile()


app = _baue_graph()

if __name__ == "__main__":
    aktiviere_score_log()
    out = app.invoke({"frage": "Welche Anforderungen stellt DORA an das IKT-Risikomanagement?"})
    print(out["antwort"])
    print("\n--- Quellen ---")
    for i, n in enumerate(out["nodes"], start=1):
        print(f"[{i}]", round(n.score, 2), n.metadata.get("file_name"))
