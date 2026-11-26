"""Deterministic review of a quote - the checklist stored on the Controls tab of the workbook (the brief behind the
'AI Summary' tab), done with plain rules so the same inputs always give the same findings. No AI is used.

Areas: NAMING, UW vs MODEL, TIV, DATA QUALITY, CONCENTRATION, RESULTS.   (Online due diligence is not covered here.)
Levels: red (must fix), amber (needs clarification), good, info.   Deductibles are not validated (pass by policy).
"""
from __future__ import annotations

import re

import pandas as pd

from ..constants import (COUNTY_CONC_WARN, CV_MATERIAL, DUP_COORD_WARN, LARGEST_LOC_WARN, PERIL_NAMES,
                         PP_MATERIAL_SHARE, RP_LIST, TIV_DELTA_ALERT, TIV_DELTA_WARN)
from ..rms.core import to_num
from . import ep as ep_rules
from .hazards import flood_class
from .layers import layer_terms, terms_in_text
from .locations import share_point_stats
from .mailmatch import normalize_exposure_name, normalize_subject
from .rds import annotate

FINE_RES = {"parcel", "building", "street", "address", "property", "rooftop", "point", "lat/long", "latlong"}
SEISMIC = {"CALIFORNIA", "WASHINGTON", "OREGON", "ALASKA", "HAWAII", "NEVADA", "UTAH", "IDAHO", "MONTANA", "WYOMING",
           "MISSOURI", "ARKANSAS", "TENNESSEE", "KENTUCKY", "SOUTH CAROLINA", "ILLINOIS", "OKLAHOMA", "NEW MEXICO"}
LOW = {"inc", "llc", "corp", "co", "company", "the", "and", "of", "lp", "ltd"}


def _f(level: str, area: str, text: str) -> dict:
    return {"level": level, "area": area, "text": text}


def _m(v) -> str:
    return "-" if v is None else f"${v:,.0f}"


def _pct(v, d: int = 1) -> str:
    return "-" if v is None else f"{v * 100:.{d}f}%"


def _toks(s: str) -> list:
    return [t for t in re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).split() if t and t not in LOW]


def _covers(ref: list, text: str) -> float:
    words = set(_toks(text))
    return sum(1 for t in ref if t in words) / len(ref) if ref else 1.0


def _title(s) -> str:
    return " ".join(w[:1].upper() + w[1:].lower() for w in str(s or "").split())


# ---------------------------------------------------------------- snapshot
def _geo(df) -> str:
    if df is None or not len(df) or df["Location TIV"].sum() <= 0:
        return "-"
    g = df.groupby("Admin1")["Location TIV"].sum().sort_values(ascending=False)
    return ", ".join(f"{_title(k)} {_pct(v / g.sum())}" for k, v in g.head(5).items())


def _mix(df, col: str, n: int = 3) -> str:
    if df is None or not len(df) or df["Location TIV"].sum() <= 0:
        return "-"
    g = df.groupby(col)["Location TIV"].sum().sort_values(ascending=False)
    return ", ".join(f"{k} {_pct(v / g.sum())}" for k, v in g.head(n).items())


def snapshot(sub: dict, df, recon: dict | None, layers: list, cols: list) -> list:
    tiv = recon["tiv_modelled"] if recon else None
    rep = recon.get("tiv_reported") if recon else to_num(sub.get("tiv_reported"))
    delta = recon.get("delta") if recon else None
    ov = ep_rules.gu_overview(cols) if cols else {}
    aal, rp250 = ov.get("aal"), ov.get("rp250")
    rows = [
        ("Account", sub.get("account_name") or sub.get("exposure_name") or "-"),
        ("Description", sub.get("comments") or "-"),
        ("Locations", f"{len(df):,}" if df is not None else "Not loaded"),
        ("TIV Reported / Modelled", (f"{_m(rep) if rep else 'not set'} / {_m(tiv)}" + (f" ({delta * 100:+.1f}%)" if delta is not None else ""))
         if tiv is not None else "Not loaded"),
        ("Geography (state, % TIV)", _geo(df)),
        ("Occupancy", _mix(df, "Occupancy Name") if df is not None else "-"),
        ("Construction", _mix(df, "Construction Name") if df is not None else "-"),
        ("Layers", ", ".join(layers) if layers else "-"),
        ("GU AAL", (f"{_m(aal)}" + (f" ({_pct(aal / tiv, 3)} of TIV)" if tiv else "")) if aal is not None else "Pull results"),
        ("Dominant peril", (f"{PERIL_NAMES.get(ov['dominant'], ov['dominant'])} ({_pct(ov['dominant_share'])} of GU AAL)")
         if ov.get("dominant") else "-"),
        ("250-year GU AEP", (f"{_m(rp250)}" + (f" ({_pct(rp250 / tiv, 2)} damage ratio)" if tiv else "")) if rp250 is not None else "Pull results"),
    ]
    return rows


