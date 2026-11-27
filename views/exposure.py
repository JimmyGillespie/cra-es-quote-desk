"""Exposure: the location schedule as a dashboard.

    Overview | Map and geography | Buildings | Data quality | Locations

The Overview is a data-completeness page: how much of the value has a street-level geocode, a known construction, occupancy,
year built and number of stories, and how the schedule breaks down by each (the same idea as a Power BI 'Data quality and
completeness' page, with the figures written on the charts and a traffic-light on every completeness tile).
All the analysis is in cra/expo.py; the charts are in cra/viz.py. This file only lays them out.

The Microsoft sign-in and e-mail search helpers (_token, _device_signin, _email_finder) also live here because the Account and
Report pages import them.
"""
from __future__ import annotations

import math
import os
import time

import numpy as np
import pandas as pd
import streamlit as st

from cra import expo, graph, viz

from .common import ctx, flags, goto, kpis, need_account, need_rms, progress_cb, section, show_chart, uk_dt

VIEWS = ["Overview", "Map and geography", "Buildings", "Data quality", "Locations"]
MONEY = "${:,.0f}"
PCT = "{:.1%}"
LEVEL_COLOURS = {"green": "#2E8B57", "amber": "#E0A100", "red": "#C0392B"}


# =============================================================== Microsoft sign-in and e-mail search (used by other pages)
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


# =============================================================== small layout helpers
def _table(df: pd.DataFrame, fmt: dict | None = None, height: int | None = None) -> None:
    """A table with the numbers already formatted ($, %, thousands) and '-' for anything missing. Done here, not with a Styler, because
    Streamlit shows 'None' for empty cells of a styled table."""
    show = df.copy()
    fmt = fmt or {}

    def blank(v):
        return v is None or (isinstance(v, float) and v != v)
    for col in show.columns:
        f = fmt.get(col)
        if f:
            show[col] = show[col].map(lambda v, f=f: "-" if blank(v) else f.format(v))
        elif show[col].dtype == object:                                   # mixed text / 0 columns cannot go to Arrow as they are
            show[col] = show[col].map(lambda v: "" if blank(v) else str(v))
        elif pd.api.types.is_float_dtype(show[col]):
            show[col] = show[col].map(lambda v: "-" if blank(v) else f"{v:,.0f}" if float(v).is_integer() else f"{v:,.2f}")
    st.dataframe(show, hide_index=True, width="stretch", height=height or min(40 + 35 * len(show), 460))


def _legend(items: list) -> None:
    chips = "".join(f"<span style='display:inline-flex;align-items:center;margin:0 14px 4px 0;font-size:.85rem'>"
                    f"<span style='width:12px;height:12px;border-radius:50%;background:rgb({r},{g},{b});display:inline-block;margin-right:6px'></span>{label}</span>"
                    for label, (r, g, b) in items)
    st.markdown(f"<div>{chips}</div>", unsafe_allow_html=True)


def _panel(title: str, note: str | None = None) -> None:
    st.markdown(f"**{title}**")
    if note:
        st.caption(note)


def _chart(ch) -> None:
    show_chart(ch)


def _tile(label: str, value: str, sub: str | None = None, level: str | None = None) -> None:
    """A figure tile. With a level, the left edge is green / amber / red (completeness tiles)."""
    edge = LEVEL_COLOURS.get(level, "#0F4C81")
    st.markdown(
        f"<div style='background:#F3F5F9;border:1px solid #E2E6EF;border-left:6px solid {edge};border-radius:6px;padding:12px 14px;height:100%'>"
        f"<div style='font-size:.66rem;letter-spacing:.09em;text-transform:uppercase;color:#5B6478;font-weight:600'>{label}</div>"
        f"<div style='font-size:1.8rem;font-weight:650;color:#0B2545;line-height:1.2'>{value}</div>"
        f"<div style='font-size:.74rem;color:#6B7488'>{sub or '&nbsp;'}</div></div>", unsafe_allow_html=True)


