"""Matching analyses to an EDM (Get_Analyses.bas MatchSmart), now with the reason for every match.

Confident matches: the analysis' exposureName IS the EDM ('exact EDM'), or its name starts with '<EDM>:' - which is how
grouped (All Perils) analyses are named and why they were missed before ('EDM in name'). Everything else is fuzzy and
is never ticked by default. If an EDM was run more than once, only the newest analysis per layer and peril is ticked.
"""
from __future__ import annotations

import re

import pandas as pd

from ..constants import PERIL_COLUMNS, PERIL_FROM_TEXT
from .layers import assign_layer

STOP_WORDS = {
    "inc", "llc", "group", "company", "co", "corp", "corporation", "associates", "holdings", "limited", "ltd", "plc",
    "partners", "enterprise", "enterprises", "services", "solutions", "systems", "technologies", "technology",
    "global", "international", "distributors", "supportive", "center", "glass", "portfolio", "rms", "edm", "tiv", "and",
}
_DATE_TOKEN = re.compile(r"\b\d{1,2}[a-z]{3}\d{2}\b", re.I)
CONFIDENT = {"exact EDM", "EDM in name"}


def clean_tokens(s: str) -> list:
    s = s or ""
    for ch in ("_", "-", ":"):
        s = s.replace(ch, " ")
    s = _DATE_TOKEN.sub("", s)
    s = re.sub("rms edm", "", s, flags=re.I)
    s = re.sub("portfolio", "", s, flags=re.I)
    return [t for t in s.lower().strip().split() if t and t not in STOP_WORDS]


def token_overlap(a: list, b: list) -> int:
    return sum(1 for x in a if x in b)


def levenshtein_ratio(s1: str, s2: str) -> float:
    s1, s2 = (s1 or "").lower(), (s2 or "").lower()
    m, n = len(s1), len(s2)
    mx = max(m, n)
    if mx == 0:
        return 1.0
    prev = list(range(n + 1))
    for i in range(1, m + 1):
        cur = [i] + [0] * n
        for j in range(1, n + 1):
            cost = 0 if s1[i - 1] == s2[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return 1 - prev[n] / mx


def match_smart(exposure_key: str, analysis_name: str, desc: str = "") -> str:
    """'' when no match, else the reason. Identical decision logic to MatchSmart."""
    if (exposure_key or "").strip().lower() == (analysis_name or "").strip().lower():
        return "name equal"
    e, a, d = clean_tokens(exposure_key), clean_tokens(analysis_name), clean_tokens(desc)
    if e and token_overlap(e, a) / len(e) >= 0.7:
        return "token overlap"
    if e and token_overlap(e, d) / len(e) >= 0.7:
        return "description overlap"
    if levenshtein_ratio(" ".join(e), " ".join(a)) >= 0.85:
        return "similar name"
    if e and all(t in a for t in e):
        return "all tokens present"
    return ""


def classify_analysis(edm_name: str, a: dict) -> str:
    edm = (edm_name or "").strip().lower()
    if a.get("exposureName") and a["exposureName"].strip().lower() == edm:
        return "exact EDM"
    nm = (a.get("analysisName") or "").strip().lower()
    if edm and (nm.startswith(edm + ":") or nm.startswith(edm + " :")):
        return "EDM in name"
    return match_smart(edm_name, a.get("analysisName", ""), a.get("description", ""))


def peril_code_of(a: dict) -> str:
    pc = (a.get("perilCode") or "").strip().upper()
    if pc:
        return {"TO": "CS", "FR": "WF", "HU": "WS"}.get(pc, pc)
    return PERIL_FROM_TEXT.get((a.get("peril") or "").strip().lower(), "")


def is_grouped(a: dict) -> bool:
    """An 'All Perils' result (multi-peril group), not an input to a grouping job."""
    return peril_code_of(a) == "YY" or "multi" in (a.get("peril") or "").lower() or \
        (a.get("analysisName") or "").lower().startswith("grouped")


_PERIL_ORDER = {c: i for i, (c, _l) in enumerate(PERIL_COLUMNS)}


def select_for_edm(edm_name: str, analyses: list, portfolios: list, previous_selection: dict | None = None) -> list:
    """Analyses belonging to the EDM, with layer, peril, match reason and a sensible default tick."""
    layer_order = {}
    for i, p in enumerate(portfolios or []):
        layer_order.setdefault(p["layer"], i)
    out = []
    for a in analyses:
        why = classify_analysis(edm_name, a)
        if not why:
            continue
        b = dict(a)
        b["match"] = why
        b["layer"] = assign_layer(b, portfolios)
        b["perilCode"] = peril_code_of(b)
        b["grouped"] = is_grouped(b)
        out.append(b)
    # newest first within each (layer, peril); only the newest confident one is ticked by default
    out.sort(key=lambda x: x.get("createDate", ""), reverse=True)
    seen = set()
    for b in out:
        key = (b["layer"], b["perilCode"] or b.get("peril"))
        b["superseded"] = False
        b["selected"] = False
        if b["match"] in CONFIDENT:
            if key in seen:
                b["superseded"] = True
            else:
                seen.add(key)
                b["selected"] = True
        if previous_selection is not None and b["analysisId"] in previous_selection:
            b["selected"] = bool(previous_selection[b["analysisId"]])
    out.sort(key=lambda x: (layer_order.get(x["layer"], 99), x["layer"], _PERIL_ORDER.get(x["perilCode"], 99),
                            "".join(chr(255 - ord(c)) for c in x.get("createDate", "")[:19])))
    return out


def coverage(items: list, only_selected: bool = True) -> pd.DataFrame:
    """Layer x peril matrix: number of analyses (blank = nothing). The first thing to look at when a tab is empty."""
    rows = [i for i in items if i.get("selected") or not only_selected]
    layers = list(dict.fromkeys(i["layer"] for i in rows))
    cols = [(c, l) for c, l in PERIL_COLUMNS if any(i["perilCode"] == c for i in rows)]
    data = {"Layer": layers}
    for code, label in cols:
        data[label] = [sum(1 for i in rows if i["layer"] == ly and i["perilCode"] == code) or None for ly in layers]
    return pd.DataFrame(data)
