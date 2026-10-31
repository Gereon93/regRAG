from evaluation.evalset import STANDARD_FAELLE, lade_faelle

_FAELLE = lade_faelle(STANDARD_FAELLE)

FAELLE = [{"frage": f["frage"], "erwartet": f["erwartet"]} for f in _FAELLE]
ANTWORTBAR = [f["frage"] for f in _FAELLE if f["erwartet"] == "answer"]
NICHT_ANTWORTBAR = [f["frage"] for f in _FAELLE if f["erwartet"] == "abstain"]