# ---------------------------------------------------------------- checks
def _naming(sub, df, portfolios, records) -> list:
    out = []
    edm = sub.get("exposure_name", "")
    core = normalize_exposure_name(edm)
    ref = _toks(core)
    acct = sub.get("account_name", "")
    if acct and ref and _covers(ref, acct) < 0.8 and _covers(_toks(acct), core) < 0.8:
        out.append(_f("amber", "NAMING", f"Account name '{acct}' does not match the EDM name '{edm}'."))
    bad_p = []
    for p in portfolios or []:
        rest = p["portfolio_name"].split("_", 1)[1] if "_" in p["portfolio_name"] else p["portfolio_name"]
        if ref and _covers(ref, rest) < 0.8 and _covers(_toks(rest), core) < 0.8:
            bad_p.append(p["portfolio_name"])
    if bad_p:
        out.append(_f("amber", "NAMING", f"Portfolio name(s) do not match the EDM: {', '.join(bad_p[:4])}."))
    other = {}
    for r in records or []:
        prefix = r["analysisName"].split(":", 1)[0].strip()
        if ":" in r["analysisName"] and prefix.lower() != edm.lower():
            other.setdefault(prefix, []).append(r["analysisId"])
    for prefix, ids in other.items():
        out.append(_f("amber", "NAMING", f"{len(ids)} analysis(es) ({', '.join(ids[:4])}) are named for a different EDM: '{prefix}'."))
    subj = sub.get("email_subject", "")
    if subj and ref and _covers(ref, normalize_subject(subj)) < 0.8:
        out.append(_f("amber", "NAMING", f"E-mail subject '{subj}' does not match the EDM name."))
    if df is not None and len(df) and ref:
        names = df.groupby("Location Name")["Location TIV"].agg(["sum", "count"]).reset_index()
        off = names[names["Location Name"].map(lambda n: _covers(ref, str(n)) < 0.8)]
        off = off[off["Location Name"].astype(str).str.strip() != "0"]
        if len(off):
            top = ", ".join(f"'{r['Location Name']}'" for _, r in off.sort_values("sum", ascending=False).head(3).iterrows())
            n_loc, t = int(off["count"].sum()), float(off["sum"].sum())
            out.append(_f("amber", "NAMING", f"{n_loc} location(s) / {_m(t)} carry names that do not match the account ({top}). "
                                             f"Confirm they are named insureds."))
    if not out and ref:
        out.append(_f("good", "NAMING", "EDM, portfolio, analysis, account and location names are consistent."))
    return out


