"""RDS event table (mod_RDS.bas GetRMS_RDS_Events_GU_AllGR).

Output: Eventid | EventName | GU_Loss | <layer>_GR ... exactly as the 'RDS Events' tab.
The macro overwrote cells in analysis order, so which analysis won was accidental. The rule here is explicit:
  * prefer the grouped (YY) analysis, otherwise the first analysis with a loss for that event;
  * if two analyses disagree by more than 0.5% a warning is raised.
"""
from __future__ import annotations

import pandas as pd

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