# =============================================================== loading and account choice
def _load_bar(p, c, d) -> None:
    b = st.columns([1.6, 3.4, 1.4, 4])
    if b[0].button("Load locations from RMS" if not d else "Reload from RMS", type="primary" if not d else "secondary", disabled=c.svc is None, key="expo_load"):
        cb, done = progress_cb()
        try:
            p.load_exposure(cb)
        except Exception as exc:                                # noqa: BLE001
            done()
            st.error(f"Exposure load failed - nothing was changed: {exc}")
        else:
            done()
            st.rerun()
    if d:
        b[1].caption(f"{len(d['rows']):,} locations saved  ·  {uk_dt(d.get('fetched'))}")
        if b[2].button("Add hazards", key="expo_haz", help="Flood zone, distance to coast and earthquake hazards"):
            goto("hazards")
            st.rerun()


def _accounts(p, full) -> None:
    key = full["Account Number"].astype(str) if "Account Number" in full else pd.Series("1", index=full.index)
    acc = (full.assign(_a=key, _t=pd.to_numeric(full["Location TIV"], errors="coerce").fillna(0.0)).groupby("_a")
           .agg(Locations=("_t", "size"), TIV=("_t", "sum")).reset_index().rename(columns={"_a": "Account Number"}))
    if len(acc) < 2:
        return
    acc = acc.sort_values("Account Number", key=lambda s: pd.to_numeric(s, errors="coerce"))
    mode, picked = p.account_mode()
    label = {"all": "All accounts", "latest": "Latest account only", "pick": "Choose accounts"}
    with st.expander(f"Accounts included: {label.get(mode, mode)}  ({len(acc)} accounts in the EDM)"):
        new = st.radio("Accounts", list(label), format_func=label.get, index=list(label).index(mode) if mode in label else 0, horizontal=True,
                       label_visibility="collapsed", key=f"acct_mode_{p.id}")
        sel = picked
        if new == "pick":
            sel = st.multiselect("Account numbers", list(acc["Account Number"]), default=[str(a) for a in picked], key=f"acct_pick_{p.id}")
        if (new, sorted(map(str, sel))) != (mode, sorted(map(str, picked))):
            p.set_account_mode(new, sel)
            st.rerun()
        _table(acc, {"TIV": MONEY}, height=min(40 + 35 * len(acc), 220))


# =============================================================== the five views
COMPLETENESS = [("geo", "Street-level geocoding"), ("cons", "Construction known"), ("occ", "Occupancy known"),
                ("year", "Year built known"), ("stories", "Stories known")]


def _short(labels: list, n: int = 28) -> list:
    """Long category names shortened for a narrow chart (kept whole if shortening would make two of them the same)."""
    cut = [x if len(x) <= n else x[:n - 1].rstrip() + "…" for x in labels]
    return cut if len(set(cut)) == len(cut) else list(labels)


def _top5(df, col: str, title: str, color: str):
    g = expo.group_tiv(df, col, top=5, other="All other")
    g["label"] = _short(g["label"].tolist())
    return viz.hbar(g, "label", "TIV", title, note="Locations", order=g["label"].tolist(), color=color)


def _overview(df, h) -> None:
    rep = h["reported"]
    a = st.columns(4)
    with a[0]:
        _tile("TIV modelled", viz.fmt_money(h["tiv"]), (f"{h['delta'] * 100:+.1f}% vs reported {viz.fmt_money(rep)}" if rep and h["delta"] is not None else "no reported TIV set"))
    with a[1]:
        _tile("Locations", f"{h['locations']:,}", f"{h['states']} state(s)" if h["states"] else None)
    with a[2]:
        _tile("Accounts", f"{h['accounts']:,}")
    with a[3]:
        _tile("Average per location", viz.fmt_money(h["avg_tiv"]))

    section("How complete is the data?", "The share of the total value (TIV) that has each attribute. Green is 95% or more, amber 80% or more, red below that. "
                                         "The Data quality view lists the locations behind each one.")
    q = {r["key"]: r for r in expo.quality_scores(df)}
    for col, (key, label) in zip(st.columns(5), COMPLETENESS):
        r = q[key]
        with col:
            _tile(label, f"{r['pct_tiv'] * 100:.0f}%", (f"{r['bad']:,} location(s) · {viz.fmt_money(r['bad_tiv'])} to check" if r["bad"] else "every location"), r["level"])
    st.write("")

    c = st.columns(3)
    with c[0]:
        g = expo.group_tiv(df, "Geocoding Resolution")
        _chart(viz.hbar(g, "label", "TIV", "Geocoding resolution (share of TIV)", note="Locations", order=g["label"].tolist(), color="#0F4C81"))
    with c[1]:
        _chart(_top5(df, "Construction Name", "Top 5 construction (share of TIV)", "#2F6DB5"))
    with c[2]:
        _chart(_top5(df, "Occupancy Name", "Top 5 occupancy (share of TIV)", "#2A9D8F"))
    d = st.columns(3)
    with d[0]:
        _chart(viz.donut(expo.group_tiv(df, "Year Built Band", expo.YEAR_ORDER), "label", "TIV", "Year built"))
    with d[1]:
        _chart(viz.donut(expo.group_tiv(df, "Stories Band", expo.STORIES_ORDER), "label", "TIV", "Number of stories"))
    with d[2]:
        fa = expo.floor_area_groups(df)
        if fa is not None:
            _chart(viz.donut(fa, "label", "TIV", "Floor area"))
        else:
            sc = expo.state_county(df)
            by_state = sc.groupby("State", as_index=False).agg(Locations=("Locations", "sum"), TIV=("TIV", "sum")).sort_values("TIV", ascending=False).head(6)
            by_state["Share"] = by_state["TIV"] / (sc["TIV"].sum() or 1.0)
            _chart(viz.donut(by_state.rename(columns={"State": "label"}), "label", "TIV", "Value by state"))


