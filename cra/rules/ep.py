"""EP results: the 'Results' sheet, the EP GrUp / layer tabs, statistics and result checks.

Port of mod_EP.bas GetEPResults_Simple_Combined plus the INDEX/MATCH formulas on 'EP GrUp' and the layer tabs.
A *column* is one (analysis, perspective, EP type): key1 = GU_AEP_WS, key2 = GU_AEP_WS_<layer>.

Kept from the workbook: lookups are first-match; GU tables ignore the layer; GR tables key on peril + layer;
'All Perils' losses / Std Dev / CV come from the grouped (YY) analysis; All Perils Pure Premium = sum of single perils.
Fixed: EP GrUp had a typed-in All Perils Std Dev / CV (1,110,743 / 8.63 in the Ben Davis file; the YY analysis says
1,676,110 / 6.58) - both now come from the YY analysis.
"""
from __future__ import annotations

from collections import OrderedDict

import pandas as pd

from ..constants import PERIL_ALIASES, PERIL_COLUMNS, PERIL_NAMES, PERSPECTIVES, RP_LIST, RP_PROBABILITY, TAIL_RP
from ..rms.core import gj, gobj, gv, json_or_none, to_num
from .layers import extract_layer, layer_key

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
    pp = pp or gj(s2, "purePremiumAmount")
    sd = sd or gj(s2, "standardDeviation")
    cv = cv or gj(s2, "coefficientOfVariation")
    return (to_num(pp), to_num(sd), to_num(cv))


def analysis_record(meta: dict, ep_by_persp: dict, stats_by_persp: dict) -> dict:
    """One analysis as stored on the account: the Meta-tab fields + parsed EP / stats."""
    name = gj(meta, "analysisName")
    return {
        "analysisId": gj(meta, "analysisId"), "appAnalysisId": gj(meta, "appAnalysisId"),
        "analysisName": name, "layer": extract_layer(name), "peril": gj(meta, "peril"),
        "perilCode": norm_peril(gj(meta, "perilCode")), "subPeril": gj(meta, "subPeril"),
        "exposureName": gj(meta, "exposureName"), "exposureResourceId": gj(meta, "exposureResourceId"),
        "exposureResourceType": gj(meta, "exposureResourceType"), "region": gj(meta, "region"),
        "engineVersion": gj(meta, "engineVersion"), "lossAmplification": gj(meta, "lossAmplification"),
        "currencyCode": gj(gobj(meta, "currency"), "currencyCode"), "currencyName": gj(gobj(meta, "currency"), "currencyName"),
        "currencyVintage": gj(gobj(meta, "currency"), "currencyVintage"),
        "modelProfile": gj(gobj(meta, "modelProfile"), "name"), "outputProfile": gj(gobj(meta, "outputProfile"), "name"),
        "username": gj(meta, "username"), "description": gj(meta, "description"), "createDate": gj(meta, "createDate"),
        "ep": ep_by_persp, "stats": stats_by_persp,
    }


