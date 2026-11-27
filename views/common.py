"""Shared UI helpers: context, formatting, banners, charts, navigation and the analysis-list cache.

Structure of the app: a HOME page (pick an EDM) and, once an EDM is open, a WORKSPACE with a menu:
Account | Exposure | Add layers | Analyses | Results | Report      (Hazards sits inside Exposure).
The EDM *is* the account: opening an EDM opens its saved account, or creates it on the spot.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd
import streamlit as st

from cra.config import Settings
from cra.pipeline import AccountPipeline
from cra.rms.service import RmsService
from cra.store import AccountStore

# ---- the menu of an open EDM -----------------------------------------------------------------------------
SECTIONS = [("account", "Account"), ("exposure", "Exposure"), ("layers", "Add layers"), ("analyses", "Analyses"),
            ("results", "Results"), ("report", "Report")]
STEPS = SECTIONS                                          # the old name, kept so nothing that imports it breaks
EXPOSURE_TABS = ["Locations and summary", "Hazards"]
# goto("hazards") and friends, as the views already call them  ->  (menu section, exposure tab)
GOTO = {"account": ("account", None), "exposure": ("exposure", EXPOSURE_TABS[0]), "hazards": ("exposure", EXPOSURE_TABS[1]),
        "layers": ("layers", None), "analyses": ("analyses", None), "results": ("results", None), "report": ("report", None)}


@dataclass
class Ctx:
    settings: Settings
    svc: RmsService | None
    store: AccountStore
    demo: bool = False


def ctx() -> Ctx:
    return st.session_state["ctx"]


def pipeline() -> AccountPipeline | None:
    aid = st.session_state.get("account_id")
    if not aid:
        return None
    c = ctx()
    return AccountPipeline(c.svc, c.store, aid)


def goto(step: str) -> None:
    """Switch the workspace menu at the top of the next run (the menu widget already exists by the time a view calls this)."""
    st.session_state["_goto"] = step


def resolve_goto(step: str) -> tuple:
    return GOTO.get(step, ("account", None))


def open_account(account_id: str, section: str = "account") -> None:
    """Make this account the open one (the app then shows its workspace)."""
    st.session_state["account_id"] = account_id
    st.session_state["section"] = section
    st.session_state.pop("lookup", None)


def go_home() -> None:
    """Leave the workspace and return to the home page."""
    for k in ("account_id", "section", "exp_tab", "_goto", "lookup", "ov_lookup", "ov_confirm_delete"):
        st.session_state.pop(k, None)
    try:
        st.query_params.clear()
    except Exception:                                       # noqa: BLE001
        pass


# ---------------------------------------------------------------- formatting
def money(v, dec: int = 0) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "-"
    return f"{v:,.{dec}f}"


def pct(v, dec: int = 1) -> str:
    return "-" if v is None else f"{v:+.{dec}%}"


def uk_dt(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return iso[:16]


def uk_day(iso: str | None) -> str:
    try:
        return datetime.fromisoformat((iso or "").replace("Z", "+00:00")).strftime("%d/%m/%Y")
    except ValueError:
        return (iso or "")[:10]


def section(title: str, sub: str | None = None) -> None:
    st.markdown(f"<div class='sec'><span>{title}</span></div>", unsafe_allow_html=True)
    if sub:
        st.caption(sub)


def kpis(items: list) -> None:
    """items: [(label, value, sub)]"""
    for c, (label, value, sub) in zip(st.columns(len(items)), items):
        c.markdown(f"<div class='kpi'><div class='kl'>{label}</div><div class='kv'>{value}</div><div class='ks'>{sub or '&nbsp;'}</div></div>",
                   unsafe_allow_html=True)


def flags(items: list) -> None:
    """items: [(level, text)] with level error|warn|info|ok."""
    icon = {"error": ":material/error:", "warn": ":material/warning:", "info": ":material/info:", "ok": ":material/check_circle:"}
    fn = {"error": st.error, "warn": st.warning, "info": st.info, "ok": st.success}
    for lvl, msg in items:
        fn.get(lvl, st.info)(msg, icon=icon.get(lvl))


def show_chart(chart) -> None:
    """Altair chart, full width, our own styling (not the Streamlit theme)."""
    try:
        st.altair_chart(chart, theme=None, width="stretch")
    except TypeError:                                     # older Streamlit
        st.altair_chart(chart, theme=None, use_container_width=True)


def progress_cb():
    """-> (callback(frac, msg), cleanup()). A real progress bar tied to the work, not a spinner."""
    bar = st.progress(0.0, text="Starting")

    def cb(frac: float, msg: str):
        bar.progress(min(max(frac, 0.0), 1.0), text=msg)
    return cb, bar.empty


def section_status(p: AccountPipeline | None) -> dict:
    """Which menu sections have something in them (shown as a tick in the menu)."""
    keys = [k for k, _ in SECTIONS]
    if not p:
        return {k: False for k in keys}
    parts = (p.store.manifest(p.id) or {}).get("parts", {})
    sel = [a for a in (p.analyses() if "analyses" in parts else []) if a.get("selected")]
    return {"account": False, "exposure": "locations" in parts, "layers": "layer_runs" in parts,
            "analyses": bool(sel), "results": "results" in parts, "report": bool(p.store.list_files(p.id, "reports"))}


step_status = section_status                              # the old name


def need_account() -> AccountPipeline | None:
    p = pipeline()
    if not p:
        st.info("Open an EDM from the home page first.", icon=":material/arrow_back:")
        if st.button("Go to the home page"):
            go_home()
            st.rerun()
    return p


def need_rms() -> bool:
    if ctx().svc is None:
        st.error("RMS is not connected. Set RISKDATA_API_KEY (and LI_API_KEY / GROUPING_API_KEY) in the Posit Vars tab, "
                 "or start with CRA_DEMO=1.", icon=":material/key_off:")
        return False
    return True


@st.cache_data(ttl=600, show_spinner="Reading the analysis list from RMS (all analyses on the tenant)...")
def _analysis_list(_svc, sig: str, nonce: int) -> list:
    return _svc.list_analyses()


def all_analyses(refresh: bool = False) -> list:
    """Every analysis on the tenant, cached for 10 minutes; refresh=True forces a new read."""
    c = ctx()
    if refresh:
        st.session_state["an_nonce"] = st.session_state.get("an_nonce", 0) + 1
    return _analysis_list(c.svc, "demo" if c.demo else c.settings.host, st.session_state.get("an_nonce", 0))


CSS = """
<style>
#MainMenu, footer {visibility:hidden}
.block-container {padding-top: 3.6rem; max-width: 1320px}      /* clears Streamlit's fixed header */
.topbar {background:#0B2545; color:#fff; padding:14px 22px; border-radius:6px; display:flex; align-items:center; justify-content:space-between; margin-bottom:10px}
.topbar .t {font-size:1.15rem; font-weight:650; letter-spacing:.01em}
.topbar .s {font-size:.72rem; opacity:.65; letter-spacing:.08em; text-transform:uppercase}
.topbar .a {font-size:.85rem; text-align:right}
.topbar .a b {display:block; font-size:.95rem}
.sec {border-bottom:1px solid #D9DEE8; margin:22px 0 10px; padding-bottom:4px}
.sec span {font-size:.74rem; font-weight:650; letter-spacing:.1em; text-transform:uppercase; color:#0F4C81}
.kpi {background:#F3F5F9; border:1px solid #E2E6EF; border-radius:6px; padding:12px 14px; height:100%}
.kl {font-size:.66rem; letter-spacing:.09em; text-transform:uppercase; color:#5B6478; font-weight:600}
.kv {font-size:1.4rem; font-weight:650; color:#0B2545; line-height:1.25}
.ks {font-size:.72rem; color:#6B7488}
.fl {border-left:4px solid; padding:7px 12px; margin:5px 0; border-radius:3px; font-size:.9rem}
.fl b {font-size:.68rem; letter-spacing:.08em; text-transform:uppercase; margin-right:8px}
.fl.red {background:#FFC7CE; border-color:#C0392B; color:#7A0010}
.fl.amber {background:#FFF2C2; border-color:#D9A400; color:#6B4B00}
.fl.good {background:#D9F2DE; border-color:#2E8B57; color:#0B5B2A}
.fl.info {background:#E3EEF7; border-color:#5B8DB8; color:#1F3864}
.snap td {padding:5px 10px; border-bottom:1px solid #E2E6EF; font-size:.9rem; vertical-align:top}
.snap td:first-child {font-weight:600; background:#EEF3F9; width:210px; color:#1F3864}
.snap table {border-collapse:collapse; width:100%; border:1px solid #D5DBE6}
.row-name {font-weight:600; color:#0B2545}
.row-sub {color:#5B6478; font-size:.88rem}
.home-h {font-size:1.5rem; font-weight:650; color:#0B2545; margin:.4rem 0 .2rem}
div[data-testid="stSegmentedControl"] button {font-weight:550}
</style>
"""


def finding_boxes(findings: list, levels=("red", "amber", "good", "info")) -> None:
    label = {"red": "Red", "amber": "Amber", "good": "Good", "info": "Note"}
    for lvl in levels:
        for f in [x for x in findings if x["level"] == lvl]:
            st.markdown(f"<div class='fl {lvl}'><b>{label[lvl]} · {f['area']}</b>{f['text']}</div>", unsafe_allow_html=True)


def snapshot_table(rows: list) -> None:
    body = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in rows)
    st.markdown(f"<div class='snap'><table>{body}</table></div>", unsafe_allow_html=True)