def _deck(points: pd.DataFrame, height: int = 540) -> None:
    try:
        import pydeck as pdk
        lon, lat = points["lon"].to_numpy(), points["lat"].to_numpy()
        span = max(float(lon.max() - lon.min()), float(lat.max() - lat.min()) * 1.3, 0.02)
        zoom = max(2.0, min(12.0, math.log2(360.0 / span) - 0.6))
        view = pdk.ViewState(latitude=float((lat.min() + lat.max()) / 2), longitude=float((lon.min() + lon.max()) / 2), zoom=zoom, pitch=0)
        layer = pdk.Layer("ScatterplotLayer", data=points, get_position=["lon", "lat"], get_radius="radius", get_fill_color=["r", "g", "b", 185], pickable=True,
                          radius_min_pixels=5, radius_max_pixels=46, stroked=True, get_line_color=[255, 255, 255], line_width_min_pixels=1)
        tip = {"html": "<b>{label}</b><br/>{tiv_txt} &middot; {locs} location(s)<br/>{category}", "style": {"backgroundColor": "#0B2545", "color": "white", "fontSize": "12px"}}
        deck = pdk.Deck(layers=[layer], initial_view_state=view, tooltip=tip, map_provider="carto", map_style="light")
        try:
            st.pydeck_chart(deck, height=height, width="stretch")
        except TypeError:
            st.pydeck_chart(deck, use_container_width=True)
    except Exception as exc:                                        # noqa: BLE001
        st.caption(f"The interactive map could not be drawn ({type(exc).__name__}); showing a simple map.")
        simple = points.assign(color=["#%02x%02x%02x" % (r, g, b) for r, g, b in zip(points["r"], points["g"], points["b"])], size=points["radius"] / 4)
        st.map(simple, latitude="lat", longitude="lon", size="size", color="color")


