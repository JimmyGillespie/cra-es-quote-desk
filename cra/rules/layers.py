"""Layer naming rules (mod_EP.bas ExtractLayer, Get_EDMandPort.bas layer = text before the first underscore)."""
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
    s = (s or "").replace("\u2026", "").replace("...", "").strip()
    return s[:maxlen]
