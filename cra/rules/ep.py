"""EP results: the 'Results' sheet, the EP GrUp / layer tabs, and the statistics block.

Port of mod_EP.bas GetEPResults_Simple_Combined plus the INDEX/MATCH formulas on 'EP GrUp' and the layer tabs.

A *column* is one (analysis, perspective, EP type):  key1 = GU_AEP_WS , key2 = GU_AEP_WS_<layer>
(Results rows 2 and 3).  The workbook looked values up with MATCH(...,0) so the FIRST column with a key won; that is
kept, and duplicates are now reported instead of silently ignored.

Decisions kept from the workbook:
  * GU tables ignore the layer (ground-up loss is the same for every layer) - first analysis with the peril wins.
  * GR tables key on peril + layer.
  * 'All Perils' = the multi-peril (YY, grouped) analysis for losses and for Std Dev / CV.
  * 'All Perils' Pure Premium = sum of the single-peril pure premiums (as before) - the YY value is shown as a check.
Fixed: EP GrUp had a typed-in Std Dev and CV for All Perils (D23, D24). Both now come from the YY analysis.
"""
from __future__ import annotations

from collections import OrderedDict

import pandas as pd

from ..constants import PERIL_ALIASES, PERIL_COLUMNS, PERSPECTIVES, RP_LIST, RP_PROBABILITY
from ..rms.core import gj, gobj, gv, json_or_none, to_num
from .layers import extract_layer

EP_TYPES = ("AEP", "OEP")
SINGLE_PERILS = [c for c, _ in PERIL_COLUMNS if c != "YY"]


def norm_peril(code: str) -> str:
    c = (code or "").strip().upper()
    return PERIL_ALIASES.get(c, c)


def _rp_key(v) -> str:
    try:
        f = float(v)
        return str(int(f)) if f.is_integer() else str(f)
    except (TypeError, ValueError):
        return str(v)


def parse_ep(text_or_obj) -> dict:
    """RMS /ep response -> {epType: [value per RP_LIST]}."""
    data = json_or_none(text_or_obj) if isinstance(text_or_obj, str) else text_or_obj
    out = {}
    if not isinstance(data, list):
        return out
    pos = {str(rp): i for i, rp in enumerate(RP_LIST)}
    for curve in data:
        ep_type = gj(curve, "epType")
        val = gv(curve, "value")
        rets = gv(val, "returnPeriods") if isinstance(val, dict) else None
        vals = gv(val, "positionValues") if isinstance(val, dict) else None
        if ep_type and isinstance(rets, list) and isinstance(vals, list):
            arr = [None] * len(RP_LIST)
            for j, rp in enumerate(rets):
                k = pos.get(_rp_key(rp))
                if k is not None and j < len(vals):
                    arr[k] = vals[j]
            out[ep_type] = arr
    return out


def parse_stats(text_or_obj) -> tuple:
    """RMS /stats response -> (pure premium, std dev, cv) with the macro's fallback field names."""
    p = json_or_none(text_or_obj) if isinstance(text_or_obj, str) else text_or_obj
    s2 = p[0] if isinstance(p, list) and p else (p if isinstance(p, dict) else None)
    if not isinstance(s2, dict):
        return (None, None, None)
    pp, sd, cv = gj(s2, "purePremium"), gj(s2, "totalStdDev"), gj(s2, "cv")
    if not pp:
        pp = gj(s2, "purePremiumAmount")
    if not sd:
        sd = gj(s2, "standardDeviation")
    if not cv:
        cv = gj(s2, "coefficientOfVariation")
    return (to_num(pp), to_num(sd), to_num(cv))


