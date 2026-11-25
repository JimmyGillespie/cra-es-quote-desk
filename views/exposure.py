"""Step 2 - submission details, layers, locations and the exposure summary (Controls, Locations, Exposure Summary, Map)."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from cra import graph
from cra.rules import locations as loc_rules

from .common import ctx, flags, kpis, money, need_account, need_rms, pct, progress_cb, section


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
                        "underwriter": best["underwriter"] or sub.get("underwriter", ""),
                        "priority": best["priority"] or sub.get("priority", ""),
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


def _token():
    import os
    ss = st.session_state
    t = ss.get("ms_tok")
    import time
    if t and t.get("expires_at", 0) > time.time():
        return t["access_token"], ""
    if os.environ.get("MSGRAPH_TOKEN"):
        return os.environ["MSGRAPH_TOKEN"], ""
    try:
        sess = st.context.headers.get("Posit-Connect-User-Session-Token") or ""
    except Exception:                                           # noqa: BLE001
        sess = ""
    if sess:
        return graph.connect_token(sess)
    return "", "no token"


def _device_signin():
    import os
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


def render():
    p = need_account()
    if not p:
        return
    c = ctx()
    sub = p.submission()
    section("Submission")
    with st.form("submission_form"):
        f = st.columns(4)
        acct = f[0].text_input("Account name", sub.get("account_name", ""))
        uw = f[1].text_input("Underwriter", sub.get("underwriter", ""))
        prio = f[2].selectbox("Priority", ["", "High", "Normal", "Low"], index=["", "High", "Normal", "Low"].index(sub.get("priority", "") or ""))
        tiv = f[3].number_input("TIV reported", min_value=0.0, value=float(sub.get("tiv_reported") or 0.0), step=100000.0, format="%.0f")
        terms = st.text_area("Quote terms / comments", sub.get("comments", ""), height=70)
        if st.form_submit_button("Save details"):
            sub.update({"account_name": acct, "underwriter": uw, "priority": prio, "tiv_reported": tiv or None, "comments": terms})
            rep = p.put("submission", sub, header={"account_name": acct})
            (st.success if rep.ok else st.error)(f"Details {rep.text()}")
    st.caption(f"EDM **{sub['exposure_name']}**  |  Exposure ID {sub['exposure_id']}  |  Quote {p.id}")
    _email_finder(p, sub)

    # ------------------------------------------------------------ layers
    section("Layers", "Each RMS portfolio in the EDM is a layer (the text before the first underscore).")
    if need_rms():
        if st.button("Refresh portfolios from RMS") or not p.portfolios():
            try:
                with st.spinner("Reading portfolios..."):
                    p.refresh_portfolios()
            except Exception as exc:                            # noqa: BLE001
                st.error(f"Could not read portfolios: {exc}")
    ports = p.portfolios()
    if ports:
        st.dataframe(pd.DataFrame(ports).rename(columns={"portfolio_name": "Portfolio", "portfolio_id": "Portfolio ID", "layer": "Layer"}),
                     hide_index=True, width="stretch", height=min(60 + 35 * len(ports), 260))

    # ------------------------------------------------------------ locations
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

    # account selection (Run_ALL vs the highest-accountId macro)
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
        st.dataframe(accts.assign(TIV=accts["TIV"].map(money)), hide_index=True, width="stretch",
                     height=min(60 + 35 * len(accts), 200))

    df = p.locations()
    rec = p.exposure_check()
    if df is None or not len(df):
        st.warning("No locations in the current account selection.")
        return
    kpis([("Locations", f"{len(df):,}", None), ("TIV modelled", money(rec["tiv_modelled"]), None),
          ("TIV reported", money(rec["tiv_reported"]) if rec["tiv_reported"] else "not set", None),
          ("Delta", pct(rec["delta"]) if rec["delta"] is not None else "-", "modelled vs reported")])
    flags(rec["flags"])

    tabs = st.tabs(["Summary", "Map", "Locations"])
    with tabs[0]:
        sums = loc_rules.exposure_summaries(df)
        keys = list(sums)
        for i in range(0, len(keys), 2):
            cols = st.columns(2)
            for col, k in zip(cols, keys[i:i + 2]):
                col.markdown(f"**{k}**")
                t = sums[k].copy()
                t["Sum of Location TIV"] = t["Sum of Location TIV"].map(money)
                col.dataframe(t, hide_index=True, width="stretch", height=min(60 + 35 * len(t), 280))
    with tabs[1]:
        mp = loc_rules.map_aggregate(df)
        mp = mp.assign(lat=pd.to_numeric(mp["Latitude"], errors="coerce"), lon=pd.to_numeric(mp["Longitude"], errors="coerce")).dropna(subset=["lat", "lon"])
        if len(mp):
            mp["size"] = 300 + 2500 * mp["Location TIV Sum"] / max(mp["Location TIV Sum"].max(), 1)
            st.map(mp, latitude="lat", longitude="lon", size="size", width="stretch")
        else:
            st.caption("No coordinates to plot.")
    with tabs[2]:
        st.dataframe(df, hide_index=True, width="stretch", height=420)