def _uw(sub, portfolios, layers) -> list:
    out = []
    comments = sub.get("comments", "")
    asked = terms_in_text(comments)
    modelled = {}
    for ly in layers:
        t = layer_terms(ly)
        if t and t[0] is not None:
            modelled[t] = ly
    if not comments:
        out.append(_f("info", "UW vs MODEL", "No underwriter comments on this quote, so layers and sublimits were not compared."))
    elif asked:
        missing = [t for t in asked if not any(abs(t[0] - m[0]) < 1 and abs(t[1] - m[1]) < 1 for m in modelled)]
        extra = [ly for m, ly in modelled.items() if not any(abs(t[0] - m[0]) < 1 and abs(t[1] - m[1]) < 1 for t in asked)]
        fmt = lambda t: f"{t[0] / 1e6:g}M xs {t[1] / 1e6:g}M"
        if missing and layers:
            out.append(_f("amber", "UW vs MODEL", "The comments ask for layer(s) that are not modelled: " + ", ".join(fmt(t) for t in sorted(missing)) + "."))
        if extra:
            out.append(_f("info", "UW vs MODEL", "Modelled layer(s) not mentioned in the comments: " + ", ".join(extra) + "."))
        if not missing and not extra and layers:
            out.append(_f("good", "UW vs MODEL", "Modelled layers match the layers in the underwriter's comments."))
    out.append(_f("good", "UW vs MODEL", "Deductibles - not validated (pass by policy)."))
    return out


def _tiv(sub, df, recon) -> list:
    out = []
    if df is None or not len(df):
        return out
    delta = recon.get("delta") if recon else None
    if delta is None:
        out.append(_f("info", "TIV", "No reported TIV on the quote, so it was not compared with the modelled TIV."))
    elif abs(delta) > TIV_DELTA_ALERT:
        out.append(_f("red", "TIV", f"Modelled TIV {_m(recon['tiv_modelled'])} is {delta * 100:+.1f}% against the reported {_m(recon['tiv_reported'])}."))
    elif abs(delta) > TIV_DELTA_WARN:
        out.append(_f("amber", "TIV", f"Modelled TIV {_m(recon['tiv_modelled'])} is {delta * 100:+.1f}% against the reported {_m(recon['tiv_reported'])}."))
    else:
        out.append(_f("good", "TIV", f"TIV reconciles: reported {_m(recon['tiv_reported'])}, modelled {_m(recon['tiv_modelled'])} ({delta * 100:+.2f}%)."))
    total = float(df["Location TIV"].sum()) or 1.0
    z = df[(df["Buildings TIV"] <= 0) & (df["Location TIV"] > 0)]
    if len(z):
        out.append(_f("amber", "TIV", f"{len(z)} location(s) / {_m(z['Location TIV'].sum())} ({_pct(z['Location TIV'].sum() / total)}) have $0 building value."))
    c = df[(df["Contents TIV"] > df["Buildings TIV"]) & (df["Contents TIV"] > 0)]
    if len(c):
        out.append(_f("amber", "TIV", f"{len(c)} location(s) have contents greater than building value."))
    zero = df[df["Location TIV"] <= 0]
    if len(zero):
        out.append(_f("amber", "TIV", f"{len(zero)} location(s) have zero total TIV and add nothing to the model."))
    if not len(z) and not len(c) and not len(zero):
        out.append(_f("good", "TIV", "No $0 building values, no contents above building, no zero-TIV locations."))
    return out


