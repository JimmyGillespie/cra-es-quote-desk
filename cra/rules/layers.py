"""Layer naming rules (mod_EP.bas ExtractLayer, Get_EDMandPort.bas layer = text before the first underscore).

New: the same layer is often spelled two ways in one account ('15Mxs10M' on the portfolio, '15Mx10M' in the analysis
name). The Ben Davis workbook shows the result - two RDS columns for one layer. Analyses are now tied to a portfolio by
portfolio ID first, then by a spelling-insensitive key, so one layer is always one layer.
"""
from __future__ import annotations

import re


def layer_from_portfolio_name(portfolio_name: str) -> str:
    """'10Mxs0_Noonan_Plaza' -> '10Mxs0' (FetchPortfoliosToSheet)."""
    name = (portfolio_name or "").strip()
    p = name.find("_")
    return name[:p] if p > 0 else name


def extract_layer(analysis_name: str) -> str:
    """Text after 'PORTFOLIO:' up to the first '_' (ExtractLayer). The whole name is used when there is no marker."""
    name = analysis_name or ""
    i = name.find("PORTFOLIO:")
    t = name[i + len("PORTFOLIO:"):].strip() if i >= 0 else name
    j = t.find("_")
    return t[:j] if j >= 0 else t


def layer_key(s: str) -> str:
    """Spelling-insensitive key: '15Mxs10M', '15Mx10M' and '15M xs 10M' are the same layer."""
    k = re.sub(r"[^a-z0-9.]", "", (s or "").lower())
    return k.replace("xs", "x")


def canonical_layer(raw: str, known_layers: list) -> str:
    """Return the known layer spelled the same way as `raw`, else `raw`."""
    key = layer_key(raw)
    for k in known_layers or []:
        if layer_key(k) == key:
            return k
    return raw


def assign_layer(a: dict, portfolios: list) -> str:
    """Layer for an analysis: portfolio ID first (exposureResourceId), then the name, matched to known layers."""
    pid = str(a.get("exposureResourceId") or "").strip()
    try:
        pid = str(int(float(pid))) if pid else ""
    except ValueError:
        pass
    by_id = {str(p["portfolio_id"]): p["layer"] for p in portfolios or []}
    if pid and pid != "0" and pid in by_id:
        return by_id[pid]
    return canonical_layer(extract_layer(a.get("analysisName", "")), [p["layer"] for p in portfolios or []])


_AMT = r"(\d+(?:\.\d+)?)\s*([kKmM])?"


def _amount(num: str, unit: str | None, default_unit: str | None) -> float:
    u = (unit or default_unit or "").lower()
    return float(num) * {"m": 1e6, "k": 1e3}.get(u, 1.0)


_LAYER_RE = re.compile(r"^\s*(TIV|(\d+(?:\.\d+)?)\s*([kKmM])?)\s*x(?:s)?\s*(\d+(?:\.\d+)?)\s*([kKmM])?\s*$", re.I)


def layer_terms(layer: str):
    """'15Mxs10M' -> (15e6, 10e6); 'TIVxs0' -> (None, 0.0); unknown spelling -> None. Dollars."""
    m = _LAYER_RE.match((layer or "").replace("$", ""))
    if not m:
        return None
    tiv, ln, lu, an, au = m.groups()
    hint = lu or au or "m"
    att = _amount(an, au, hint if float(an) else "")
    if tiv.upper() == "TIV":
        return (None, att)
    return (_amount(ln, lu, hint), att)


def terms_in_text(text: str) -> set:
    """Layers mentioned in UW comments: '10M primary', 'P: $25m', '15x10', '$15m x $10m' -> {(limit, attach)} in dollars."""
    t = (text or "").replace("$", " ")
    out = set()
    for m in re.finditer(rf"(?<![\w.]){_AMT}\s*(?:xs|x|excess of)\s*{_AMT}(?![\w.])", t, re.I):
        n1, u1, n2, u2 = m.groups()
        hint = u1 or u2 or "m"
        out.add((_amount(n1, u1, hint), _amount(n2, u2, hint if float(n2) else "")))
    for m in re.finditer(rf"(?<![\w.]){_AMT}\s*primary", t, re.I):
        out.add((_amount(m.group(1), m.group(2), "m"), 0.0))
    for m in re.finditer(rf"\bP\s*:\s*{_AMT}", t):
        out.add((_amount(m.group(1), m.group(2), "m"), 0.0))
    return out


def clean_sheet_name(name: str) -> str:
    """CleanSheetName: strip \\ / ? * [ ] : and cut to 31 characters."""
    return re.sub(r"[\\/?*\[\]:]", "", name or "")[:31]


def unique_sheet_names(layers: list, reserved: set | None = None) -> dict:
    """layer -> Excel-safe unique sheet name (the VBA would silently overwrite a clash)."""
    used = {r.lower() for r in (reserved or set())}
    out = {}
    for layer in layers:
        base = clean_sheet_name(layer) or "Layer"
        name, n = base, 2
        while name.lower() in used:
            suffix = f"~{n}"
            name = base[:31 - len(suffix)] + suffix
            n += 1
        used.add(name.lower())
        out[layer] = name
    return out


def limit_group_name(s: str, maxlen: int = 64) -> str:
    """Limit64: strip ellipsis characters and cap the length (Post_GroupAnalysis.bas)."""
    return (s or "").replace("\u2026", "").replace("...", "").strip()[:maxlen]
