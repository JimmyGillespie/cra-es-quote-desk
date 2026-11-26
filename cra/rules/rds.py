"""RDS event table (mod_RDS.bas GetRMS_RDS_Events_GU_AllGR).

Output: Eventid | EventName | GU_Loss | <layer>_GR ... exactly as the 'RDS Events' tab. The macro overwrote cells in
analysis order, so which analysis won was accidental. Rule: prefer the grouped (YY) analysis, otherwise the first with a
loss; a >0.5% disagreement raises a warning. Layers are the account's layers (see rules/layers.py), so one layer is one column.
"""
from __future__ import annotations

import pandas as pd

from ..constants import PERIL_NAMES
from ..rms.core import gj, gv, json_or_none, to_num


def parse_elt(payload) -> dict:
    """/elt response -> {eventid: summed perspvalue}. Handles an array or {items:[...]}."""
    if isinstance(payload, str):
        payload = json_or_none(payload)
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        payload = payload["items"]
    sums = {}
    if isinstance(payload, list):
        for v in payload:
            if isinstance(v, dict):
                eid = gj(v, "eventid")
                if eid:
                    sums[eid] = sums.get(eid, 0.0) + (to_num(gv(v, "perspvalue")) or 0.0)
    return sums


def merge_rds(rds_by_analysis: list, event_map: dict, layer_order: list) -> tuple:
    """rds_by_analysis: [{analysisId, layer, perilCode, GU: {eid: v}, GR: {eid: v}}] -> (DataFrame, warnings)."""
    warnings = []
    layers = list(layer_order)
    for r in rds_by_analysis:
        if r["layer"] not in layers:
            layers.append(r["layer"])
    ordered = sorted(rds_by_analysis, key=lambda r: (0 if r.get("perilCode") == "YY" else 1))
    gu, gr = {}, {l: {} for l in layers}

    def put(store, key, val, who, what):
        if not val or val <= 0:
            return
        if key in store:
            old = store[key][0]
            if abs(old - val) / max(abs(old), 1.0) > 0.005:
                warnings.append(("warn", f"{what}: analyses {store[key][1]} and {who} give different values "
                                         f"({old:,.0f} vs {val:,.0f}); the first is shown."))
            return
        store[key] = (val, who)

    for r in ordered:
        for eid, v in (r.get("GU") or {}).items():
            put(gu, eid, v, r["analysisId"], f"Event {eid} ground-up")
        for eid, v in (r.get("GR") or {}).items():
            put(gr[r["layer"]], eid, v, r["analysisId"], f"Event {eid} layer {r['layer']}")
    rows = []
    for eid, name in event_map.items():
        row = {"Eventid": eid, "EventName": name, "GU_Loss": round(gu[eid][0]) if eid in gu else None}
        for l in layers:
            row[f"{l}_GR"] = round(gr[l][eid][0]) if eid in gr[l] else None
        rows.append(row)
    return pd.DataFrame(rows), warnings


def annotate(df: pd.DataFrame, event_perils: dict, perils_modelled: set) -> pd.DataFrame:
    """Add Peril and Status columns: tells 'peril not modelled' from 'no loss' (the workbook left both blank)."""
    out = df.copy()
    loss_cols = [c for c in out.columns if c == "GU_Loss" or c.endswith("_GR")]
    perils = [event_perils.get(str(e), "") for e in out["Eventid"]]
    out.insert(2, "Peril", [PERIL_NAMES.get(p, p) for p in perils])
    status = []
    for p, (_, row) in zip(perils, out.iterrows()):
        if any(pd.notna(row[c]) and row[c] for c in loss_cols):
            status.append("Loss")
        elif p and p not in perils_modelled:
            status.append("Peril not modelled")
        else:
            status.append("No loss")
    out["Status"] = status
    return out
