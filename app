"""CRA E&S Quote Desk - Streamlit rebuild of CRA_E&S_Template.xlsm.

    Account -> Exposure -> Hazards -> Analyses -> Results -> Report

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
from views import account, analyses, common, exposure, hazards, report, results   # noqa: E402

VIEWS = {"account": account, "exposure": exposure, "hazards": hazards, "analyses": analyses, "results": results, "report": report}


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
        return DemoService()
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


def _connection_panel(c: common.Ctx):
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
            st.warning("Only one storage location is writable, so there is no second copy of saved quotes.")
        if st.button("Test connections", disabled=c.svc is None):
            with st.spinner("Testing..."):
                for name, ok, msg in c.svc.test_connections():
                    (st.success if ok else st.error)(f"{name}: {msg}")


def main():
    st.markdown(common.CSS, unsafe_allow_html=True)
    c = _bootstrap()
    st.session_state["ctx"] = c
    p = common.pipeline()
    sub = p.submission() if p else {}
    st.markdown(
        "<div class='topbar'><div><div class='s'>Catastrophe modelling</div><div class='t'>CRA E&amp;S Quote Desk</div></div>"
        + (f"<div class='a'>{sub.get('account_name', '')}<b>{sub.get('exposure_name', '')}</b></div>" if sub else "")
        + "</div>", unsafe_allow_html=True)
    status = common.step_status(p)
    labels = dict(common.STEPS)
    if "_goto" in st.session_state:
        st.session_state["step"] = st.session_state.pop("_goto")
    st.session_state.setdefault("step", "account")
    step = st.segmented_control("Step", [k for k, _ in common.STEPS], key="step", label_visibility="collapsed",
                                format_func=lambda k: f"{'✓  ' if status.get(k) and k != 'account' else ''}{labels[k]}") or "account"
    _connection_panel(c)
    VIEWS[step].render()


main()
