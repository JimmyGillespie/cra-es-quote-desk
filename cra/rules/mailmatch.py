"""Find the underwriter's submission email for an EDM (Find_Emails.bas FindTopEmailMatchSingle_Solid).

Pure scoring over message dicts {id, subject, body, sent}; the Graph call that supplies the messages is in cra/graph.py.
Same scoring as the macro: Levenshtein similarity of subject / account name to the normalised EDM name, a small
token-coverage bonus, +0.15 for each phrase hit, at least two token hits required, score capped at 1.
"""
from __future__ import annotations

import re

from .matching import levenshtein_ratio

LOW_SIGNAL = {"inc", "llc", "corp", "co", "company"}
ACCOUNT_LABELS = ["Account Name:", "Account:", "Acct Name:", "Insured:"]


def normalize_exposure_name(s: str) -> str:
    t = (s or "").strip()
    if not t:
        return ""
    if t.lower().startswith("edm_"):
        t = t[4:]
    i = t.rfind("_")
    if i >= 0:
        tail = t[i + 1:]
        if tail[:1].lower() == "y" and tail[1:].isdigit():
            t = t[:i]
    t = t.replace("_", " ")
    return re.sub(r" {2,}", " ", t).strip().lower()


def normalize_subject(s: str) -> str:
    t = (s or "").strip().lower()
    for p in ("re:", "fw:", "fwd:", "es prop quote:"):
        if t.startswith(p):
            t = t[len(p):].strip()
    return t


def canonical(s: str) -> str:
    t = (s or "").strip().lower()
    for ch in ",.-/\\():;&":
        t = t.replace(ch, " ")
    return re.sub(r" {2,}", " ", t).strip()


def useful_tokens(raw: list) -> list:
    seen, out = set(), []
    for r in raw:
        t = r.strip().lower()
        if len(t) >= 2 and t not in LOW_SIGNAL and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def token_hits(tokens: list, text: str) -> int:
    text = " " + (text or "").strip().lower() + " "
    return sum(1 for t in tokens if t and (f" {t} " in text or t in text))


def coverage(tokens: list, text: str) -> float:
    toks = [t for t in tokens if t]
    return token_hits(toks, text) / len(toks) if toks else 0.0


def extract_value(txt: str, label: str) -> str:
    m = re.search(re.escape(label), txt or "", re.I)
    if not m:
        return ""
    rest = (txt or "")[m.end():]
    return rest.split("\r\n", 1)[0].split("\n", 1)[0].strip()


def extract_any(txt: str, labels: list) -> str:
    for l in labels:
        v = extract_value(txt, l)
        if v:
            return v
    return ""


def find_best(exposure_name: str, messages: list) -> dict | None:
    """-> {message, score, lev_subject, account, priority, underwriter, tiv, comments} or None."""
    target = canonical(normalize_exposure_name(exposure_name))
    tokens = useful_tokens(target.split(" ")) or [target]
    best, best_score, best_lev = None, -1.0, -1.0
    for m in messages:
        subj = canonical(normalize_subject(m.get("subject", "")))
        body = m.get("body", "") or ""
        acct = canonical(extract_any(body, ACCOUNT_LABELS).lower())
        lev_s = levenshtein_ratio(subj, target)
        lev_a = levenshtein_ratio(acct, target) if acct else 0.0
        hits = token_hits(tokens, subj)
        if hits + token_hits(tokens, acct) < 2 and len(tokens) > 1:
            continue
        bonus = (0.15 if target and target in subj else 0.0) + (0.15 if acct and target in acct else 0.0)
        score = min(1.0, max(lev_s, lev_a) + 0.1 * max(coverage(tokens, subj), coverage(tokens, acct)) + bonus)
        if score > best_score or (abs(score - best_score) < 1e-6 and lev_s > best_lev):
            best, best_score, best_lev = m, score, lev_s
            if best_score >= 0.999:
                break
    if best is None:
        return None
    body = best.get("body", "") or ""
    return {
        "message": best, "score": round(best_score, 3), "lev_subject": round(best_lev, 3),
        "account": extract_any(body, ACCOUNT_LABELS + ["Insured:"]),
        "priority": extract_any(body, ["Priority:", "Prio:"]),
        "underwriter": extract_any(body, ["Underwriter:", "UW:"]),
        "tiv": extract_any(body, ["TIV:", "Total Insured Value:", "TIV (USD):"]),
        "comments": extract_any(body, ["Comments:", "Notes:", "Remark:"]),
    }


def greeting_name(value: str) -> str:
    """FormatHiNameFromValue: 'roma.razionale@amtrustgroup.com' -> 'Roma'; a plain name is kept; blank -> 'there'."""
    v = (value or "").strip()
    if not v:
        return "there"
    if "@" in v:
        first = v.split("@")[0].split(".")[0].split("_")[0].split("-")[0]
        return first[:1].upper() + first[1:].lower() if first else "there"
    return v
