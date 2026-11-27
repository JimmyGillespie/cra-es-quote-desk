"""Home page: pick an EDM. The EDM is the account - choosing one opens its saved account, or creates it on the spot."""
from __future__ import annotations

import streamlit as st

from cra.pipeline import AccountPipeline
from cra.rules.naming import folder_name_from_exposure

from .common import all_analyses, ctx, open_account, uk_dt

RECENT = 8


@st.cache_data(ttl=900, show_spinner="Loading the EDM list from RMS...")
def _exposures(_svc, sig: str) -> list:
    return _svc.list_exposures()


def _stage(parts: dict) -> str:
    for key, label in (("results", "Results pulled"), ("jobs", "Analyses running"), ("hazards", "Hazards added"),
                       ("locations", "Exposure loaded"), ("submission", "Started")):
        if key in parts:
            return label
    return "Started"


def open_edm(name: str, eid: str) -> None:
    """Open the EDM's account (create it the first time) and go to its workspace."""
    c = ctx()
    p = AccountPipeline.existing_for(c.svc, c.store, eid)
    if p is None:
        p = AccountPipeline.create(c.svc, c.store, name, eid, account_name=folder_name_from_exposure(name))
    open_account(p.id)
    st.rerun()


def _picker(c) -> None:
    st.markdown("<div class='home-h'>Select an EDM</div>", unsafe_allow_html=True)
    if c.svc is None:
        st.error("RMS is not connected, so the EDM list cannot be loaded. Set RISKDATA_API_KEY in the Posit Vars tab "
                 "(or start with CRA_DEMO=1). You can still open a saved account below.", icon=":material/key_off:")
        return
    top = st.columns([8, 1.2])
    if top[1].button("Refresh list", help="Reload the EDM and analysis lists from RMS"):
        _exposures.clear()
        all_analyses(refresh=True)
    try:
        exps = _exposures(c.svc, "demo" if c.demo else c.settings.host)
    except Exception as exc:                                    # noqa: BLE001
        st.error(f"Could not read the EDM list from RMS: {exc}")
        return
    names = [e["exposureName"] for e in exps]
    pick = top[0].selectbox(f"{len(names):,} EDMs in RMS - type to search, choose one to open it", names, index=None,
                            placeholder="Type part of the name, e.g. Ben Davis", key="home_pick")
    if pick:
        eid = next(e["exposureId"] for e in exps if e["exposureName"] == pick)
        try:
            open_edm(pick, eid)
        except OSError as exc:
            st.error(f"The account for this EDM could not be saved, so it was not opened: {exc}")
        except Exception as exc:                                # noqa: BLE001
            st.error(f"Could not open this EDM: {exc}")


def _recent(c) -> None:
    accounts = c.store.list_accounts()
    if not accounts:
        st.caption("EDMs you open appear here, so you can come back to them in one click.")
        return
    st.markdown("**Recently opened**")
    for a in accounts[:RECENT]:
        r = st.columns([4, 2, 2.2, 1])
        r[0].markdown(f"<span class='row-name'>{a.get('account_name') or a.get('exposure_name')}</span><br>"
                      f"<span class='row-sub'>{a.get('exposure_name', '')}</span>", unsafe_allow_html=True)
        r[1].write(_stage(a.get("parts", {})))
        r[2].write(uk_dt(a.get("updated")))
        if r[3].button("Open", key=f"home_open_{a['account_id']}"):
            open_account(a["account_id"])
            st.rerun()
    if len(accounts) > RECENT:
        st.caption(f"{len(accounts) - RECENT} older account(s) are not listed - search for the EDM above to open one.")


def render() -> None:
    c = ctx()
    _picker(c)
    _recent(c)
