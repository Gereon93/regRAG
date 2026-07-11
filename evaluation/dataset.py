ANTWORTBAR = [
    "Welche Anforderungen stellt DORA an das IKT-Risikomanagement?",
    "Was versteht DORA unter einem schwerwiegenden IKT-bezogenen Vorfall?",
    "Welche Pflichten bestehen beim Management des IKT-Drittparteienrisikos?",
    "Was schreibt DORA zu Tests der digitalen operationalen Resilienz vor?",
    "Welche Rolle hat das Leitungsorgan im IKT-Risikomanagementrahmen?",
    "Welche Meldepflichten bestehen bei schwerwiegenden IKT-Vorfällen?",
    "Was verlangt DORA hinsichtlich Ausstiegsstrategien bei IKT-Drittdienstleistern?",
    "Was regelt DORA zu bedrohungsgeleiteten Penetrationstests?",
]

NICHT_ANTWORTBAR = [
    "Wie backe ich einen Schokoladenkuchen?",
    "Was ist die Hauptstadt von Australien?",
    "Wie hoch ist der gesetzliche Mindestlohn in Deutschland?",
    "Welche Aktien sollte ich diese Woche kaufen?",
    "Wie wird das Wetter morgen in Berlin?",
    "Wer wurde 2022 Fußballweltmeister?",
]

FAELLE = (
    [{"frage": f, "erwartet": "answer"} for f in ANTWORTBAR]
    + [{"frage": f, "erwartet": "abstain"} for f in NICHT_ANTWORTBAR]
)
