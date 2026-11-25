"""Shared UI helpers: context, formatting, banners, step status."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd
import streamlit as st

from cra.config import Settings
from cra.pipeline import AccountPipeline
from cra.rms.service import RmsService
from cra.store import AccountStore

STEPS = [("account", "Account"), ("exposure", "Exposure"), ("hazards", "Hazards"), ("analyses", "Analyses"),
         ("results", "Results"), ("report", "Report")]


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
    """Switch step. Applied at the top of the next run (the step widget already exists by the time a view calls this)."""
    st.session_state["_goto"] = step


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


def section(title: str, sub: str | None = None) -> None:
    st.markdown(f"<div class='sec'><span>{title}</span></div>", unsafe_allow_html=True)
    if sub:
        st.caption(sub)


def kpis(items: list) -> None:
    """items: [(label, value, sub)]"""
    cols = st.columns(len(items))
    for c, (label, value, sub) in zip(cols, items):
        c.markdown(f"<div class='kpi'><div class='kl'>{label}</div><div class='kv'>{value}</div>"
                   f"<div class='ks'>{sub or '&nbsp;'}</div></div>", unsafe_allow_html=True)


def flags(items: list) -> None:
    icon = {"error": ":material/error:", "warn": ":material/warning:", "info": ":material/info:"}
    fn = {"error": st.error, "warn": st.warning, "info": st.info}
    for lvl, msg in items:
        fn.get(lvl, st.info)(msg, icon=icon.get(lvl))


def progress_cb():
    """-> (callback(frac, msg), cleanup())  A real progress bar tied to the work, not a spinner."""
    bar = st.progress(0.0, text="Starting")

    def cb(frac: float, msg: str):
        bar.progress(min(max(frac, 0.0), 1.0), text=msg)
    return cb, bar.empty


def step_status(p: AccountPipeline | None) -> dict:
    if not p:
        return {k: False for k, _ in STEPS}
    m = p.store.manifest(p.id) or {}
    parts = m.get("parts", {})
    sel = [a for a in (p.analyses() if "analyses" in parts else []) if a.get("selected")]
    return {"account": True, "exposure": "locations" in parts,
            "hazards": "hazards" in parts and "locations" in parts,
            "analyses": bool(sel), "results": "results" in parts, "report": bool(p.store.list_files(p.id, "reports"))}


def need_account() -> AccountPipeline | None:
    p = pipeline()
    if not p:
        st.info("Open or create a quote on the **Account** step first.", icon=":material/arrow_back:")
        if st.button("Go to Account"):
            goto("account")
            st.rerun()
    return p


def need_rms() -> bool:
    c = ctx()
    if c.svc is None:
        st.error("RMS is not connected. Set RISKDATA_API_KEY (and LI_API_KEY / GROUPING_API_KEY) in the Posit Vars tab, "
                 "or start with CRA_DEMO=1.", icon=":material/key_off:")
        return False
    return True


CSS = """
<style>
#MainMenu, footer {visibility:hidden}
.block-container {padding-top: 1.2rem; max-width: 1280px}
.topbar {background:#0B2545; color:#fff; padding:14px 22px; border-radius:6px; display:flex; align-items:center; justify-content:space-between; margin-bottom:10px}
.topbar .t {font-size:1.15rem; font-weight:650; letter-spacing:.01em}
.topbar .s {font-size:.72rem; opacity:.65; letter-spacing:.08em; text-transform:uppercase}
.topbar .a {font-size:.85rem; text-align:right}
.topbar .a b {display:block; font-size:.95rem}
.sec {border-bottom:1px solid #D9DEE8; margin:22px 0 10px; padding-bottom:4px}
.sec span {font-size:.74rem; font-weight:650; letter-spacing:.1em; text-transform:uppercase; color:#0F4C81}
.kpi {background:#F3F5F9; border:1px solid #E2E6EF; border-radius:6px; padding:12px 14px}
.kl {font-size:.66rem; letter-spacing:.09em; text-transform:uppercase; color:#5B6478; font-weight:600}
.kv {font-size:1.45rem; font-weight:650; color:#0B2545; line-height:1.25}
.ks {font-size:.72rem; color:#6B7488}
div[data-testid="stSegmentedControl"] button {font-weight:550}
</style>
"""