def _data(sub, df) -> list:
    out = []
    if df is None or not len(df):
        return out
    n, total = len(df), float(df["Location TIV"].sum()) or 1.0
    res = df["Geocoding Resolution"].astype(str).str.strip()
    coarse = df[~res.str.lower().isin(FINE_RES) & res.ne("0") & res.ne("")]
    if len(coarse):
        brk = ", ".join(f"{v} {k}" for k, v in coarse["Geocoding Resolution"].value_counts().items())
        out.append(_f("amber", "DATA", f"{len(coarse)} location(s) / {_m(coarse['Location TIV'].sum())} ({_pct(coarse['Location TIV'].sum() / total)}) "
                                       f"geocoded below street level ({brk})."))
    else:
        out.append(_f("good", "DATA", "All locations are geocoded to street level or better."))
    unk = df[df["Year Built Band"] == "Unknown"]
    if len(unk):
        lvl = "red" if len(unk) > n / 2 else "amber"
        out.append(_f(lvl, "DATA", f"{len(unk)} of {n} location(s) / {_m(unk['Location TIV'].sum())} have a year built of 0, 9999 or blank."
                                   + (" That is the majority of the schedule." if lvl == "red" else "")))
    else:
        out.append(_f("good", "DATA", "Year built is populated on every location."))
    sp = share_point_stats(df)
    if sp["locations"]:
        lvl = "amber" if sp["tiv"] / total >= DUP_COORD_WARN else "info"
        out.append(_f(lvl, "DATA", f"{sp['locations']} location(s) / {_m(sp['tiv'])} ({_pct(sp['tiv'] / total)}) share a coordinate with another location "
                                   f"({sp['groups']} group(s)). Confirm this is genuine multi-building sites."))
    if df["floodZone"].map(lambda v: v not in (None, "")).any():
        hi = df[df["floodZone"].map(flood_class) == "High risk (A / V zones)"]
        if len(hi):
            out.append(_f("amber", "DATA", f"{len(hi)} location(s) / {_m(hi['Location TIV'].sum())} ({_pct(hi['Location TIV'].sum() / total)}) are in FEMA high-risk flood "
                                           f"zones ({', '.join(sorted(hi['floodZone'].astype(str).unique()))})."))
        else:
            out.append(_f("good", "DATA", "No locations in FEMA high-risk flood zones (A / AE / V / VE)."))
    else:
        out.append(_f("info", "DATA", "Hazards have not been added, so flood zones were not checked."))
    ucon = df[(df["Construction Name"].astype(str).str.upper().isin(["UNKNOWN", "", "0"])) | (df["Construction Code"].astype(str) == "0")]
    uocc = df[(df["Occupancy Name"].astype(str).str.upper().isin(["UNKNOWN", "", "0"])) | (df["Occupancy Code"].astype(str) == "0")]
    if len(ucon):
        out.append(_f("amber", "DATA", f"{len(ucon)} location(s) / {_m(ucon['Location TIV'].sum())} have unknown construction."))
    if len(uocc):
        out.append(_f("amber", "DATA", f"{len(uocc)} location(s) / {_m(uocc['Location TIV'].sum())} have unknown occupancy."))
    if not len(ucon) and not len(uocc):
        out.append(_f("good", "DATA", "Construction and occupancy are populated on every location."))
    return out


def _conc(df) -> list:
    out = []
    if df is None or not len(df) or df["Location TIV"].sum() <= 0:
        return out
    total = float(df["Location TIV"].sum())
    big = df.loc[df["Location TIV"].idxmax()]
    share = float(big["Location TIV"]) / total
    txt = f"Largest location is {_m(big['Location TIV'])} ({_pct(share, 2)} of TIV) at {big['Street Address']}, {big['City']}."
    out.append(_f("amber" if share >= LARGEST_LOC_WARN else "good", "CONCENTRATION", txt))
    cty = df.groupby(["Admin1", "Admin2"])["Location TIV"].sum().sort_values(ascending=False)
    if len(cty):
        (a1, a2), v = cty.index[0], float(cty.iloc[0])
        out.append(_f("amber" if v / total >= COUNTY_CONC_WARN else "good", "CONCENTRATION",
                      f"{_title(a2)}, {_title(a1)} holds {_m(v)} ({_pct(v / total)}) of TIV."))
    return out


