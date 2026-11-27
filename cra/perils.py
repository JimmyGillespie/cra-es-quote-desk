"""Which perils does a quote cover?  (so an analysis can be run for every covered peril automatically)

An RMS policy is written "for one peril, applying to all locations" and carries a `peril` field, so the perils an account covers
are the perils of its policies. This module reads that field tolerantly (RMS may return a code, a name, or an object holding
both), maps it to the app's peril codes, and says where each answer came from. Nothing here calls RMS or Streamlit.

Order of evidence  (the first that gives an answer decides the default; the rest are shown as suggestions):
  1. the policies themselves                                  -> "policies"
  2. a policy that covers ALL perils                          -> every peril the app can run
  3. perils already modelled for this EDM                     -> "earlier analyses"
  4. perils named in the underwriter's comments               -> "the underwriter's comments"
If a policy's peril cannot be read, it is reported (unmapped) so the mapping can be extended - it is never silently ignored.
"""
from __future__ import annotations

import re

PERIL_ORDER = ["WS", "EQ", "CS", "WT", "FL", "WF", "TR"]          # the codes the app can run, in a sensible run order
PERIL_LABEL = {"WS": "Windstorm", "EQ": "Earthquake", "CS": "Severe convective storm", "WT": "Winterstorm",
               "FL": "Flood", "WF": "Wildfire", "TR": "Terrorism"}

# words RMS (or an underwriter) uses for each peril. Whole words only; two-letter words are never used on free text.
_WORDS = {
    "WS": {"ws", "windstorm", "wind", "hurricane", "hu", "tc", "cyclone", "typhoon"},
    "EQ": {"eq", "earthquake", "quake", "seismic"},
    "CS": {"cs", "scs", "sts", "tornado", "hail", "convective", "thunderstorm"},
    "WT": {"wt", "winter", "winterstorm", "freeze"},
    "FL": {"fl", "flood", "floods", "flooding"},
    "WF": {"wf", "fr", "wildfire", "bushfire"},
    "TR": {"tr", "terror", "terrorism"},
}
_TWO_LETTER = {w for ws in _WORDS.values() for w in ws if len(w) <= 3}
_ALL = re.compile(r"\b(all\s+perils?|all\s+other\s+perils?|multi\s*-?\s*perils?|every\s+peril|all)\b", re.I)
_PHRASE_EQ = re.compile(r"fire\s+following", re.I)                 # a sub-peril of earthquake, not wildfire
_PEL_KEYS = ("peril", "perilcode", "perilname", "perils")
_OBJ_KEYS = ("code", "perilcode", "name", "perilname", "description", "abbreviation", "label", "value", "shortname")


def _words(text: str) -> list:
    return re.findall(r"[a-z]+", (text or "").lower().replace("_", " "))


def code_from_text(text: str, *, free_text: bool = False) -> str:
    """'Earthquake' / 'EQ' / 'Tornado/Hail' / 'Winter Storm' -> 'EQ' / 'EQ' / 'CS' / 'WT'.  '' when it is not a peril.
    'ALL' for 'All Perils' / 'Multi-Peril'.  free_text=True (comments) ignores short codes and 'all'."""
    t = (text or "").strip()
    if not t:
        return ""
    if _PHRASE_EQ.search(t):
        return "EQ"
    if not free_text and t.lower() == "to":                          # the old run-list label for tornado/hail, only on its own
        return "CS"
    if not free_text and _ALL.search(t):
        return "ALL"
    ws = set(_words(t))
    for code in PERIL_ORDER:
        hit = ws & _WORDS[code]
        if free_text:
            hit = {w for w in hit if w not in _TWO_LETTER}
        if hit:
            return code
    if not free_text and ws & {"fire"}:
        return "WF"
    return ""


def codes_in_comments(comments: str) -> list:
    """Perils the underwriter names in free text, in run order. Short codes are ignored (FL is Florida, TO is a word)."""
    ws = set(_words(comments))
    return [c for c in PERIL_ORDER if {w for w in ws & _WORDS[c] if w not in _TWO_LETTER}]


