"""Results: the numbers an underwriter needs first, the detail behind tabs, and ONE switch for the perspective.

    Perspective   Ground up (before any terms) | Gross (after the layer's terms) | Retained (after reinsurance, when RMS returned it)
    Layer         for Gross and Retained
    Curve         Annual total (AEP) | Largest single event (OEP)

Everything on the page follows those three choices: the headline numbers, the charts, the tables and the events.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from cra import resview as rv
from cra import viz

from .common import finding_boxes, flags, kpis, need_account, need_rms, progress_cb, section, show_chart, snapshot_table, uk_dt

HELP = ("**Average annual loss** is the loss you expect in a typical year, averaged over thousands of simulated years. "
        "**1 in 250** means a loss that is reached or beaten once in 250 years on average (a 0.4% chance in any one year). "
        "**Ground up** is the full modelled loss before any policy terms; **Gross** applies the layer's limit and what it sits above. "
        "**Annual total (AEP)** adds up every event in a year; **Largest single event (OEP)** looks at the worst single event.")


def _money(v) -> str:
    return viz.fmt_money(v)


def _style_rp(df: pd.DataFrame):
    money_cols = [c for c in df.columns if c not in ("Return period", "Chance each year")]
    sty = df.style.format({c: "${:,.0f}" for c in money_cols}, na_rep="-")
    return sty.apply(lambda r: ["background-color:#C1E4F5;font-weight:600" if r["Return period"] == "1 in 250" else "" for _ in r], axis=1)


# ---------------------------------------------------------------- pulling from RMS
def _pull_controls(p, recs) -> None:
    last = p.get("results")
    selected = [a["analysisId"] for a in p.analyses() if a.get("selected")]
    with st.expander("Pull or refresh the results from RMS", expanded=not recs):
        if last:
            st.caption(f"Saved {uk_dt(last.get('fetched'))}  ·  {len(recs)} analyses")
        if not selected:
            st.caption("No analyses are selected yet. Use 'Find results in RMS' on the Account page, or run them on the Analyses page.")
        c = st.columns([1.4, 1.2, 1.2, 4])
        rl = c[1].toggle("Include retained", value=bool((last or {}).get("include_rl", True)), help="Retained loss after reinsurance, if RMS returns it")
        rds = c[2].toggle("Include events", value=True, help="The scenario (RDS) event losses")
        if c[0].button("Pull again" if last else "Pull results", type="secondary" if last else "primary", disabled=not selected or not need_rms(), key="res_pull"):
            cb, done = progress_cb()
            try:
                out = p.pull_results(selected, include_rl=rl, progress=cb, with_rds=rds)
            except Exception as exc:                                   # noqa: BLE001
                done()
                st.error(f"Pull failed - previously saved results are untouched: {exc}")
            else:
                done()
                st.session_state["pull_msg"] = out
                st.rerun()
    msg = st.session_state.pop("pull_msg", None)
    if msg:
        st.success(f"{msg['records']} analysis(es) saved")
        for e in msg["errors"]:
            st.error(e)


# ---------------------------------------------------------------- the three choices
def _controls(p, cols, layers) -> tuple:
    persps = rv.available_perspectives(cols)
    keys = [k for k, _l, _h in persps]
    names = {k: l for k, l, _h in persps}
    helps = {k: h for k, _l, h in persps}
    kp = f"res_persp_{p.id}"
    if st.session_state.get(kp) not in keys:
        st.session_state[kp] = keys[0]
    c = st.columns([3.2, 3.4, 3.2])
    with c[0]:
        persp = st.segmented_control("Perspective", keys, key=kp, format_func=names.get) or keys[0]
    kl = f"res_layer_{p.id}"
    if st.session_state.get(kl) not in layers:
        st.session_state[kl] = layers[0] if layers else None
    with c[1]:
        if persp == "GU" or not layers:
            st.segmented_control("Layer", ["Same for every layer"], key=f"res_layer_off_{p.id}", disabled=True, default="Same for every layer")
            layer = None
        elif len(layers) <= 5:
            layer = st.segmented_control("Layer", layers, key=kl) or layers[0]
        else:
            layer = st.selectbox("Layer", layers, key=kl)
    kc = f"res_curve_{p.id}"
    if st.session_state.get(kc) not in ("AEP", "OEP"):
        st.session_state[kc] = "AEP"
    curve_names = {k: l for k, l, _h in rv.CURVES}
    with c[2]:
        ep = st.segmented_control("Curve", ["AEP", "OEP"], key=kc, format_func=curve_names.get) or "AEP"
    chelp = {k: h for k, _l, h in rv.CURVES}[ep]
    st.caption(f"**{names[persp]}**: {helps[persp]}   ·   **{curve_names[ep]}**: {chelp}")
    return persp, layer, ep


def _title(persp: str, layer, ep: str) -> str:
    who = "Ground-up" if persp == "GU" else ("Gross" if persp == "GR" else "Retained")
    return f"{who}{'' if persp == 'GU' or not layer else ' - ' + layer}, {'annual total' if ep == 'AEP' else 'largest single event'}"


# ---------------------------------------------------------------- tabs
def _summary(cols, persp, layer, ep, tiv) -> None:
    h = rv.headline(cols, persp, ep, layer, tiv)
    lim = rv.layer_limit(layer) if persp != "GU" and layer else None

    def sub(v, pct_tiv):
        if persp == "GU":
            return f"{pct_tiv * 100:.2f}% of TIV" if pct_tiv is not None else None
        return f"{v / lim * 100:.1f}% of the layer limit" if (lim and v is not None) else None
    dom = h["dominant"]
    kpis([("Average annual loss", _money(h["aal"]) if h["aal"] is not None else "-",
           (f"{h['aal'] / lim * 100:.2f}% of the layer limit" if (lim and h["aal"]) else (f"{h['aal_pct_tiv'] * 100:.3f}% of TIV" if h["aal_pct_tiv"] is not None else None))),
          ("1 in 100 year", _money(h["rp"][100]) if h["rp"][100] is not None else "-", sub(h["rp"][100], h["rp_pct_tiv"][100])),
          ("1 in 250 year", _money(h["rp"][250]) if h["rp"][250] is not None else "-", sub(h["rp"][250], h["rp_pct_tiv"][250])),
          ("1 in 500 year", _money(h["rp"][500]) if h["rp"][500] is not None else "-", sub(h["rp"][500], h["rp_pct_tiv"][500])),
          ("Biggest peril", rv.NAME.get(dom, "-") if dom else "-", f"{h['dominant_share'] * 100:.0f}% of the average annual loss" if dom else None)])
    st.write("")
    if not h["has_grouped"]:
        st.info("There is no grouped (All Perils) analysis for this view, so the all-perils return-period losses are not shown - exceedance losses cannot be added "
                "across perils. Pick a single peril on the Loss curves tab, or group the perils on the Analyses page.", icon=":material/info:")
    left, right = st.columns([5, 4])
    with left:
        if h["has_grouped"]:
            show_chart(viz.ladder([(rv.rp_label(r), h["rp"][r]) for r in rv.KEY_RPS], f"How big is the loss? ({_title(persp, layer, ep)})"))
    with right:
        split = rv.peril_split(cols, persp, layer)
        if len(split):
            show_chart(viz.peril_bars(split, "Where the average annual loss comes from"))
        if h["aal_source"] and h["aal"] is not None:
            st.caption(f"The average annual loss is taken from {h['aal_source']}.")
    st.markdown("**Loss at the key return periods**")
    t = rv.rp_table(cols, persp, ep, layer)
    st.dataframe(_style_rp(t), hide_index=True, width="stretch", height=40 + 35 * len(t))
    st.download_button("Download this table (CSV)", rv.csv_bytes(t), file_name=f"loss_by_return_period_{persp}{'_' + layer if layer else ''}_{ep}.csv", mime="text/csv", key="dl_rp")


def _curves(cols, persp, layer, ep) -> None:
    codes_all = rv.perils_with_loss(cols, persp, ep, layer)
    if not codes_all:
        st.info("No curves for this selection.")
        return
    c = st.columns([4, 1.4, 1.6])
    pick = c[0].multiselect("Perils", codes_all, default=codes_all, format_func=lambda k: rv.NAME.get(k, k), key=f"res_perils_{persp}_{layer}_{ep}")
    logy = c[1].toggle("Log loss axis", help="Shows small and large losses on one chart", key="res_log")
    allrp = c[2].toggle("All 12 return periods", help="The table shows the key return periods unless this is on", key="res_allrp")
    pick = pick or codes_all
    show_chart(viz.ep_curve(rv.ep_points(cols, persp, ep, layer, pick), _title(persp, layer, ep), y_log=logy))
    t = rv.rp_table(cols, persp, ep, layer, rps=rv.RP_LIST[::-1] if allrp else None, codes=pick)
    st.dataframe(_style_rp(t), hide_index=True, width="stretch", height=min(40 + 35 * len(t), 480))
    st.download_button("Download this table (CSV)", rv.csv_bytes(t), file_name=f"ep_{persp}{'_' + layer if layer else ''}_{ep}.csv", mime="text/csv", key="dl_ep")
    stt = rv.aal_parts(cols, persp, layer)
    if stt["total"] is not None:
        st.caption(f"Average annual loss {_money(stt['total'])}" + (f"  ·  coefficient of variation {stt['cv']:.2f}" if stt.get("cv") else "")
                   + (f"  ·  standard deviation {_money(stt['std_dev'])}" if stt.get("std_dev") else ""))


def _layers(cols, persp, ep, layers) -> None:
    if persp == "GU":
        st.info("Ground-up loss is the same whichever layer you look at, so there is nothing to compare here. Switch the perspective to **Gross** to compare the layers.",
                icon=":material/layers:")
        return
    if len(layers) < 2:
        st.info("There is only one layer in this account.")
    curves = rv.layer_curves(cols, persp, ep, layers)
    if curves.empty:
        st.info("There is no grouped (All Perils) analysis to compare.")
    else:
        show_chart(viz.layer_compare(curves, f"All Perils by layer, {'annual total' if ep == 'AEP' else 'largest single event'}"))
    t = rv.layer_table(cols, persp, ep, layers)
    st.dataframe(t.style.format({c: "${:,.0f}" for c in t.columns[1:]}, na_rep="-"), hide_index=True, width="stretch")
    st.caption("Average annual loss is the grouped (All Perils) analysis; the other columns are the loss at each return period.")


def _events(p, recs, persp, layer) -> None:
    df = p.rds_frame()
    if df is None or not len(df):
        st.caption("Event (RDS) losses were not pulled. Turn on 'Include events' under 'Pull or refresh' and pull again.")
        return
    if persp == "RL":
        st.info("Event losses are available ground up and gross only.", icon=":material/info:")
        return
    col = "GU_Loss" if persp == "GU" else f"{layer}_GR"
    if col not in df.columns:
        st.info(f"There are no event losses for {layer}.")
        return
    try:
        from cra.constants import load_event_perils
        from cra.rules.rds import annotate
        ann = annotate(df, load_event_perils(), {r["perilCode"] for r in recs if r["perilCode"] not in ("", "YY")})
    except Exception:                                                  # noqa: BLE001
        ann = df.assign(Peril="", Status=df[col].map(lambda v: "Loss" if v == v and v else "No loss"))
    n_loss, n_not = int((ann["Status"] == "Loss").sum()), int((ann["Status"] == "Peril not modelled").sum())
    st.caption(f"{n_loss} of {len(ann)} scenario events show a loss" + (f"; {n_not} belong to perils that were not modelled." if n_not else "."))
    hit = ann[pd.to_numeric(ann[col], errors="coerce").fillna(0) > 0]
    if len(hit):
        long = pd.DataFrame({"Event": hit["EventName"].values, "Measure": ("Ground up" if persp == "GU" else f"{layer} (gross)"),
                             "Loss": pd.to_numeric(hit[col]).values})
        show_chart(viz.rds_bars(long, f"Scenario event losses ({'ground up' if persp == 'GU' else 'gross, ' + layer})"))
    show_all = st.toggle("Show events with no loss", value=False, key="res_ev_all")
    view = ann[["EventName", "Peril", "Status", col]].rename(columns={"EventName": "Event", col: "Loss"})
    if not show_all:
        view = view[view["Status"] == "Loss"]
    st.dataframe(view.style.format({"Loss": "${:,.0f}"}, na_rep="-"), hide_index=True, width="stretch", height=min(60 + 35 * len(view), 520))


def _review(p, has_results) -> None:
    try:
        rev = p.review()
    except Exception as exc:                                           # noqa: BLE001
        st.warning(f"Could not read the review: {type(exc).__name__}: {exc}")
        return
    c = rev["counts"]
    kpis([("Red flags", str(c["red"]), "must fix"), ("Amber flags", str(c["amber"]), "need a look"), ("Checks passed", str(c["good"]), None), ("Notes", str(c["info"]), None)])
    st.write("")
    f = rev["findings"]
    finding_boxes(f, ("red", "amber"))
    if not [x for x in f if x["level"] in ("red", "amber")]:
        st.success("No red or amber flags.")
    with st.expander(f"Passed ({c['good']}) and notes ({c['info']})"):
        finding_boxes(f, ("good", "info"))
    with st.expander("Account snapshot", expanded=True):
        snapshot_table(rev["snapshot"])


def _details(p, recs, cols, layers) -> None:
    st.markdown("**Analyses used**")
    st.dataframe(pd.DataFrame([{"Layer": a["layer"], "Peril": a["peril"], "Code": a["perilCode"], "Analysis": a["analysisName"], "ID": a["analysisId"], "Engine": a.get("engineVersion", ""),
                                "Model profile": a.get("modelProfile", ""), "Output profile": a.get("outputProfile", ""),
                                "Currency": f"{a.get('currencyCode', '')} {a.get('currencyVintage', '')}".strip(), "User": a.get("username", "")} for a in recs]),
                 hide_index=True, width="stretch")
    chk = p.result_checks()
    st.markdown("**Checks on the results**")
    if chk:
        flags(chk)
    else:
        st.success("No issues found in the results.")
    no_yy = [l for l in layers if not rv.find_col(cols, "GR", "AEP", "YY", l)]
    if no_yy:
        with st.expander(f"All Perils for layers with no grouped analysis ({len(no_yy)})"):
            st.caption("If only one peril was run for a layer, tick it to show that peril as 'All Perils' on that layer.")
            alias, new = p.yy_alias(), set(p.yy_alias())
            for l in no_yy:
                for a in [r for r in recs if r["layer"] == l]:
                    on = st.checkbox(f"{l}: {a['peril']} ({a['analysisId']})", value=a["analysisId"] in alias, key=f"alias_{p.id}_{a['analysisId']}")
                    (new.add if on else new.discard)(a["analysisId"])
            if new != alias:
                p.set_yy_alias(new)
                st.rerun()


# ---------------------------------------------------------------- the page
def render() -> None:
    p = need_account()
    if not p:
        return
    recs = p.results()
    _pull_controls(p, recs)
    if not recs:
        st.info("There are no results yet. Use 'Find results in RMS' on the Account page, or pull them above once analyses are selected.", icon=":material/info:")
        return
    cols = p.result_columns()
    if not cols:
        st.warning("Results were saved but contain no loss curves.")
        return
    layers = rv.layers_in(cols, p.layer_order())
    try:
        rec = p.exposure_check()
    except Exception:                                                  # noqa: BLE001
        rec = None
    tiv = rec["tiv_modelled"] if rec else None
    with st.expander("How to read these numbers"):
        st.markdown(HELP)
    persp, layer, ep = _controls(p, cols, layers)
    tabs = st.tabs(["Summary", "Loss curves", "Layers", "Events", "Review", "Details"])
    with tabs[0]:
        _summary(cols, persp, layer, ep, tiv)
    with tabs[1]:
        _curves(cols, persp, layer, ep)
    with tabs[2]:
        _layers(cols, persp, ep, layers)
    with tabs[3]:
        _events(p, recs, persp, layer)
    with tabs[4]:
        _review(p, True)
    with tabs[5]:
        _details(p, recs, cols, layers)
