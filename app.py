"""CRA E&S Quote Desk - Streamlit rebuild of CRA_E&S_Template.xlsm.

    Home: pick an EDM  ->  the EDM's workspace:  Account | Exposure | Add layers | Analyses | Results | Report

The EDM is the account. Opening one opens its saved account (or creates it). The address bar keeps ?edm=...&page=... so a
refresh or a bookmark returns to the same place.

Start:  streamlit run app.py          (CRA_DEMO=1 for an offline demo with synthetic data)
Keys (Posit Connect 'Vars' tab / environment): RISKDATA_API_KEY, LI_API_KEY, GROUPING_API_KEY, optional
GROUPING_RESOURCE_GROUP_ID, RMS_HOST, CRA_SHARE_DIR, CRA_STORE_DIR, CRA_MAILBOX, CRA_QUOTES_ROOT.
"""
from __future__ import annotations

import os

import streamlit as st

st.set_page_config(page_title="CRA E&S Quote Desk", page_icon=":material/query_stats:", layout="wide", initial_sidebar_state="collapsed")

from cra import APP_VERSION                                                # noqa: E402
from cra.config import Settings, default_store_roots, env_getter           # noqa: E402
from cra.rms.demo import DemoService                                       # noqa: E402
from cra.rms.service import RmsService                                     # noqa: E402
from cra.store import AccountStore                                         # noqa: E402
from views import analyses, common, exposure, hazards, home, layering, overview, report, results   # noqa: E402

PAGES = {"account": overview.render, "layers": layering.render, "analyses": analyses.render, "results": results.render,
         "report": report.render}


def _secrets() -> dict:
    try:
        return dict(st.secrets)
    except Exception:                                                      # noqa: BLE001  (no secrets file)
        return {}


@st.cache_resource(show_spinner=False)
def _store(roots: tuple) -> AccountStore:
    return AccountStore(list(roots))


@st.cache_resource(show_spinner=False)
def _service(sig: str, _settings: Settings, demo: bool):
    if demo:
        return DemoService(preseed=True)
    return RmsService(_settings.creds()) if _settings.riskdata_key else None


def _bootstrap() -> common.Ctx:
    settings = Settings.load(env_getter(_secrets()))
    demo = os.environ.get("CRA_DEMO", "").strip() in ("1", "true", "yes")
    sig = "|".join([settings.host, str(len(settings.riskdata_key)), str(len(settings.li_key)), str(len(settings.grouping_key)),
                    settings.grouping_resource_group_id, str(demo)])
    roots = tuple(str(r) for r in default_store_roots(settings))
    if demo:
        import tempfile
        roots = (os.path.join(tempfile.gettempdir(), "cra_demo_store"),)
    return common.Ctx(settings=settings, svc=_service(sig, settings, demo), store=_store(roots), demo=demo)


def _connection_panel(c: common.Ctx) -> None:
    with st.expander("Connection and storage"):
        s = c.settings
        st.markdown(f"**Host** `{s.host}`  |  **Version** {APP_VERSION}" + ("  |  **DEMO MODE - synthetic data**" if c.demo else ""))
        for name, val in (("RISKDATA_API_KEY", s.riskdata_key), ("LI_API_KEY", s.li_key), ("GROUPING_API_KEY", s.grouping_key)):
            src = s.grouping_key_source if name == "GROUPING_API_KEY" else ("set" if val else "")
            st.markdown(f"- `{name}`: {'set (' + src + ')' if val else '**not set**'}")
        for n in s.notes:
            st.caption(n)
        d = c.store.describe()
        st.markdown("**Saved to:** " + ", ".join(f"`{r}`" for r in d["writable"]))
        if d["unavailable"]:
            st.warning("Not writable (skipped): " + ", ".join(d["unavailable"]))
        if len(d["writable"]) < 2 and not c.demo:
            st.warning("Only one storage location is writable, so there is no second copy of saved accounts.")
        if st.button("Test connections", disabled=c.svc is None):
            with st.spinner("Testing..."):
                for name, ok, msg in c.svc.test_connections():
                    (st.success if ok else st.error)(f"{name}: {msg}")


def _topbar(account: str = "", edm: str = "") -> None:
    st.markdown(
        "<div class='topbar'><div><div class='s'>Catastrophe modelling</div><div class='t'>CRA E&amp;S Quote Desk</div></div>"
        + (f"<div class='a'>{account}<b>{edm}</b></div>" if edm else "") + "</div>", unsafe_allow_html=True)


def _qp(key: str) -> str:
    try:
        v = st.query_params.get(key)
        return v if isinstance(v, str) else ""
    except Exception:                                                      # noqa: BLE001
        return ""


def _restore_from_url(c: common.Ctx) -> None:
    """A refresh or a bookmark carries ?edm=<account>&page=<section>: reopen it, if that account still exists."""
    if st.session_state.get("account_id"):
        return
    aid = _qp("edm")
    if aid and c.store.manifest(aid):
        page = _qp("page")
        common.open_account(aid, page if page in dict(common.SECTIONS) else "account")
    elif aid:
        try:
            st.query_params.clear()
        except Exception:                                                  # noqa: BLE001
            pass


def _sync_url(account_id: str, section: str) -> None:
    try:
        if _qp("edm") != account_id or _qp("page") != section:
            st.query_params["edm"], st.query_params["page"] = account_id, section
    except Exception:                                                      # noqa: BLE001
        pass


def _pick_section(p) -> str:
    """The menu. Returns the key of the section to show."""
    keys = [k for k, _ in common.SECTIONS]
    labels = dict(common.SECTIONS)
    status = common.section_status(p)
    g = st.session_state.pop("_goto", None)
    if g:
        sec, tab = common.resolve_goto(g)
        st.session_state["section"] = sec
        if tab:
            st.session_state["exp_tab"] = tab
    if st.session_state.get("section") not in keys:
        st.session_state["section"] = "account"
    back, nav = st.columns([1.2, 9])
    if back.button("← All EDMs", key="back_home", help="Back to the list of EDMs"):
        common.go_home()
        st.rerun()
    with nav:
        picked = st.segmented_control("Menu", keys, key="section", label_visibility="collapsed",
                                      format_func=lambda k: f"{'✓  ' if status.get(k) else ''}{labels[k]}")
    return picked or "account"


def _exposure_page() -> None:
    """Exposure, with Hazards as its second tab (one thing at a time, so nothing runs that is not on screen)."""
    if st.session_state.get("exp_tab") not in common.EXPOSURE_TABS:
        st.session_state["exp_tab"] = common.EXPOSURE_TABS[0]
    tab = st.segmented_control("Exposure view", common.EXPOSURE_TABS, key="exp_tab", label_visibility="collapsed") or common.EXPOSURE_TABS[0]
    (hazards.render if tab == common.EXPOSURE_TABS[1] else exposure.render)()


def main() -> None:
    st.markdown(common.CSS, unsafe_allow_html=True)
    c = _bootstrap()
    st.session_state["ctx"] = c
    _restore_from_url(c)
    p = common.pipeline()
    if p is not None and c.store.manifest(p.id) is None:                   # deleted elsewhere
        common.go_home()
        p = None
        st.warning("That account no longer exists, so you are back at the home page.")
    if p is None:
        _topbar()
        home.render()
        _connection_panel(c)
        return
    sub = p.submission()
    _topbar(sub.get("account_name", ""), sub.get("exposure_name", ""))
    section = _pick_section(p)
    _sync_url(p.id, section)
    (_exposure_page if section == "exposure" else PAGES[section])()
    _connection_panel(c)


main()
