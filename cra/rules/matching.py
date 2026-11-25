"""Matching analyses to an EDM (Get_Analyses.bas MatchSmart) - same tests, but the reason is now reported.

The macro silently listed every fuzzy hit. Here each analysis carries a match reason so a wrong hit can be un-ticked,
and an exact exposureName match is preferred over name guessing.
"""
from __future__ import annotations

import re

STOP_WORDS = {
    "inc", "llc", "group", "company", "co", "corp", "corporation", "associates", "holdings", "limited", "ltd", "plc",
    "partners", "enterprise", "enterprises", "services", "solutions", "systems", "technologies", "technology",
    "global", "international", "distributors", "supportive", "center", "glass", "portfolio", "rms", "edm", "tiv", "and",
}
_DATE_TOKEN = re.compile(r"\b\d{1,2}[a-z]{3}\d{2}\b", re.I)          # e.g. 08Sep25


def clean_tokens(s: str) -> list:
    s = (s or "")
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
    """'' when no match, else the reason ('name equal', 'token overlap', 'description overlap', 'similar name',
    'all tokens present'). Identical decision logic to MatchSmart."""
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
    """Reason this analysis belongs to the EDM, or ''. Exact exposureName wins; otherwise the macro's fuzzy test."""
    if (a.get("exposureName") or "").strip().lower() == (edm_name or "").strip().lower() and a.get("exposureName"):
        return "exact EDM"
    return match_smart(edm_name, a.get("analysisName", ""), a.get("description", ""))
