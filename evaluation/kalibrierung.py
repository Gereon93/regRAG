"""Temperature Scaling, ECE und Cluster-Bootstrap fuer die Laya-Kalibrierung (nur stdlib)."""

import math
import random

from gate import logit

T_MIN, T_MAX = 0.05, 20.0
TOLERANZ_LOG_T = 1e-4
MIN_GRUPPEN = 10
_GOLDEN = (math.sqrt(5) - 1) / 2
_NICHT_SCHAETZBAR = "Temperatur nicht schätzbar"


def _softplus(x):
    """log(1 + exp(x)) ohne Overflow und ohne Saettigung."""
    return max(x, 0.0) + math.log1p(math.exp(-abs(x)))


def _nll(p, y, temperatur):
    """Mittlere binaere Kreuzentropie direkt aus dem skalierten Logit z = logit(p) / T.

    -log(sigmoid(z)) = softplus(-z), -log(1 - sigmoid(z)) = softplus(z). Nur die Eingabe p wird geklemmt
    (wie im Gate); ein geklemmtes q wuerde den Verlust sicher falscher Faelle deckeln und ein zu scharfes T waehlen.
    """
    summe = 0.0
    for pi, yi in zip(p, y, strict=True):
        z = logit(pi) / temperatur
        summe += _softplus(-z) if yi else _softplus(z)
    return summe / len(p)


def fitte_temperatur(p, y):
    """T minimiert die mittlere NLL (Goldener Schnitt ueber log T in [0.05, 20])."""
    if len(set(y)) < 2:
        return 1.0, [_NICHT_SCHAETZBAR]
    lo, hi = math.log(T_MIN), math.log(T_MAX)
    c = hi - _GOLDEN * (hi - lo)
    d = lo + _GOLDEN * (hi - lo)
    fc, fd = _nll(p, y, math.exp(c)), _nll(p, y, math.exp(d))
    while hi - lo > TOLERANZ_LOG_T:
        if fc < fd:
            hi, d, fd = d, c, fc
            c = hi - _GOLDEN * (hi - lo)
            fc = _nll(p, y, math.exp(c))
        else:
            lo, c, fc = c, d, fd
            d = lo + _GOLDEN * (hi - lo)
            fd = _nll(p, y, math.exp(d))
    temperatur = math.exp((lo + hi) / 2)
    warnungen = []
    if (hi - math.log(T_MAX)) > -2 * TOLERANZ_LOG_T or (lo - math.log(T_MIN)) < 2 * TOLERANZ_LOG_T:
        warnungen.append(f"Temperatur am Rand des Suchintervalls [{T_MIN}, {T_MAX}]")
    return temperatur, warnungen


def ece(p, y, bins=5):
    """Expected Calibration Error mit gleich breiten Bins auf [0, 1]."""
    if not p:
        return None
    n_ges = len(p)
    zellen = [[] for _ in range(bins)]
    for pi, yi in zip(p, y, strict=True):
        zellen[min(int(pi * bins), bins - 1)].append((pi, yi))
    ergebnis, fehler = [], 0.0
    for zelle in zellen:
        n = len(zelle)
        if n == 0:
            ergebnis.append({"n": 0, "konfidenz": None, "trefferquote": None})
            continue
        konfidenz = sum(pi for pi, _ in zelle) / n
        treffer = sum(yi for _, yi in zelle) / n
        fehler += n / n_ges * abs(konfidenz - treffer)
        ergebnis.append({"n": n, "konfidenz": konfidenz, "trefferquote": treffer})
    return {"ece": fehler, "bins": ergebnis}


def _index_fingerprint(report):
    return (report.get("laya") or {}).get("index_fingerprint")


def _pruefe_vergleichbar(a, b):
    for feld in ("eval_version", "min_retrieval_score", "kosten"):
        if a[feld] != b[feld]:
            raise ValueError(f"Reports nicht vergleichbar: {feld} weicht ab")
    if _index_fingerprint(a) != _index_fingerprint(b):
        raise ValueError("Reports nicht vergleichbar: laya.index_fingerprint weicht ab (Index neu gebaut?)")
    ids_a = {f["id"] for f in a["test_faelle"]}
    ids_b = {f["id"] for f in b["test_faelle"]}
    if ids_a != ids_b or len(ids_a) != len(a["test_faelle"]) or len(ids_b) != len(b["test_faelle"]):
        raise ValueError("Reports nicht vergleichbar: Menge der Test-ids weicht ab")


def vergleiche(a, b, seed=0, b_resamples=10_000):
    """Gepaarter Cluster-Bootstrap (Resampling ueber `gruppe`), Kandidat b minus Baseline a."""
    _pruefe_vergleichbar(a, b)
    kosten_a = {f["id"]: f["kosten"] for f in a["test_faelle"]}
    summen, anzahl = {}, {}
    for f in b["test_faelle"]:
        g = f["gruppe"]
        summen[g] = summen.get(g, 0.0) + f["kosten"] - kosten_a[f["id"]]
        anzahl[g] = anzahl.get(g, 0) + 1
    gruppen = sorted(summen)
    n_fall = sum(anzahl.values())
    differenz = sum(summen.values()) / n_fall
    if len(gruppen) < MIN_GRUPPEN:
        return {
            "differenz": differenz,
            "ci95": None,
            "signifikant": None,
            "grund": f"weniger als {MIN_GRUPPEN} Gruppen im Test-Split ({len(gruppen)})",
        }
    s = [summen[g] for g in gruppen]
    c = [anzahl[g] for g in gruppen]
    rng = random.Random(seed)
    k = len(gruppen)
    stat = []
    for _ in range(b_resamples):
        idx = rng.choices(range(k), k=k)
        stat.append(sum(s[i] for i in idx) / sum(c[i] for i in idx))
    stat.sort()
    lo = stat[min(int(0.025 * b_resamples), b_resamples - 1)]
    hi = stat[min(int(0.975 * b_resamples), b_resamples - 1)]
    return {"differenz": differenz, "ci95": [lo, hi], "signifikant": hi < 0, "grund": None}
