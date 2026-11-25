"""Step 1 - open a saved quote or start a new one (Controls!A2 EDM dropdown + the quote library)."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from cra.pipeline import AccountPipeline
from cra.rules.naming import folder_name_from_exposure

from .common import ctx, goto, need_rms, section, uk_dt


@st.cache_data(ttl=900, show_spinner="Loading the EDM list from RMS...")
def _exposures(_svc, sig: str) -> list:
    return _svc.list_exposures()


def _stage(parts: dict) -> str:
    for key, label in (("results", "Results pulled"), ("jobs", "Analyses running"), ("hazards", "Hazards added"),
                       ("locations", "Exposure loaded"), ("submission", "Started")):
        if key in parts:
            return label
    return "Started"


def render():
    c = ctx()
    section("Quote library", "Every quote is saved on the shared drive and can be re-opened at any time.")
    accounts = c.store.list_accounts()
    if accounts:
        df = pd.DataFrame([{"Account": a.get("account_name") or a.get("exposure_name"), "EDM": a.get("exposure_name"),
                            "Stage": _stage(a.get("parts", {})), "Last saved": uk_dt(a.get("updated")), "_id": a["account_id"]}
                           for a in accounts])
        ev = st.dataframe(df.drop(columns="_id"), hide_index=True, width="stretch", on_select="rerun",
                          selection_mode="single-row", key="lib_sel", height=min(60 + 35 * len(df), 320))
        sel = ev.selection.rows if ev and ev.selection else []
        cols = st.columns([1, 1, 1, 4])
        if cols[0].button("Open", type="primary", disabled=not sel):
            st.session_state["account_id"] = df.iloc[sel[0]]["_id"]
            goto("exposure")
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
    else:
        st.caption("No saved quotes yet.")

    section("New quote", "Pick the EDM the underwriter's submission was imported to.")
    if not need_rms():
        return
    top = st.columns([6, 1])
    if top[1].button("Refresh list", help="Reload the EDM list from RMS"):
        _exposures.clear()
    try:
        exps = _exposures(c.svc, "demo" if c.demo else c.settings.host)
    except Exception as exc:                                    # noqa: BLE001
        st.error(f"Could not read the EDM list from RMS: {exc}")
        return
    names = [e["exposureName"] for e in exps]
    q = top[0].text_input("Search EDMs", placeholder="Type part of the name, e.g. Noonan", label_visibility="collapsed")
    shown = [n for n in names if q.lower() in n.lower()] if q else names
    pick = st.selectbox(f"EDM ({len(shown):,} of {len(names):,})", shown, index=None, placeholder="Select an EDM")
    if not pick:
        return
    eid = next(e["exposureId"] for e in exps if e["exposureName"] == pick)
    same = [a for a in accounts if a.get("exposure_id") == eid]
    if same:
        st.info(f"A quote for this EDM already exists ({same[0]['account_id']}). Opening it keeps all its saved work.",
                icon=":material/folder_open:")
        if st.button("Open the existing quote", type="primary"):
            st.session_state["account_id"] = same[0]["account_id"]
            goto("exposure")
            st.rerun()
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