def analysis_record(meta: dict, ep_by_persp: dict, stats_by_persp: dict) -> dict:
    """One analysis as stored on the account: meta fields used by the Meta tab + parsed EP/stats."""
    name = gj(meta, "analysisName")
    return {
        "analysisId": gj(meta, "analysisId"),
        "appAnalysisId": gj(meta, "appAnalysisId"),
        "analysisName": name,
        "layer": extract_layer(name),
        "peril": gj(meta, "peril"),
        "perilCode": norm_peril(gj(meta, "perilCode")),
        "subPeril": gj(meta, "subPeril"),
        "exposureName": gj(meta, "exposureName"),
        "exposureResourceId": gj(meta, "exposureResourceId"),
        "exposureResourceType": gj(meta, "exposureResourceType"),
        "region": gj(meta, "region"),
        "engineVersion": gj(meta, "engineVersion"),
        "lossAmplification": gj(meta, "lossAmplification"),
        "currencyCode": gj(gobj(meta, "currency"), "currencyCode"),
        "currencyName": gj(gobj(meta, "currency"), "currencyName"),
        "currencyVintage": gj(gobj(meta, "currency"), "currencyVintage"),
        "modelProfile": gj(gobj(meta, "modelProfile"), "name"),
        "outputProfile": gj(gobj(meta, "outputProfile"), "name"),
        "username": gj(meta, "username"),
        "description": gj(meta, "description"),
        "createDate": gj(meta, "createDate"),
        "ep": ep_by_persp,        # {"GU": {"AEP": [...], "OEP": [...]}, "GR": ..., "RL": ...}
        "stats": stats_by_persp,  # {"GU": [pp, sd, cv], ...}
    }


# ---------------------------------------------------------------- the 'Results' sheet
def results_columns(analyses: list, yy_alias: set | None = None) -> list:
    """Flatten analyses -> one column per (analysis, perspective, EP type), in the workbook's order.

    yy_alias: analysis IDs to ALSO publish as 'All Perils' (YY). This is the Single_Peril macro: when a layer was only run
    for one peril there is no grouped analysis, so the single-peril columns were copied and relabelled YY by hand.
    The copy is added after the real columns, so the real peril column is still there.
    """
    yy_alias = {str(x) for x in (yy_alias or set())}
    cols = []
    for a in analyses:
        for persp in PERSPECTIVES:
            curves = (a.get("ep") or {}).get(persp) or {}
            pp, sd, cv = (a.get("stats") or {}).get(persp) or (None, None, None)
            for ep_type in EP_TYPES:
                if ep_type not in curves:
                    continue
                codes = [a["perilCode"]] + (["YY"] if str(a["analysisId"]) in yy_alias and a["perilCode"] != "YY" else [])
                for code in codes:
                    k1 = f"{persp}_{ep_type}_{code}"
                    cols.append({"key1": k1, "key2": f"{k1}_{a['layer']}", "perspective": persp, "epType": ep_type,
                                 "perilCode": code, "layer": a["layer"], "analysisId": a["analysisId"],
                                 "analysisName": a["analysisName"], "peril": a["peril"], "subPeril": a["subPeril"],
                                 "portfolioName": a.get("exposureName", ""), "exposureId": a.get("exposureResourceId", ""),
                                 "aliasOf": a["perilCode"] if code != a["perilCode"] else "",
                                 "values": list(curves[ep_type]), "purePremium": pp, "stdDev": sd, "cv": cv})
    return cols


def results_frame(cols: list) -> pd.DataFrame:
    """The Results tab as a table: header block, 12 return periods, then Pure Premium / Std Dev / CV."""
    labels = ["Index 1", "Index 2", None, "Portfolio Name:", "Portfolio ID", "Analysis Name:", "Layer:", "Analysis ID:",
              "Peril:", "Peril Code:", "Subperil:", "Perspective:", "EP Type"] + list(RP_LIST) + ["Pure Premium:", "Std Dev:", "CV:"]
    data = {"": labels}
    for i, c in enumerate(cols):
        data[f"c{i}"] = ([c["key1"], c["key2"], None, c["portfolioName"], c["exposureId"], c["analysisName"], c["layer"],
                          c["analysisId"], c["peril"], c["perilCode"], c["subPeril"], c["perspective"], c["epType"]]
                         + c["values"] + [c["purePremium"], c["stdDev"], c["cv"]])
    return pd.DataFrame(data)


# ---------------------------------------------------------------- lookups
def find_column(cols: list, perspective: str, ep_type: str, peril: str, layer: str | None = None):
    """First column that matches (MATCH(...,0))."""
    for c in cols:
        if c["perspective"] == perspective and c["epType"] == ep_type and c["perilCode"] == peril:
            if layer is None or c["layer"] == layer:
                return c
    return None


def ep_table(cols: list, perspective: str, ep_type: str, layer: str | None = None) -> pd.DataFrame:
    """Return period x peril table (EP GrUp rows 8-19 / layer tab). Missing -> None (the workbook showed text '0')."""
    data = OrderedDict()
    data["Probability"] = [RP_PROBABILITY[rp] for rp in RP_LIST]
    data["Return Period"] = list(RP_LIST)
    for code, label in PERIL_COLUMNS:
        c = find_column(cols, perspective, ep_type, code, layer)
        data[label] = list(c["values"]) if c else [None] * len(RP_LIST)
    return pd.DataFrame(data)


