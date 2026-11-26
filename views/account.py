"""Step 1 - find results for an EDM, open a saved quote, or start a new one (Controls!A2 EDM dropdown + quote library)."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from cra.constants import PERIL_NAMES
from cra.pipeline import AccountPipeline
from cra.rules import matching
from cra.rules.naming import folder_name_from_exposure

from .common import all_analyses, ctx, goto, kpis, need_rms, progress_cb, section, uk_day, uk_dt


@st.cache_data(ttl=900, show_spinner="Loading the EDM list from RMS...")
def _exposures(_svc, sig: str) -> list:
    return _svc.list_exposures()


def _stage(parts: dict) -> str:
    for key, label in (("report_built", "Report built"), ("results", "Results pulled"), ("jobs", "Analyses running"),
                       ("hazards", "Hazards added"), ("locations", "Exposure loaded"), ("submission", "Started")):
        if key in parts:
            return label
    return "Started"


def _lookup(c, pick: str, eid: str) -> None:
    with st.spinner("Matching analyses to the EDM..."):
        ports = c.svc.list_portfolios(eid)
        every = all_analyses()
        items = matching.select_for_edm(pick, every, ports)
    st.session_state["lookup"] = {"eid": eid, "edm": pick, "items": items, "ports": ports, "all": every}


def _find_results(c, accounts):
    section("Find results for an EDM", "Pick the EDM and the app finds every analysis RMS holds for it - no need to load locations or hazards first.")
    if not need_rms():
        return
    top = st.columns([6, 1])
    if top[1].button("Refresh", help="Reload the EDM and analysis lists from RMS"):
        _exposures.clear()
        all_analyses(refresh=True)
        st.session_state.pop("lookup", None)
    try:
        exps = _exposures(c.svc, "demo" if c.demo else c.settings.host)
    except Exception as exc:                                    # noqa: BLE001
        st.error(f"Could not read the EDM list from RMS: {exc}")
        return
    names = [e["exposureName"] for e in exps]
    q = top[0].text_input("Search EDMs", placeholder="Type part of the name, e.g. Ben Davis", label_visibility="collapsed")
    shown = [n for n in names if q.lower() in n.lower()] if q else names
    pick = st.selectbox(f"EDM ({len(shown):,} of {len(names):,})", shown, index=None, placeholder="Select an EDM", key="edm_pick")
    if not pick:
        return
    eid = next(e["exposureId"] for e in exps if e["exposureName"] == pick)
    saved = [a for a in accounts if str(a.get("exposure_id")) == str(eid)]
    b = st.columns([1.5, 1.6, 1.6, 4])
    if b[0].button("Find results in RMS", type="primary"):
        try:
            _lookup(c, pick, eid)
        except Exception as exc:                                # noqa: BLE001
            st.error(f"Could not read analyses from RMS: {exc}")
    if saved and b[1].button("Open saved quote"):
        st.session_state["account_id"] = saved[0]["account_id"]
        goto("results" if "results" in saved[0].get("parts", {}) else "exposure")
        st.rerun()
    if saved:
        st.caption(f"A saved quote exists for this EDM (last saved {uk_dt(saved[0].get('updated'))}).")
    lk = st.session_state.get("lookup")
    if not lk or lk["eid"] != eid:
        return
    items = lk["items"]
    sure = [a for a in items if a["match"] in matching.CONFIDENT]
    chosen = [a for a in items if a["selected"]]
    if not sure:
        st.warning("RMS holds no analyses that are clearly named for this EDM." + (f" {len(items)} loose name match(es) were found - see below." if items else ""))
    else:
        latest = max(a.get("createDate", "") for a in sure)
        kpis([("Analyses found", f"{len(sure)}", "named for this EDM"), ("In the latest run", f"{len(chosen)}", "newest per layer and peril"),
              ("Layers", f"{len({a['layer'] for a in chosen})}", ", ".join(dict.fromkeys(a['layer'] for a in chosen))),
              ("Latest run", uk_day(latest), "")])
        st.write("")
        cov = matching.coverage(items)
        if len(cov):
            st.markdown("**Layer x peril coverage** (analyses ticked by default)")
            st.dataframe(cov.fillna(""), hide_index=True, width="stretch", height=min(40 + 35 * len(cov), 240))
        old = [a for a in sure if a["superseded"]]
        if old:
            st.caption(f"{len(old)} older analysis(es) for the same layer and peril were set aside (newest wins). You can change this on the Analyses step.")
        loose = [a for a in items if a["match"] not in matching.CONFIDENT]
        if loose:
            st.warning(f"{len(loose)} analysis(es) matched by name only and are NOT included: "
                       + ", ".join(f"{a['analysisName'][:50]}" for a in loose[:3]) + (" ..." if len(loose) > 3 else ""))
        with st.expander(f"All analyses found ({len(items)})"):
            st.dataframe(pd.DataFrame([{"Use": a["selected"], "Layer": a["layer"], "Peril": PERIL_NAMES.get(a["perilCode"], a["peril"]),
                                        "Created": uk_day(a.get("createDate")), "Analysis ID": a["analysisId"], "Match": a["match"],
                                        "Superseded": "yes" if a["superseded"] else ""} for a in items]),
                         hide_index=True, width="stretch")
        if st.button("Pull results and open them", type="primary", disabled=not chosen,
                     help="Reads the EP curves, statistics and RDS events for the ticked analyses and opens the Results page."):
            cb, done = progress_cb()
            try:
                p = AccountPipeline.open_results(c.svc, c.store, pick, eid, lk["all"], cb, account_name=folder_name_from_exposure(pick))
            except Exception as exc:                            # noqa: BLE001
                done()
                st.error(f"Could not pull the results: {exc}")
            else:
                done()
                st.session_state["account_id"] = p.id
                st.session_state.pop("lookup", None)
                goto("results")
                st.rerun()


def _new_quote(c, accounts):
    with st.expander("Start a new quote with details (underwriter, reported TIV, terms)"):
        if c.svc is None:
            st.caption("RMS is not connected.")
            return
        names = [e["exposureName"] for e in _exposures(c.svc, "demo" if c.demo else c.settings.host)]
        pick = st.session_state.get("edm_pick") or st.selectbox("EDM", names, index=None, placeholder="Select an EDM", key="edm_pick_new")
        if not pick:
            st.caption("Select an EDM above first.")
            return
        eid = next(e["exposureId"] for e in _exposures(c.svc, "demo" if c.demo else c.settings.host) if e["exposureName"] == pick)
        if any(str(a.get("exposure_id")) == str(eid) for a in accounts):
            st.info("A quote for this EDM already exists - open it instead; all its saved work is kept.")
            return
        with st.form("new_quote"):
            f1, f2 = st.columns(2)
            acct = f1.text_input("Account name", value=folder_name_from_exposure(pick))
            uw = f2.text_input("Underwriter (name or e-mail)")
            f3, f4 = st.columns(2)
            prio = f3.selectbox("Priority", ["", "High", "Normal", "Low"])
            tiv = f4.number_input("TIV reported by the underwriter", min_value=0.0, step=100000.0, format="%.0f")
            terms = st.text_area("Quote terms / comments", height=80)
            if st.form_submit_button("Create quote", type="primary"):
                try:
                    p = AccountPipeline.create(c.svc, c.store, pick, eid, account_name=acct, underwriter=uw, priority=prio,
                                               tiv_reported=tiv or None, comments=terms)
                except OSError as exc:
                    st.error(f"The quote could not be saved, so it was not created: {exc}")
                    return
                st.session_state["account_id"] = p.id
                goto("exposure")
                st.rerun()


def _library(c, accounts):
    section("Saved quotes", "Every quote is saved on the shared drive and can be re-opened at any time.")
    if not accounts:
        st.caption("No saved quotes yet.")
        return
    df = pd.DataFrame([{"Account": a.get("account_name") or a.get("exposure_name"), "EDM": a.get("exposure_name"),
                        "Stage": _stage(a.get("parts", {})), "Last saved": uk_dt(a.get("updated")), "_id": a["account_id"],
                        "_res": "results" in a.get("parts", {})} for a in accounts])
    ev = st.dataframe(df.drop(columns=["_id", "_res"]), hide_index=True, width="stretch", on_select="rerun",
                      selection_mode="single-row", key="lib_sel", height=min(40 + 35 * len(df), 320))
    sel = ev.selection.rows if ev and ev.selection else []
    cols = st.columns([1, 1, 1, 4])
    if cols[0].button("Open", type="primary", disabled=not sel):
        st.session_state["account_id"] = df.iloc[sel[0]]["_id"]
        goto("results" if df.iloc[sel[0]]["_res"] else "exposure")
        st.rerun()
    if cols[1].button("Check files", disabled=not sel, help="Verifies every saved file against its checksum."):
        rep = c.store.check_account(df.iloc[sel[0]]["_id"])
        (st.success if not rep["damaged"] else st.error)(
            f"{len(rep['ok'])} file(s) verified" + (f"; damaged: {', '.join(rep['damaged'])}" if rep["damaged"] else ""))
    if cols[2].button("Delete", disabled=not sel):
        st.session_state["confirm_delete"] = df.iloc[sel[0]]["_id"]
    if st.session_state.get("confirm_delete"):
        st.warning(f"Delete {st.session_state['confirm_delete']} and all its files? This cannot be undone.")
        y, n = st.columns([1, 6])
        if y.button("Yes, delete"):
            c.store.delete_account(st.session_state.pop("confirm_delete"))
            st.rerun()
        if n.button("Cancel"):
            st.session_state.pop("confirm_delete")
            st.rerun()


def render():
    c = ctx()
    accounts = c.store.list_accounts()
    _find_results(c, accounts)
    _library(c, accounts)
    _new_quote(c, accounts)