def results_columns(analyses: list, yy_alias: set | None = None) -> list:
    """Flatten analyses -> one column per (analysis, perspective, EP type), in the workbook's order.

    yy_alias: analysis IDs to ALSO publish as 'All Perils' (the Single_Peril macro): when a layer was run for one peril
    only, its columns are copied and relabelled YY. The real peril column is kept.
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
    out = pd.DataFrame({"": list(rows.keys())})
    out["All Perils"] = [sum(per_peril_pp) if per_peril_pp else (yy["purePremium"] if yy else None),
                         yy["stdDev"] if yy else None, yy["cv"] if yy else None]
    for (_code, label), pp, sd, cv in zip([x for x in PERIL_COLUMNS if x[0] != "YY"],
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


def gu_overview(cols: list) -> dict:
    """Ground-up annual average loss (AAL = pure premium), dominant peril and its share."""
    yy = find_column(cols, "GU", "AEP", "YY")
    parts = {}
    for code in SINGLE_PERILS:
        c = find_column(cols, "GU", "AEP", code)
        if c and not c.get("aliasOf") and c["purePremium"] is not None:
            parts[code] = c["purePremium"]
    total_single = sum(parts.values())
    aal = yy["purePremium"] if yy and yy["purePremium"] is not None else (total_single or None)
    dom = max(parts, key=parts.get) if parts and total_single > 0 else None
    return {"aal": aal, "aal_source": "grouped" if yy and yy["purePremium"] is not None else "sum of perils",
            "dominant": dom, "dominant_share": (parts[dom] / total_single) if dom else None, "by_peril": parts,
            "rp250": (yy["values"][RP_LIST.index(250)] if yy else None)}


def _fmt_m(v) -> str:
    return f"{v / 1e6:,.1f}m" if abs(v) >= 1e6 else f"{v:,.0f}"


def check_results(analyses: list, cols: list, tol: float = 0.005) -> list:
    """Warnings the workbook never raised. -> [(level, message)]."""
    out = []
    # 1 duplicate (layer, peril): MATCH silently used the first
    seen = {}
    for a in analyses:
        seen.setdefault((a["layer"], a["perilCode"]), []).append(a["analysisId"])
    for (layer, peril), ids in seen.items():
        if len(ids) > 1:
            out.append(("warn", f"Layer {layer}, {PERIL_NAMES.get(peril, peril)}: {len(ids)} analyses ({', '.join(ids)}). "
                                f"Only the first is used - untick the others."))
    # 2 one layer spelled two ways
    spell = {}
    for a in analyses:
        spell.setdefault(layer_key(a["layer"]), set()).add(a["layer"])
    for names in spell.values():
        if len(names) > 1:
            out.append(("warn", f"Layer names {', '.join(sorted(names))} look like the same layer - they are split into separate tabs."))
    # 3 ground-up should not depend on the layer
    for ep_type in EP_TYPES:
        for code, _ in PERIL_COLUMNS:
            vals = [c for c in cols if c["perspective"] == "GU" and c["epType"] == ep_type and c["perilCode"] == code]
            if len(vals) > 1:
                ref = vals[0]["values"]
                for other in vals[1:]:
                    if any(r and o is not None and abs(o - r) / abs(r) > tol for r, o in zip(ref, other["values"])):
                        out.append(("warn", f"Ground-up {ep_type} for {PERIL_NAMES.get(code, code)} differs between layers "
                                            f"{vals[0]['layer']} and {other['layer']} (more than {tol:.1%})."))
                        break
    # 4 ground-up loss but no layer curve
    for a in analyses:
        ep = a.get("ep") or {}
        if ep.get("GU") and not ep.get("GR") and a["perilCode"] not in ("", "YY"):
            out.append(("warn", f"Layer {a['layer']}, {PERIL_NAMES.get(a['perilCode'], a['perilCode'])}: RMS returned a ground-up curve "
                                f"but no layer (gross) curve, so this peril shows 0 for the layer. Either the layer takes no loss or "
                                f"the analysis did not finish."))
    for layer in {a["layer"] for a in analyses}:
        yy = find_column(cols, "GR", "AEP", "YY", layer)
        if not yy:
            out.append(("info", f"Layer {layer}: no grouped (multi-peril) analysis, so 'All Perils' losses are blank."))
            continue
        # 5 sum of single perils vs grouped pure premium
        parts = [find_column(cols, "GR", "AEP", p, layer) for p in SINGLE_PERILS]
        parts = [p["purePremium"] for p in parts if p and p["purePremium"] is not None and not p.get("aliasOf")]
        if yy["purePremium"] and parts:
            tot = sum(parts)
            if abs(tot - yy["purePremium"]) / abs(yy["purePremium"]) > 0.05 and abs(tot - yy["purePremium"]) > 500:
                out.append(("warn", f"Layer {layer}: single-peril pure premium adds to {tot:,.0f} but the grouped All Perils analysis says "
                                    f"{yy['purePremium']:,.0f} (more than 5% apart). The tabs show the sum; the grouped value is on the Results tab."))
        # 6 tail: grouped All Perils below one of its own perils
        low = []
        for code in SINGLE_PERILS:
            pc = find_column(cols, "GR", "AEP", code, layer)
            if not pc or pc.get("aliasOf"):
                continue
            for i, rp in enumerate(RP_LIST):
                pv, yv = pc["values"][i], yy["values"][i]
                if rp >= TAIL_RP and pv and pv > 1000 and yv is not None and yv < 0.95 * pv:
                    low.append((rp, PERIL_NAMES.get(code, code), yv, pv))
        if low:
            rp, nm, yv, pv = max(low, key=lambda t: t[0])
            out.append(("warn", f"Layer {layer}: All Perils at {rp:,}-year ({_fmt_m(yv)}) is below {nm} on its own ({_fmt_m(pv)}). "
                                f"Grouped analyses use far fewer simulated years than the single-peril models, so the far tail "
                                f"(1,000-year and above) is thin. Rely on the 250-year and below, or re-run with more simulations."))
    return out
