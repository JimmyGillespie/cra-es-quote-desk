"""Pure helpers behind the Results page: which perspectives exist, the curve / table / headline numbers for a chosen perspective
(ground up, gross, retained), a layer and a curve type. No Streamlit and no RMS here.

`cols` is the list of result columns the pipeline builds (one per analysis x perspective x curve x peril), each a dict with
perspective, epType, perilCode, layer, values (12 return periods, RP_LIST order), purePremium, stdDev, cv, aliasOf.
"""
from __future__ import annotations

import pandas as pd

from .constants import PERIL_COLUMNS, PERIL_NAMES, RP_LIST, RP_PROBABILITY

PERSPECTIVES = [
    ("GU", "Ground up", "Before any policy terms: the full modelled loss from the events."),
    ("GR", "Gross", "After the layer's terms (the limit and what it sits above) are applied."),
    ("RL", "Retained", "What is left after reinsurance, where RMS returned it."),
]
CURVES = [
    ("AEP", "Annual total (AEP)", "Total loss in a year, adding every event. Use this for the average annual loss and pricing."),
    ("OEP", "Largest single event (OEP)", "The biggest single event in a year. Use this to judge one bad event."),
]
KEY_RPS = [10, 25, 50, 100, 250, 500, 1000]
SINGLE = [c for c, _l in PERIL_COLUMNS if c != "YY"]
NAME = dict(PERIL_NAMES)


def layers_in(cols: list, preferred=()) -> list:
    """The layers that have results: the account's own order first (its portfolios), then any others in the order they appear."""
    present = list(dict.fromkeys(c["layer"] for c in cols if c.get("layer")))
    first = [l for l in preferred if l in present]
    return first + [l for l in present if l not in first]


def layer_limit(layer: str):
    """The limit of a layer written like '15Mxs10M' (dollars), or None when the name is not a layer."""
    try:
        from .rules.layers import layer_terms
        t = layer_terms(layer)
        return t[0] if t and t[0] else None
    except Exception:                                                  # noqa: BLE001
        return None


def has_loss(values) -> bool:
    return any(v is not None and v == v and v > 1 for v in (values or []))


def available_perspectives(cols: list) -> list:
    """[(code, label, help)] for the perspectives that actually have curves, in a fixed order."""
    present = {c["perspective"] for c in cols}
    return [p for p in PERSPECTIVES if p[0] in present]


def find_col(cols: list, persp: str, ep_type: str, peril: str, layer: str | None = None):
    """The first matching column (the workbook's MATCH). Ground up ignores the layer."""
    for c in cols:
        if c["perspective"] == persp and c["epType"] == ep_type and c["perilCode"] == peril:
            if persp == "GU" or layer is None or c["layer"] == layer:
                return c
    return None


def perils_with_loss(cols: list, persp: str, ep_type: str, layer: str | None) -> list:
    """Peril codes to show, All Perils first, then the single perils that actually have a loss."""
    out = []
    for code in ["YY"] + SINGLE:
        c = find_col(cols, persp, ep_type, code, layer)
        if c and has_loss(c["values"]):
            out.append(code)
    return out


def ep_points(cols: list, persp: str, ep_type: str, layer: str | None, codes: list | None = None) -> pd.DataFrame:
    """Long table for the EP chart: Return period, Probability, Peril, Code, Loss."""
    rows = []
    for code in codes if codes is not None else perils_with_loss(cols, persp, ep_type, layer):
        c = find_col(cols, persp, ep_type, code, layer)
        if not c:
            continue
        for rp, v in zip(RP_LIST, c["values"]):
            if v is not None and v == v:
                rows.append({"Return period": rp, "Probability": RP_PROBABILITY[rp], "Peril": NAME.get(code, code), "Code": code, "Loss": float(v)})
    return pd.DataFrame(rows, columns=["Return period", "Probability", "Peril", "Code", "Loss"])


def rp_label(rp: int) -> str:
    return f"1 in {rp:,}"


