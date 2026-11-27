"""Exposure: layers, locations and the exposure summary (Locations, Exposure Summary, Map). The underwriter's details now live on the
Account page; the Microsoft sign-in and e-mail search helpers stay here because the Account and Report pages use them."""
from __future__ import annotations

import os
import time

import pandas as pd
import streamlit as st

from cra import charts, graph
from cra.rules import locations as loc_rules

from .common import ctx, flags, kpis, money, need_account, need_rms, pct, progress_cb, section, show_chart


def _token():
    ss = st.session_state
    t = ss.get("ms_tok")
    if t and t.get("expires_at", 0) > time.time():
        return t["access_token"], ""
    if os.environ.get("MSGRAPH_TOKEN"):
        return os.environ["MSGRAPH_TOKEN"], ""
    try:
        sess = st.context.headers.get("Posit-Connect-User-Session-Token") or ""
    except Exception:                                           # noqa: BLE001
        sess = ""
    return graph.connect_token(sess) if sess else ("", "no token")


def _device_signin():
    tenant, client = os.environ.get("MS_TENANT_ID", ""), os.environ.get("MS_CLIENT_ID", "")
    if not (tenant and client):
        st.caption("Set MS_TENANT_ID and MS_CLIENT_ID to enable sign-in from this page.")
        return
    ss = st.session_state
    if st.button("Sign in to Microsoft"):
        dev, err = graph.device_start(tenant, client)
        if dev:
            ss["ms_dev"] = dev
        else:
            st.error(err)
    dev = ss.get("ms_dev")
    if dev:
        st.info(f"Go to {dev.get('verification_uri')} and enter **{dev.get('user_code')}**, then press the button below.")
        if st.button("I have signed in"):
            tok, state = graph.device_poll(tenant, client, dev)
            if tok:
                ss["ms_tok"] = tok
                ss.pop("ms_dev", None)
                st.rerun()
            st.warning("Still waiting for sign-in." if state == "pending" else state)


def _email_finder(p, sub):
    c = ctx()
    with st.expander("Find the underwriter's submission e-mail"):
        tok, err = _token()
        if not tok:
            st.caption(f"Not signed in to Microsoft ({err}). Fill the fields above by hand, or sign in below.")
            _device_signin()
            return
        if st.button("Search the shared mailbox", help=f"Sent Items of {c.settings.mailbox}, last 365 days"):
            with st.spinner("Searching Sent Items..."):
                try:
                    best = graph.find_submission(tok, c.settings.mailbox, sub["exposure_name"])
                except graph.GraphError as exc:
                    st.error(str(exc))
                    return
            if not best:
                st.warning("No e-mail matched this EDM name.")
                return
            m = best["message"]
            sub.update({"email_id": m["id"], "email_subject": m["subject"], "email_to": m.get("to", []),
                        "underwriter": best["underwriter"] or sub.get("underwriter", ""), "priority": best["priority"] or sub.get("priority", ""),
                        "comments": best["comments"] or sub.get("comments", "")})
            try:
                sub["tiv_reported"] = float(str(best["tiv"]).replace(",", "").replace("$", "")) if best["tiv"] else sub.get("tiv_reported")
            except ValueError:
                pass
            p.put("submission", sub)
            st.success(f"Matched: {m['subject']}  (score {best['score']})")
            st.rerun()
    if sub.get("email_subject"):
        st.caption(f"Linked e-mail: {sub['email_subject']}")


def _county_frame(summ: pd.DataFrame) -> pd.DataFrame:
    d = summ.copy()
    d["County"] = [f"{str(b).title()}, {str(a).title()}" for a, b in zip(d["Admin1"], d["Admin2"])]
    return d[["County", "Loc Count", "Sum of Location TIV"]]


def exposure_charts(df: pd.DataFrame) -> None:
    sums = loc_rules.exposure_summaries(df)
    pairs = [("Where the value sits (top counties)", _county_frame(sums["State / County"]), "County", None, 10),
             ("Construction", sums["Construction"], "Construction Name", None, 8),
             ("Occupancy", sums["Occupancy"], "Occupancy Name", None, 8),
             ("Year built", sums["Year Built Band"], "Year Built Band", loc_rules.YEAR_ORDER, None),
             ("Number of stories", sums["Stories Band"], "Stories Band", loc_rules.STORIES_ORDER, None),
             ("Geocoding resolution", sums["Geocoding Resolution"], "Geocoding Resolution", None, None),
             ("Wind tier", sums["Wind Tier"], "Wind Tier", loc_rules.WIND_ORDER, None)]
    for i in range(0, len(pairs), 2):
        row = st.columns(2)
        for col, (title, d, cat, order, top) in zip(row, pairs[i:i + 2]):
            with col:
                st.markdown(f"**{title}**")
                show_chart(charts.tiv_bar(d, cat, order=order, top=top))
    top = df.sort_values("Location TIV", ascending=False).head(10)
    tot = float(df["Location TIV"].sum()) or 1.0
    st.markdown("**Ten largest locations**")
    st.dataframe(pd.DataFrame({"Location": top["LocID"], "Name": top["Location Name"], "Address": top["Street Address"] + ", " + top["City"].astype(str),
                               "TIV": top["Location TIV"].map(money), "% of TIV": (top["Location TIV"] / tot).map(lambda v: f"{v:.1%}")}),
                 hide_index=True, width="stretch")