def _geography(df, h) -> None:
    modes = [m for m in expo.MAP_MODES if m != "Flood zone" or expo.hazards_added(df)]
    c = st.columns([2.4, 6])
    mode = c[0].selectbox("Colour the map by", modes, key="expo_map_mode", help="Circle size is always the value at that point.")
    pts, legend = expo.map_points(df, mode)
    if pts.empty:
        c[1].info("None of the locations has usable coordinates, so there is nothing to plot.")
    else:
        _deck(pts)
        _legend(legend)
        placed = int(pts["locs"].sum())
        st.caption(f"{len(pts):,} distinct points ({placed:,} of {len(df):,} locations have coordinates). Circle size is the value at that point; hover for the address.")
    a, b = st.columns(2)
    with a:
        sc = expo.state_county(df)
        top = sc.head(12).assign(label=lambda d: d["County"] + ", " + d["State"])
        _chart(viz.hbar(top, "label", "TIV", "Biggest counties", note="Locations"))
    with b:
        cell = st.radio("Pile-up size", [0.01, 0.05, 0.1], index=1, format_func={0.01: "About 1 km", 0.05: "About 5 km", 0.1: "About 10 km"}.get, horizontal=True, key="expo_cell")
        acc = expo.accumulations(df, cell, 10)
        if acc.empty:
            st.caption("No coordinates to build accumulations from.")
        else:
            top = acc.assign(label=acc["Area"])
            _chart(viz.hbar(top, "label", "TIV", "Biggest piles of value in one small area", note="Locations"))
    section("Windstorm and the coast", "Wind tier by how precisely the locations are placed, and the hazard layers if they were added.")
    a, b = st.columns(2)
    with a:
        wt = expo.group_tiv(df, "Wind Tier", expo.WIND_ORDER)
        _chart(viz.donut(wt, "label", "TIV", "Value by wind tier", center="Wind tier"))
    with b:
        w = expo.wind_by_resolution(df)
        _panel("Wind tier by geocoding")
        _table(w, {"TIV": MONEY, "Locations": "{:,.0f}"}, height=min(40 + 35 * len(w), 320))
    hz = expo.hazard_groups(df)
    if hz:
        items = list(hz.items())
        for i in range(0, len(items), 2):
            for col, (name, g) in zip(st.columns(2), items[i:i + 2]):
                with col:
                    _chart(viz.hbar(g, "label", "TIV", name, order=g["label"].tolist()))
    else:
        st.caption("Hazards have not been added yet, so flood, coast distance and earthquake detail are not shown. Use 'Add hazards' above.")


def _buildings(df) -> None:
    m = expo.matrix(df, "Occupancy Name", "Construction Name", top_rows=7, top_cols=6)
    _chart(viz.heat(m, "What is insured and how it is built (value by occupancy and construction)"))
    st.caption("Darker boxes hold more value. A dark box in an old or timber construction class is where to look first.")
    a, b = st.columns(2)
    with a:
        _chart(viz.hbar(expo.group_tiv(df, "Construction Name", top=10), "label", "TIV", "Construction", note="Locations"))
    with b:
        _chart(viz.hbar(expo.group_tiv(df, "Occupancy Name", top=10), "label", "TIV", "Occupancy", note="Locations", color="#2A9D8F"))
    ah = expo.matrix(df, "Year Built Band", "Stories Band", row_order=expo.YEAR_ORDER, col_order=expo.STORIES_ORDER)
    _chart(viz.heat(ah, "Age and height (value by year built and number of stories)", row_title="", col_title=""))
    a, b = st.columns(2)
    with a:
        g = expo.group_tiv(df, "Construction Name")
        _panel("Construction table")
        _table(g.rename(columns={"label": "Construction"}), {"TIV": MONEY, "Share": PCT, "Average": MONEY})
    with b:
        g = expo.group_tiv(df, "Occupancy Name")
        _panel("Occupancy table")
        _table(g.rename(columns={"label": "Occupancy"}), {"TIV": MONEY, "Share": PCT, "Average": MONEY})


def _quality(df) -> None:
    rows = expo.quality_scores(df)
    reds = [r for r in rows if r["level"] == "red"]
    ambers = [r for r in rows if r["level"] == "amber"]
    kpis([("Checks", str(len(rows)), None), ("Passing (95%+ of value)", str(sum(1 for r in rows if r["level"] == "green")), None),
          ("Need a look", str(len(ambers)), "80-95% of value"), ("Weak", str(len(reds)), "under 80% of value")])
    st.write("")
    _chart(viz.score_bars(rows))
    st.caption("Each bar is the share of the total value that passes the check. Green is 95% or more, amber 80% or more, red below that.")
    bad = [r for r in rows if r["bad"] > 0]
    if not bad:
        st.success("Every check passes on every location.")
        return
    pick = st.selectbox("Show the locations that fail", [r["key"] for r in bad], format_func=lambda k: next(f"{r['check']}  -  {r['bad']} location(s), {viz.fmt_money(r['bad_tiv'])}" for r in bad if r["key"] == k), key="expo_issue")
    t = expo.issue_rows(df, pick)
    _table(t, {"Location TIV": MONEY, "Buildings TIV": MONEY}, height=min(40 + 35 * len(t), 420))
    st.download_button("Download these locations (CSV)", t.to_csv(index=False).encode("utf-8-sig"), file_name=f"locations_to_check_{pick}.csv", mime="text/csv", key="dl_issue")