def policy_peril_texts(raw: dict) -> list:
    """Every piece of peril text on a policy as RMS returned it: a string, a number, an object holding a code / name, or a list."""
    out = []

    def add(v):
        if v is None or isinstance(v, bool):
            return
        if isinstance(v, str):
            if v.strip():
                out.append(v.strip())
        elif isinstance(v, (int, float)):
            out.append(str(int(v)) if float(v).is_integer() else str(v))
        elif isinstance(v, dict):
            low = {str(k).lower(): x for k, x in v.items()}
            for k in _OBJ_KEYS:
                if isinstance(low.get(k), str) and low[k].strip():
                    out.append(low[k].strip())
            if not any(isinstance(low.get(k), str) for k in _OBJ_KEYS):
                for k in ("id", "perilid"):
                    if isinstance(low.get(k), (int, float)) and not isinstance(low.get(k), bool):
                        out.append(str(int(low[k])))
        elif isinstance(v, list):
            for x in v:
                add(x)

    if isinstance(raw, dict):
        for k, v in raw.items():
            if str(k).lower() in _PEL_KEYS:
                add(v)
    seen, uniq = set(), []
    for t in out:
        if t.lower() not in seen:
            seen.add(t.lower())
            uniq.append(t)
    return uniq


def _ordered(codes) -> list:
    return [c for c in PERIL_ORDER if c in set(codes)]


def derive_perils(policies: list, comments: str = "", earlier=(), available=None) -> dict:
    """What a quote covers.  policies: layer_api.norm_policy() dicts (their 'raw' holds the policy as RMS returned it).
    Returns {codes, source, by_code, policy_texts, unmapped, all_perils, suggested, flake, n_policies}."""
    avail = [c for c in (available or PERIL_ORDER) if c in PERIL_LABEL]
    texts, mapped, unmapped, all_flag = [], {}, [], False
    for pol in policies or []:
        for t in policy_peril_texts(pol.get("raw") if isinstance(pol, dict) and "raw" in pol else pol):
            texts.append(t)
            code = code_from_text(t)
            if code == "ALL":
                all_flag = True
            elif code:
                mapped.setdefault(code, []).append(t)
            else:
                unmapped.append(t)
    comment_codes = codes_in_comments(comments)
    earlier_codes = [c for c in _ordered(earlier) if c in avail]
    by_code = {}
    if all_flag:
        codes, source = list(avail), "a policy that covers all perils"
        for c in codes:
            by_code[c] = ["policy: all perils"]
    elif mapped:
        codes, source = [c for c in _ordered(mapped) if c in avail], "the policies"
        for c in codes:
            by_code[c] = [f"policy: {sorted(set(mapped[c]))[0]}"]
    elif earlier_codes:
        codes, source = earlier_codes, "earlier analyses in this EDM"
        for c in codes:
            by_code[c] = ["earlier analysis"]
    elif comment_codes:
        codes, source = [c for c in comment_codes if c in avail], "the underwriter's comments"
        for c in codes:
            by_code[c] = ["comments"]
    else:
        codes, source = [], "none"
    suggested = [c for c in _ordered(set(comment_codes) | set(earlier_codes)) if c not in codes and c in avail]
    return {"codes": codes, "source": source, "by_code": by_code, "policy_texts": sorted(set(texts), key=str.lower),
            "unmapped": sorted(set(unmapped), key=str.lower), "all_perils": all_flag, "suggested": suggested,
            "flake": bool(re.search(r"\bflake\b", comments or "", re.I)), "n_policies": len(policies or [])}


def labels(codes) -> str:
    return ", ".join(PERIL_LABEL.get(c, c) for c in codes) or "none"


def describe(res: dict) -> str:
    """One line for the page: 'Earthquake, Flood (from the policies)'."""
    if not res["codes"]:
        why = f" The policies say: {', '.join(res['policy_texts'])}." if res["policy_texts"] else " The policies carry no peril."
        return "No perils could be worked out." + why
    return f"{labels(res['codes'])} (from {res['source']})"
