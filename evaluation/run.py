from deepeval.metrics import FaithfulnessMetric
from deepeval.test_case import LLMTestCase

from agent import app, beleglage_zu_schwach, retriever, ABSTAIN_ANTWORT
from evaluation.dataset import FAELLE
from evaluation.judge import LokalerJudge


def guard_report():
    korrekt = 0
    for fall in FAELLE:
        nodes = retriever.retrieve(fall["frage"])
        entschieden = "abstain" if beleglage_zu_schwach(nodes) else "answer"
        ok = entschieden == fall["erwartet"]
        korrekt += ok
        marker = "ok " if ok else "!! "
        print(f"{marker}{entschieden:8s} erwartet={fall['erwartet']:8s} {fall['frage'][:55]}")
    print(f"\nGuard: {korrekt}/{len(FAELLE)} Entscheidungen korrekt")
    return korrekt


def faithfulness_report(judge, schwelle=0.7):
    antwortbar = [f["frage"] for f in FAELLE if f["erwartet"] == "answer"]
    metric = FaithfulnessMetric(threshold=schwelle, model=judge, async_mode=False)
    scores = []
    for frage in antwortbar:
        out = app.invoke({"frage": frage})
        if out["antwort"] == ABSTAIN_ANTWORT:
            print(f"-- abstained (kein Faithfulness-Test): {frage[:55]}")
            continue
        tc = LLMTestCase(
            input=frage,
            actual_output=out["antwort"],
            retrieval_context=[n.text for n in out["nodes"]],
        )
        metric.measure(tc)
        scores.append(metric.score)
        print(f"faithfulness={metric.score:.2f}  {frage[:55]}")
    if scores:
        print(f"\nFaithfulness Ø = {sum(scores) / len(scores):.2f} über {len(scores)} Antworten")
    return scores


if __name__ == "__main__":
    print("=== Guard-Verhalten ===")
    guard_report()
    print("\n=== Faithfulness (lokaler Judge) ===")
    faithfulness_report(LokalerJudge())