def _locations(df) -> None:
    c = st.columns([3, 2.5, 2.5, 1.7])
    q = c[0].text_input("Search", placeholder="name, address or city", key="expo_q")
    states = sorted(expo.text(df, "Admin1").map(expo.title).unique())
    st_pick = c[1].multiselect("State", states, key="expo_state")
    occ = sorted(expo.text(df, "Occupancy Name").unique())
    occ_pick = c[2].multiselect("Occupancy", occ, key="expo_occ")
    minv = c[3].number_input("Minimum TIV", min_value=0.0, value=0.0, step=100000.0, format="%.0f", key="expo_min")
    d = df
    if q:
        hay = (expo.text(d, "Location Name", "") + " " + expo.text(d, "Street Address", "") + " " + expo.text(d, "City", "")).str.lower()
        d = d[hay.str.contains(q.lower(), regex=False)]
    if st_pick:
        d = d[expo.text(d, "Admin1").map(expo.title).isin(st_pick)]
    if occ_pick:
        d = d[expo.text(d, "Occupancy Name").isin(occ_pick)]
    if minv:
        d = d[expo.num(d, "Location TIV") >= minv]
    wanted = ["LocID", "Location Name", "Street Address", "City", "Admin1", "Occupancy Name", "Construction Name", "Year Built Band", "Stories Band", "Geocoding Resolution",
              "Location TIV", "Buildings TIV", "Contents TIV", "BI TIV", "Wind Tier", "floodZone", "DTC"]
    every = st.toggle("Show every column", key="expo_allcols")
    cols = list(d.columns) if every else [x for x in wanted if x in d.columns]
    d = d.assign(_t=expo.num(d, "Location TIV")).sort_values("_t", ascending=False)[cols]
    st.caption(f"{len(d):,} of {len(df):,} locations, biggest first")
    _table(d, {k: MONEY for k in ("Location TIV", "Buildings TIV", "Contents TIV", "BI TIV")}, height=520)
    st.download_button("Download these rows (CSV)", d.to_csv(index=False).encode("utf-8-sig"), file_name="locations.csv", mime="text/csv", key="dl_locs")


def _workbook_button(df, rep) -> None:
    key = "expo_xlsx"
    if st.button("Prepare summary workbook", key="expo_xlsx_btn", help="Every table on these views in one Excel file"):
        with st.spinner("Building the workbook..."):
            st.session_state[key] = expo.summary_workbook(df, rep)
    if st.session_state.get(key):
        st.download_button("Download the exposure summary (.xlsx)", st.session_state[key], file_name="Exposure_Summary.xlsx", type="primary",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="expo_xlsx_dl")


# =============================================================== the page
def render() -> None:
    p = need_account()
    if not p:
        return
    c = ctx()
    if not p.portfolios() and c.svc is not None:
        try:
            p.refresh_portfolios()
        except Exception:                                       # noqa: BLE001 - the layers are shown elsewhere; never block the page
            pass
    d = p.get("locations")
    _load_bar(p, c, d)
    full = p.all_locations()
    if full is None:
        st.info("The locations have not been loaded yet. Press **Load locations from RMS** - it takes a few minutes for a large account - and this page fills with the "
                "completeness overview, map and data-quality views.", icon=":material/downloading:")
        return
    _accounts(p, full)
    df, rec = p.locations(), p.exposure_check()
    if df is None or not len(df):
        st.warning("No locations in the current account selection.")
        return
    notes = rec["flags"] if rec else []
    if notes:
        with st.expander(f"Data notes ({len(notes)})", expanded=any(lvl in ("error", "warn") for lvl, _m in notes)):
            flags(notes)
    h = expo.headline(df, (rec or {}).get("tiv_reported"))
    top = st.columns([6.5, 3.5])
    if st.session_state.get("expo_view") not in VIEWS:                  # also covers a view that no longer exists (e.g. the old 'Concentration')
        st.session_state["expo_view"] = VIEWS[0]
    view = top[0].segmented_control("View", VIEWS, key="expo_view", label_visibility="collapsed") or VIEWS[0]
    with top[1]:
        _workbook_button(df, h["reported"])
    st.write("")
    if view == "Map and geography":
        _geography(df, h)
    elif view == "Buildings":
        _buildings(df)
    elif view == "Data quality":
        _quality(df)
    elif view == "Locations":
        _locations(df)
    else:
        _overview(df, h)
