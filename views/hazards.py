"""Step 3 - Location Intelligence hazards (flood zone, distance to coast, EQ soil / liquefaction / landslide, MMI)."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from .common import ctx, flags, kpis, need_account, need_rms, progress_cb, section


def render():
    p = need_account()
    if not p:
        return
    df = p.locations()
    if df is None:
        st.info("Load the exposure first (Exposure step).", icon=":material/arrow_back:")
        return
    section("Hazard enrichment", "Four Location Intelligence calls per distinct US point. These calls are chargeable, so nothing runs until you press the button.")
    plan = p.hazard_plan()
    kpis([("Locations", f"{plan['locations']:,}", "in the selected accounts"), ("US with a state", f"{plan['eligible']:,}", "can be enriched"),
          ("Distinct points", f"{plan['unique_points']:,}", "locations at one address are charged once"),
          ("Already saved", f"{plan['cached']:,}", "never charged twice"), ("To fetch now", f"{plan['to_fetch']:,}", f"{plan['to_fetch'] * 4:,} LI calls")])
    st.write("")
    if need_rms():
        retry = st.checkbox("Also retry points that previously returned an error", value=True)
        ok = st.checkbox(f"I understand this will make about {plan['to_fetch'] * 4:,} chargeable Location Intelligence calls",
                         disabled=plan["to_fetch"] == 0 and not retry)
        if st.button("Add hazards", type="primary", disabled=not ok):
            cb, done = progress_cb()
            try:
                out = p.enrich_hazards(cb, retry_errors=retry)
            except Exception as exc:                            # noqa: BLE001
                done()
                st.error(f"Stopped: {exc}. Everything fetched so far is saved - press the button again to carry on.")
            else:
                done()
                if out["errors"]:
                    st.warning(f"{out['errors']:,} point(s) returned an error from RMS; they are marked 'Error' and will be retried next time.")
                st.rerun()
    haz = df["floodZone"].notna().sum()
    if not haz:
        st.caption("No hazard data saved yet.")
        return
    section("Results")
    errs = int((df[["floodZone", "soilDescription", "mmi100yr"]] == "Error").any(axis=1).sum())
    if errs:
        flags([("warn", f"{errs:,} location(s) have an 'Error' hazard value.")])
    cols = st.columns(2)
    for col, field, title in ((cols[0], "floodZone", "FEMA flood zone"), (cols[1], "DTC", "Distance to inner coast"),
                              (cols[0], "MMI_Description", "Earthquake shaking (MMI 100-yr)"), (cols[1], "soilDescription", "Soil")):
        t = (df.assign(**{field: df[field].astype(str).replace({"None": "Not enriched", "nan": "Not enriched"})})
               .groupby(field).agg(Locations=("LocID", "count"), TIV=("Location TIV", "sum")).reset_index()
               .sort_values("TIV", ascending=False))
        t["TIV"] = t["TIV"].map(lambda v: f"{v:,.0f}")
        col.markdown(f"**{title}**")
        col.dataframe(t, hide_index=True, width="stretch", height=min(60 + 35 * len(t), 260))