def stats_table(cols: list, perspective: str, layer: str | None = None) -> pd.DataFrame:
    """Pure Premium / Std Dev / CV rows by peril. Statistics are read from the AEP column, as the formulas did."""
    rows = OrderedDict([("Pure Premium:", []), ("Std Dev:", []), ("CV:", [])])
    per_peril_pp = []
    yy = find_column(cols, perspective, "AEP", "YY", layer)
    for code, _label in PERIL_COLUMNS:
        if code == "YY":
            continue
        c = find_column(cols, perspective, "AEP", code, layer)
        if c and c.get("aliasOf"):
            c = None
        rows["Pure Premium:"].append(c["purePremium"] if c else None)
        rows["Std Dev:"].append(c["stdDev"] if c else None)
        rows["CV:"].append(c["cv"] if c else None)
        if c and c["purePremium"] is not None:
            per_peril_pp.append(c["purePremium"])
    all_pp = sum(per_peril_pp) if per_peril_pp else (yy["purePremium"] if yy else None)
    all_sd = yy["stdDev"] if yy else None
    all_cv = yy["cv"] if yy else None
    out = pd.DataFrame({"": list(rows.keys())})
    out["All Perils"] = [all_pp, all_sd, all_cv]
    for (code, label), pp, sd, cv in zip([x for x in PERIL_COLUMNS if x[0] != "YY"],
                                         rows["Pure Premium:"], rows["Std Dev:"], rows["CV:"]):
        out[label] = [pp, sd, cv]
    return out


def layers_in_order(analyses: list, preferred: list | None = None) -> list:
    seen, out = set(), []
    for l in (preferred or []) + [a["layer"] for a in analyses]:
        if l and l not in seen and any(a["layer"] == l for a in analyses):
            seen.add(l)
            out.append(l)
    return out


def check_results(analyses: list, cols: list, tol: float = 0.005) -> list:
    """Warnings the workbook never raised. -> [(level, message)]."""
    out = []
    # duplicate (peril, layer) analyses: MATCH would silently use the first
    seen = {}
    for a in analyses:
        k = (a["layer"], a["perilCode"])
        seen.setdefault(k, []).append(a["analysisId"])
    for (layer, peril), ids in seen.items():
        if len(ids) > 1:
            out.append(("warn", f"Layer {layer}, peril {peril}: {len(ids)} analyses ({', '.join(ids)}). "
                                f"Only the first is used - untick the others."))
    # ground-up should not depend on the layer
    for ep_type in EP_TYPES:
        for code, _ in PERIL_COLUMNS:
            vals = [c for c in cols if c["perspective"] == "GU" and c["epType"] == ep_type and c["perilCode"] == code]
            if len(vals) > 1:
                ref = vals[0]["values"]
                for other in vals[1:]:
                    for r, o in zip(ref, other["values"]):
                        if r and o is not None and abs(o - r) / abs(r) > tol:
                            out.append(("warn", f"Ground-up {ep_type} for {code} differs between layers "
                                                f"{vals[0]['layer']} and {other['layer']} (more than {tol:.1%})."))
                            break
                    else:
                        continue
                    break
    # sum of single-peril pure premium vs the grouped (YY) pure premium
    for layer in {a["layer"] for a in analyses}:
        yy = find_column(cols, "GR", "AEP", "YY", layer)
        parts = [find_column(cols, "GR", "AEP", p, layer) for p in SINGLE_PERILS]
        parts = [p["purePremium"] for p in parts if p and p["purePremium"] is not None]
        if yy and yy["purePremium"] and parts:
            tot = sum(parts)
            if abs(tot - yy["purePremium"]) / abs(yy["purePremium"]) > 0.01:
                out.append(("warn", f"Layer {layer}: sum of single-peril pure premium ({tot:,.0f}) differs from the "
                                    f"grouped analysis ({yy['purePremium']:,.0f}) by more than 1%."))
    # a layer with no multi-peril analysis cannot show 'All Perils'
    for layer in {a["layer"] for a in analyses}:
        if not find_column(cols, "GR", "AEP", "YY", layer):
            out.append(("info", f"Layer {layer}: no grouped (multi-peril) analysis, so 'All Perils' losses are blank."))
    return out