def _results(sub, df, records, cols, rds_df, event_perils, result_checks) -> list:
    out = []
    if not records:
        out.append(_f("info", "RESULTS", "No results pulled yet."))
        return out
    comments = (sub.get("comments") or "").lower()
    ov = ep_rules.gu_overview(cols)
    dom = ov.get("dominant")
    if dom:
        text = f"Dominant peril is {PERIL_NAMES.get(dom, dom)} at {_pct(ov['dominant_share'])} of ground-up AAL."
        lvl = "info"
        if df is not None and len(df) and df["Location TIV"].sum() > 0:
            tot = float(df["Location TIV"].sum())
            if dom == "WS":
                inw = float(df[df["Wind Tier"].isin(["T1", "T2"])]["Location TIV"].sum()) / tot
                if inw < 0.10:
                    lvl, text = "amber", text + f" Only {_pct(inw)} of TIV is in wind-tier counties, so check the windstorm result."
            if dom == "EQ":
                ins = float(df[df["Admin1"].astype(str).str.upper().isin(SEISMIC)]["Location TIV"].sum()) / tot
                if ins < 0.25:
                    lvl, text = "amber", text + f" Only {_pct(ins)} of TIV is in seismic states, so check the earthquake result."
        out.append(_f(lvl, "RESULTS", text))
    layers = ep_rules.layers_in_order(records)
    for ly in layers:
        yy = ep_rules.find_column(cols, "GR", "AEP", "YY", ly)
        if yy and not any(v for v in yy["values"] if v and v > 1):
            out.append(_f("red", "RESULTS", f"Layer {ly} shows $0 at every return period for All Perils."))
    flake = "flake" in comments
    zeros = []
    for ly in layers:
        for code in ("EQ", "FL"):
            c = ep_rules.find_column(cols, "GR", "AEP", code, ly)
            if c is None or not any(v for v in c["values"] if v and v > 1):
                zeros.append((ly, code))
    if flake:
        nonzero = []
        for ly in layers:
            for code in ("EQ", "FL"):
                c = ep_rules.find_column(cols, "GR", "AEP", code, ly)
                if c and c.get("purePremium"):
                    nonzero.append(f"{PERIL_NAMES[code]} {c['purePremium']:,.0f} in {ly}")
        if nonzero:
            out.append(_f("amber", "RESULTS", "The comments mention FLAKE, but the model still shows Flood / EQ loss (pure premium: "
                                              + "; ".join(nonzero[:4]) + "). Confirm whether FLAKE is an exclusion or a sublimit and that the analysis applies it."))
        else:
            out.append(_f("good", "RESULTS", "FLAKE confirmed - Flood & EQ excluded, $0 losses expected."))
    for code, sh in ov.get("by_peril", {}).items():
        pass
    tot = sum(ov.get("by_peril", {}).values()) or 0
    for code, pp in ov.get("by_peril", {}).items():
        c = ep_rules.find_column(cols, "GU", "AEP", code)
        if c and c.get("cv") and c["cv"] > CV_MATERIAL and tot and pp / tot >= PP_MATERIAL_SHARE:
            out.append(_f("amber", "RESULTS", f"{PERIL_NAMES.get(code, code)} has a ground-up CV of {c['cv']:,.0f} on {_pct(pp / tot)} of the AAL. Treat its tail with care."))
    if rds_df is not None and len(rds_df):
        modelled = {r["perilCode"] for r in records if r["perilCode"] not in ("", "YY")}
        ann = annotate(rds_df, event_perils, modelled)
        n_loss, n_not = int((ann["Status"] == "Loss").sum()), int((ann["Status"] == "Peril not modelled").sum())
        if n_loss == 0:
            out.append(_f("amber", "RESULTS", f"None of the {len(ann)} RDS events shows a loss."))
        else:
            out.append(_f("info", "RESULTS", f"RDS: {n_loss} of {len(ann)} events show a loss"
                          + (f"; {n_not} belong to perils that were not modelled." if n_not else ".")))
    for lvl, msg in result_checks or []:
        out.append(_f({"error": "red", "warn": "amber"}.get(lvl, "info"), "RESULTS", msg))
    return out


def run_review(*, sub: dict, locations, recon, portfolios: list, records: list, cols: list, rds_df, event_perils: dict,
               layer_order: list, result_checks: list) -> dict:
    layers = ep_rules.layers_in_order(records, layer_order) if records else [p["layer"] for p in portfolios or []]
    findings = []
    findings += _naming(sub, locations, portfolios, records)
    findings += _uw(sub, portfolios, layers)
    findings += _tiv(sub, locations, recon)
    findings += _data(sub, locations)
    findings += _conc(locations)
    findings += _results(sub, locations, records, cols, rds_df, event_perils, result_checks)
    counts = {k: sum(1 for f in findings if f["level"] == k) for k in ("red", "amber", "good", "info")}
    head = f"{counts['red']} red, {counts['amber']} amber, {counts['good']} passed" if findings else "Nothing to review yet"
    return {"snapshot": snapshot(sub, locations, recon, layers, cols), "findings": findings, "counts": counts, "headline": head}
