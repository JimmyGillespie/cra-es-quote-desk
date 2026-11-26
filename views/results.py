"""Step 5 - EP / RDS results, charts and the review (EP GrUp, layer tabs, RDS Events, Results, Meta, AI Summary)."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from cra import charts
from cra.constants import PERIL_COLUMNS, PERIL_NAMES, RP_LIST, load_event_perils
from cra.rules import ep as ep_rules
from cra.rules import matching
from cra.rules.rds import annotate

from .common import (finding_boxes, flags, kpis, money, need_account, need_rms, progress_cb, section, show_chart, snapshot_table, uk_dt)

LABELS = [l for _c, l in PERIL_COLUMNS]


def _style_ep(df: pd.DataFrame):
    fmt = {c: "{:,.0f}" for c in LABELS}
    fmt["Probability"] = "{:.4f}"
    return (df.style.format(fmt, na_rep="-")
              .apply(lambda r: ["background-color:#C1E4F5;font-weight:600" if r["Return Period"] == 250 else "" for _ in r], axis=1))


def _ep_view(cols: list, key: str, layers: list):
    c = st.columns([1.4, 2.4, 1.2, 1.4, 3])
    view = c[0].radio("View", ["Ground up", "Layer"], horizontal=True, key=f"view_{key}")
    layer = c[1].selectbox("Layer", layers, key=f"layer_{key}", disabled=view != "Layer", label_visibility="visible") if layers else None
    ep_type = c[2].radio("Curve", ["AEP", "OEP"], horizontal=True, key=f"ep_{key}", help="AEP = annual aggregate; OEP = largest single event in a year")
    log = c[3].toggle("Log loss axis", key=f"log_{key}", help="Shows small and large losses on one chart")
    persp, lay = ("GU", None) if view == "Ground up" else ("GR", layer)
    long = charts.ep_long(cols, persp, ep_type, lay)
    if long.empty:
        st.info("No curves for this selection.")
        return
    title = f"{'Ground-up' if persp == 'GU' else lay + ' layer'} {ep_type} by peril"
    show_chart(charts.ep_chart(long, title, y_log=log))
    t = ep_rules.ep_table(cols, persp, ep_type, lay)
    left, right = st.columns([3, 2])
    with left:
        st.markdown("**Return-period table**")
        st.dataframe(_style_ep(t), hide_index=True, width="stretch", height=470)
    with right:
        stt = ep_rules.stats_table(cols, persp, lay)
        pp = charts.pp_frame(stt)
        if len(pp):
            show_chart(charts.pp_chart(pp, "Annual average loss by peril"))
        st.markdown("**Statistics**")
        fm = {x: "{:,.0f}" for x in stt.columns[1:]}
        st.dataframe(stt.style.format(fm, na_rep="-").format({x: "{:,.2f}" for x in stt.columns[1:]}, subset=pd.IndexSlice[2, :], na_rep="-"),
                     hide_index=True, width="stretch")


def _layer_table(cols: list, layers: list) -> pd.DataFrame:
    rows = []
    for ly in [None] + layers:
        persp = "GU" if ly is None else "GR"
        yy = ep_rules.find_column(cols, persp, "AEP", "YY", ly)
        st_ = ep_rules.stats_table(cols, persp, ly)
        row = {"Layer": "Ground up" if ly is None else ly, "AAL, grouped": yy["purePremium"] if yy else None,
               "AAL, sum of perils": st_.iloc[0]["All Perils"]}
        for rp in (100, 250, 500, 1000):
            row[f"{rp}-yr AEP"] = yy["values"][RP_LIST.index(rp)] if yy else None
        rows.append(row)
    return pd.DataFrame(rows)


def _overview(p, recs, cols, layers):
    rev = p.review()
    ov = ep_rules.gu_overview(cols)
    rec = p.exposure_check()
    tiv = rec["tiv_modelled"] if rec else None
    c = rev["counts"]
    kpis([("GU annual average loss", charts.fmt_money(ov["aal"]) if ov["aal"] is not None else "-", f"{ov['aal'] / tiv * 100:.3f}% of TIV" if ov["aal"] and tiv else "ground up, all perils"),
          ("250-year GU AEP", charts.fmt_money(ov["rp250"]) if ov["rp250"] is not None else "-", f"{ov['rp250'] / tiv * 100:.1f}% damage ratio" if ov["rp250"] and tiv else ""),
          ("Dominant peril", PERIL_NAMES.get(ov["dominant"], "-"), f"{ov['dominant_share']:.0%} of ground-up AAL" if ov["dominant"] else ""),
          ("TIV modelled", charts.fmt_money(tiv) if tiv else "not loaded", f"{rec['locations']:,} locations" if rec else "load the exposure"),
          ("Review", f"{c['red']} red · {c['amber']} amber", f"{c['good']} checks passed")])
    st.write("")
    left, right = st.columns([5, 4])
    with left:
        st.markdown("**Account snapshot**")
        snapshot_table(rev["snapshot"])
    with right:
        pp = charts.pp_frame(ep_rules.stats_table(cols, "GU", None))
        if len(pp):
            show_chart(charts.pp_chart(pp, "Where the ground-up loss comes from"))
    section("Review", "Rule-based checks from your review checklist (naming, UW vs model, TIV, data quality, concentration, results). No AI.")
    f = rev["findings"]
    finding_boxes(f, ("red", "amber"))
    if not [x for x in f if x["level"] in ("red", "amber")]:
        st.success("No red or amber flags.")
    with st.expander(f"Passed ({c['good']}) and notes ({c['info']})"):
        finding_boxes(f, ("good", "info"))
    cov = matching.coverage([{"layer": r["layer"], "perilCode": r["perilCode"], "selected": True} for r in recs])
    if len(cov):
        st.markdown("**Layer x peril coverage of these results**")
        st.dataframe(cov.fillna(""), hide_index=True, width="stretch", height=min(40 + 35 * len(cov), 240))


def _layers_tab(cols: list, layers: list):
    if not layers:
        st.info("No layer results yet.")
        return
    ep_type = st.radio("Curve", ["AEP", "OEP"], horizontal=True, key="cmp_ep")
    long = charts.compare_long(cols, ep_type, "GR", layers)
    if long.empty:
        st.info("There is no grouped (All Perils) analysis to compare.")
    else:
        show_chart(charts.compare_chart(long, f"All Perils {ep_type} by layer"))
    st.markdown("**Layers side by side**")
    t = _layer_table(cols, layers)
    fm = {x: "{:,.0f}" for x in t.columns[1:]}
    st.dataframe(t.style.format(fm, na_rep="-"), hide_index=True, width="stretch")
    st.caption("AAL = annual average loss (pure premium). 'Grouped' is the multi-peril analysis; 'sum of perils' adds the single-peril analyses, as the workbook tabs do.")


def _rds_tab(p, recs):
    df = p.rds_frame()
    if df is None:
        st.caption("RDS was not pulled.")
        return
    modelled = {r["perilCode"] for r in recs if r["perilCode"] not in ("", "YY")}
    ann = annotate(df, load_event_perils(), modelled)
    n_loss, n_not = int((ann["Status"] == "Loss").sum()), int((ann["Status"] == "Peril not modelled").sum())
    st.caption(f"{n_loss} of {len(ann)} events show a loss" + (f"; {n_not} belong to perils that were not modelled." if n_not else "."))
    long = charts.rds_long(df)
    if len(long):
        show_chart(charts.rds_chart(long, "Realistic disaster scenario losses"))
    fm = {x: "{:,.0f}" for x in ann.columns if x == "GU_Loss" or x.endswith("_GR")}
    st.dataframe(ann.style.format(fm, na_rep="-"), hide_index=True, width="stretch", height=min(60 + 35 * len(ann), 560))


def render():
    p = need_account()
    if not p:
        return
    selected = [a["analysisId"] for a in p.analyses() if a["selected"]]
    section("Pull results from RMS", "Reads EP curves, statistics and RDS event losses for the analyses selected on the Analyses step (or found from the Account step) and saves them with the quote.")
    last = p.get("results")
    b = st.columns([1.4, 1.2, 1.2, 4])
    rl = b[1].toggle("Include RL", value=bool((last or {}).get("include_rl", True)), help="Retained loss - on the Results tab, not on the EP tabs")
    rds = b[2].toggle("Include RDS", value=True)
    if b[0].button("Pull results" if not last else "Pull again", type="primary" if not last else "secondary", disabled=not selected or not need_rms()):
        cb, done = progress_cb()
        try:
            out = p.pull_results(selected, include_rl=rl, progress=cb, with_rds=rds)
        except Exception as exc:                                # noqa: BLE001
            done()
            st.error(f"Pull failed - previously saved results are untouched: {exc}")
        else:
            done()
            st.session_state["pull_msg"] = out
            st.rerun()
    if not selected and not last:
        st.caption("Select analyses on the Analyses step, or use 'Find results for an EDM' on the Account step.")
    msg = st.session_state.pop("pull_msg", None)
    if msg:
        st.success(f"{msg['records']} analysis(es) saved")
        for e in msg["errors"]:
            st.error(e)
    recs = p.results()
    if not recs:
        return
    if last:
        t = p.timings()
        st.caption(f"Saved {uk_dt(last.get('fetched'))}  |  {len(recs)} analyses  |  {len(p.result_columns())} result columns"
                   + (f"  |  pulled in {t['results']:.0f}s" if t.get("results") else ""))
    cols = p.result_columns()
    layers = ep_rules.layers_in_order(recs, p.layer_order())
    chk = p.result_checks()
    no_yy = [l for l in layers if not ep_rules.find_column(ep_rules.results_columns(recs), "GR", "AEP", "YY", l)]
    if no_yy:
        with st.expander(f"All Perils for layers with no grouped analysis ({len(no_yy)})"):
            st.caption("If only one peril was run for a layer, tick it to show that peril as 'All Perils' on the layer tab (replaces the Single_Peril macro).")
            alias, new = p.yy_alias(), set(p.yy_alias())
            for l in no_yy:
                for a in [r for r in recs if r["layer"] == l]:
                    on = st.checkbox(f"{l}: {a['peril']} ({a['analysisId']})", value=a["analysisId"] in alias, key=f"alias_{p.id}_{a['analysisId']}")
                    (new.add if on else new.discard)(a["analysisId"])
            if new != alias:
                p.set_yy_alias(new)
                st.rerun()
    tabs = st.tabs(["Overview", "EP curves", "Layers", "RDS events", "Analyses", "Checks" + (f" ({len(chk)})" if chk else "")])
    with tabs[0]:
        _overview(p, recs, cols, layers)
    with tabs[1]:
        _ep_view(cols, f"ep_{p.id}", layers)
    with tabs[2]:
        _layers_tab(cols, layers)
    with tabs[3]:
        _rds_tab(p, recs)
    with tabs[4]:
        st.dataframe(pd.DataFrame([{"Layer": a["layer"], "Peril": a["peril"], "Code": a["perilCode"], "Analysis": a["analysisName"], "ID": a["analysisId"],
                                    "Engine": a["engineVersion"], "Model profile": a["modelProfile"], "Output profile": a["outputProfile"],
                                    "Currency": f"{a['currencyCode']} {a['currencyVintage']}", "User": a["username"]} for a in recs]),
                     hide_index=True, width="stretch")
    with tabs[5]:
        if chk:
            flags(chk)
        else:
            st.success("No issues found in the results.")