def render():
    p = need_account()
    if not p:
        return
    c = ctx()
    sub = p.submission()
    section("Layers", "Each RMS portfolio in the EDM is a layer (the text before the first underscore).")
    if need_rms() and (st.button("Refresh portfolios from RMS") or not p.portfolios()):
        try:
            with st.spinner("Reading portfolios..."):
                p.refresh_portfolios()
        except Exception as exc:                                # noqa: BLE001
            st.error(f"Could not read portfolios: {exc}")
    ports = p.portfolios()
    if ports:
        st.dataframe(pd.DataFrame(ports).rename(columns={"portfolio_name": "Portfolio", "portfolio_id": "Portfolio ID", "layer": "Layer"}),
                     hide_index=True, width="stretch", height=min(40 + 35 * len(ports), 260))

    section("Locations", "Downloads every location with its Building / Contents / BI split. Takes a few minutes for large accounts.")
    d = p.get("locations")
    b = st.columns([1.3, 1.3, 5])
    run = b[0].button("Load from RMS" if not d else "Reload from RMS", type="primary" if not d else "secondary", disabled=c.svc is None)
    if d:
        b[1].caption(f"Saved {len(d['rows']):,} rows")
    if run:
        cb, done = progress_cb()
        try:
            p.load_exposure(cb)
        except Exception as exc:                                # noqa: BLE001
            done()
            st.error(f"Exposure load failed - nothing was changed: {exc}")
        else:
            done()
            st.rerun()
    full = p.all_locations()
    if full is None:
        return

    accts = loc_rules.account_summary(full)
    mode, picked = p.account_mode()
    with st.container(border=True):
        st.markdown("**Accounts to include**")
        label = {"all": "All accounts", "latest": "Latest account only", "pick": "Choose accounts"}
        new = st.radio("Accounts", list(label), format_func=label.get, index=list(label).index(mode), horizontal=True,
                       label_visibility="collapsed", key=f"acct_mode_{p.id}")
        sel = picked
        if new == "pick":
            sel = st.multiselect("Account numbers", [str(a) for a in accts["Account Number"]], default=[str(a) for a in picked], key=f"acct_pick_{p.id}")
        if (new, sorted(sel)) != (mode, sorted(map(str, picked))):
            p.set_account_mode(new, sel)
            st.rerun()
        st.dataframe(accts.assign(TIV=accts["TIV"].map(money)), hide_index=True, width="stretch", height=min(40 + 35 * len(accts), 200))

    df, rec = p.locations(), p.exposure_check()
    if df is None or not len(df):
        st.warning("No locations in the current account selection.")
        return
    kpis([("Locations", f"{len(df):,}", None), ("TIV modelled", money(rec["tiv_modelled"]), None),
          ("TIV reported", money(rec["tiv_reported"]) if rec["tiv_reported"] else "not set", None),
          ("Delta", pct(rec["delta"]) if rec["delta"] is not None else "-", "modelled vs reported")])
    flags(rec["flags"])
    tabs = st.tabs(["Summary", "Map", "Locations"])
    with tabs[0]:
        exposure_charts(df)
    with tabs[1]:
        mp = charts.location_map_frame(df)
        if len(mp):
            st.caption("Bubble size is TIV at that point. Red: half or more of the largest point; amber: 15% or more.")
            st.map(mp, latitude="lat", longitude="lon", size="size", color="color", width="stretch")
        else:
            st.caption("No coordinates to plot.")
    with tabs[2]:
        view = df.copy()                                          # display only: mixed 0 / text columns (e.g. Admin4) cannot go to Arrow as they are
        for col in view.select_dtypes("object").columns:
            view[col] = view[col].map(lambda v: "" if v is None or (isinstance(v, float) and v != v) else str(v))
        st.dataframe(view, hide_index=True, width="stretch", height=420)