def rp_table(cols: list, persp: str, ep_type: str, layer: str | None, rps: list | None = None, codes: list | None = None) -> pd.DataFrame:
    """Loss by return period and peril, as numbers: columns 'Return period', 'Chance each year', then one column per peril."""
    rps = rps or KEY_RPS
    codes = codes if codes is not None else perils_with_loss(cols, persp, ep_type, layer)
    data = {"Return period": [rp_label(r) for r in rps], "Chance each year": [f"{RP_PROBABILITY[r]:.2%}".replace(".00%", "%") for r in rps]}
    for code in codes:
        c = find_col(cols, persp, ep_type, code, layer)
        data[NAME.get(code, code)] = [(c["values"][RP_LIST.index(r)] if c else None) for r in rps]
    return pd.DataFrame(data)


def aal_parts(cols: list, persp: str, layer: str | None) -> dict:
    """Average annual loss (pure premium): the total, where it came from, and the per-peril split."""
    parts = {}
    for code in SINGLE:
        c = find_col(cols, persp, "AEP", code, layer)
        if c and not c.get("aliasOf") and c.get("purePremium") is not None and c["purePremium"] > 0:
            parts[code] = float(c["purePremium"])
    yy = find_col(cols, persp, "AEP", "YY", layer)
    total_parts = sum(parts.values())
    if yy and yy.get("purePremium") is not None:
        total, source = float(yy["purePremium"]), "the grouped (all perils) analysis"
    elif parts:
        total, source = total_parts, "the sum of the single-peril analyses"
    else:
        total, source = None, ""
    dom = max(parts, key=parts.get) if parts and total_parts > 0 else None
    return {"total": total, "source": source, "parts": parts, "sum_of_parts": total_parts or None, "dominant": dom,
            "dominant_share": (parts[dom] / total_parts) if dom else None,
            "cv": yy.get("cv") if yy else None, "std_dev": yy.get("stdDev") if yy else None}


def headline(cols: list, persp: str, ep_type: str, layer: str | None, tiv: float | None = None) -> dict:
    """The numbers at the top of the page for one perspective / layer / curve."""
    aal = aal_parts(cols, persp, layer)
    yy = find_col(cols, persp, ep_type, "YY", layer)
    rp = {r: (yy["values"][RP_LIST.index(r)] if yy else None) for r in KEY_RPS}
    return {"aal": aal["total"], "aal_source": aal["source"], "aal_parts": aal["parts"], "dominant": aal["dominant"],
            "dominant_share": aal["dominant_share"], "rp": rp, "has_grouped": yy is not None,
            "aal_pct_tiv": (aal["total"] / tiv) if (tiv and aal["total"] is not None) else None,
            "rp_pct_tiv": {r: (v / tiv) if (tiv and v is not None) else None for r, v in rp.items()}, "cv": aal["cv"]}


def layer_table(cols: list, persp: str, ep_type: str, layers: list) -> pd.DataFrame:
    """One row per layer: average annual loss and the loss at the key return periods (All Perils)."""
    rows = []
    for ly in layers:
        h = headline(cols, persp, ep_type, ly)
        row = {"Layer": ly, "Average annual loss": h["aal"]}
        for r in (10, 100, 250, 500, 1000):
            row[rp_label(r)] = h["rp"][r]
        rows.append(row)
    return pd.DataFrame(rows)


def layer_curves(cols: list, persp: str, ep_type: str, layers: list) -> pd.DataFrame:
    rows = []
    for ly in layers:
        c = find_col(cols, persp, ep_type, "YY", ly)
        if c:
            rows += [{"Return period": rp, "Layer": ly, "Loss": float(v)} for rp, v in zip(RP_LIST, c["values"]) if v is not None and v == v]
    return pd.DataFrame(rows, columns=["Return period", "Layer", "Loss"])


def peril_split(cols: list, persp: str, layer: str | None) -> pd.DataFrame:
    """Average annual loss by peril with its share: Peril, Code, Average annual loss, Share, Label."""
    parts = aal_parts(cols, persp, layer)["parts"]
    tot = sum(parts.values())
    rows = [{"Peril": NAME.get(c, c), "Code": c, "Average annual loss": v, "Share": (v / tot if tot else 0.0)} for c, v in sorted(parts.items(), key=lambda kv: -kv[1])]
    return pd.DataFrame(rows, columns=["Peril", "Code", "Average annual loss", "Share"])


def csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8-sig")
