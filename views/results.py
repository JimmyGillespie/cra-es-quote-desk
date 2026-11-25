"""Step 5 - EP / RDS results (EP GrUp, layer tabs, RDS Events, Results, Meta)."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from cra.constants import PERIL_COLUMNS, RP_LIST
from cra.rules import ep as ep_rules

from .common import flags, kpis, money, need_account, need_rms, progress_cb, section, uk_dt

LABELS = [l for _c, l in PERIL_COLUMNS]


def _style_ep(df: pd.DataFrame):
    fmt = {c: "{:,.0f}" for c in LABELS}
    fmt["Probability"] = "{:.4f}"
    return (df.style.format(fmt, na_rep="-")
              .apply(lambda r: ["background-color:#C1E4F5;font-weight:600" if r["Return Period"] == 250 else "" for _ in r], axis=1))


def _ep_view(cols: list, persp: str, layer: str | None, key: str):
    ep_type = st.radio("Curve", ["AEP", "OEP"], horizontal=True, key=f"ep_{key}", help="AEP = aggregate, OEP = occurrence exceedance probability")
    t = ep_rules.ep_table(cols, persp, ep_type, layer)
    live = [l for l in LABELS if t[l].notna().any()]
    if not live:
        st.info("No curves for this selection.")
        return
    c1, c2 = st.columns([3, 2])
    c1.dataframe(_style_ep(t), hide_index=True, width="stretch", height=470)
    chart = t.set_index("Return Period")[live].fillna(0)
    chart.index = chart.index.astype(str)
    c2.line_chart(chart.iloc[::-1], height=420)
    st.markdown("**Statistics**")
    stt = ep_rules.stats_table(cols, persp, layer)
    fm = {c: "{:,.0f}" for c in stt.columns[1:]}
    st.dataframe(stt.style.format(fm, na_rep="-").format({c: "{:,.2f}" for c in stt.columns[1:]}, subset=pd.IndexSlice[2, :], na_rep="-"),
                 hide_index=True, width="stretch")


def render():
    p = need_account()
    if not p:
        return
    selected = [a["analysisId"] for a in p.analyses() if a["selected"]]
    section("Pull results from RMS", "Reads EP curves, statistics and RDS event losses for the analyses selected on the previous step, and saves them with the quote.")
    last = p.get("results")
    b = st.columns([1.4, 1.2, 1.2, 4])
    rl = b[1].toggle("Include RL", value=bool((last or {}).get("include_rl", True)), help="Retained loss - on the Results tab, not on the EP tabs")
    rds = b[2].toggle("Include RDS", value=True)
    if b[0].button("Pull results", type="primary", disabled=not selected or not need_rms()):
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
    if not selected:
        st.caption("Select analyses on the previous step.")
    msg = st.session_state.pop("pull_msg", None)
    if msg:
        st.success(f"{msg['records']} analysis(es) saved")
        for e in msg["errors"]:
            st.error(e)
    recs = p.results()
    if not recs:
        return
    if last:
        st.caption(f"Saved {uk_dt(last.get('fetched'))}  |  {len(recs)} analyses  |  {len(p.result_columns())} result columns")
    cols = p.result_columns()
    chk = (p.get("results_check", {}) or {}).get("warnings", [])
    layers = ep_rules.layers_in_order(recs, p.layer_order())

    # a layer with no grouped analysis can show a single peril as All Perils (the Single_Peril macro)
    no_yy = [l for l in layers if not ep_rules.find_column(ep_rules.results_columns(recs), "GR", "AEP", "YY", l)]
    if no_yy:
        with st.expander(f"All Perils for layers with no grouped analysis ({len(no_yy)})"):
            st.caption("If only one peril was run for a layer, tick it to show that peril as 'All Perils' on the layer tab (replaces the Single_Peril macro).")
            alias = p.yy_alias()
            new = set(alias)
            for l in no_yy:
                for a in [r for r in recs if r["layer"] == l]:
                    on = st.checkbox(f"{l}: {a['peril']} ({a['analysisId']})", value=a["analysisId"] in alias, key=f"alias_{p.id}_{a['analysisId']}")
                    (new.add if on else new.discard)(a["analysisId"])
            if new != alias:
                p.set_yy_alias(new)
                st.rerun()

    tabs = st.tabs(["Ground up", "By layer", "RDS events", "Analyses", "Checks" + (f" ({len(chk)})" if chk else "")])
    with tabs[0]:
        _ep_view(cols, "GU", None, "gu")
    with tabs[1]:
        layer = st.selectbox("Layer", layers, key=f"layer_pick_{p.id}")
        if layer:
            _ep_view(cols, "GR", layer, f"gr_{layer}")
    with tabs[2]:
        df = p.rds_frame()
        if df is None:
            st.caption("RDS was not pulled.")
        else:
            fm = {c: "{:,.0f}" for c in df.columns[2:]}
            st.dataframe(df.style.format(fm, na_rep="-"), hide_index=True, width="stretch", height=min(80 + 35 * len(df), 560))
    with tabs[3]:
        st.dataframe(pd.DataFrame([{"Layer": a["layer"], "Peril": a["peril"], "Code": a["perilCode"], "Analysis": a["analysisName"],
                                    "ID": a["analysisId"], "Engine": a["engineVersion"], "Model profile": a["modelProfile"],
                                    "Output profile": a["outputProfile"], "Currency": f"{a['currencyCode']} {a['currencyVintage']}",
                                    "User": a["username"]} for a in recs]), hide_index=True, width="stretch")
    with tabs[4]:
        if chk:
            flags(chk)
        else:
            st.success("No issues found in the results.")
