"""Step 4 - run analyses in RMS, group perils, and choose the analyses that go into the report.
(RunRMSAnalysis, Get_Analyses_Foolproof, Submit_GroupingJobs / Refresh_GroupingJobs_Status)"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from cra.constants import WORKFLOW_SUCCESS
from cra.rules import ep as ep_rules

from .common import ctx, flags, need_account, need_rms, progress_cb, section, uk_dt

TERMINAL_BAD = {"FAILED", "SUBMIT_FAILED", "CANCELED", "CANCELLED", "ERROR"}


def _jobs_table(p):
    jobs = p.jobs()
    if not jobs:
        st.caption("No jobs submitted from this app yet.")
        return 0
    rows = [{"Type": "Analysis" if j["kind"] == "analysis" else "Grouping", "Layer": j.get("layer", ""), "Peril": j.get("peril", ""),
             "Status": j["status"], "Progress": j.get("progress"), "Submitted": uk_dt(j.get("submitted")),
             "Checked": uk_dt(j.get("last_polled")), "Note": (j.get("error") or "")[:120]} for j in jobs]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=min(60 + 35 * len(rows), 320),
                 column_config={"Progress": st.column_config.ProgressColumn("Progress", min_value=0, max_value=100, format="%d%%")})
    s = p.job_summary(jobs)
    st.caption(f"{s['done']} finished, {s['active']} in progress, {s['failed']} failed")
    bad = [j for j in jobs if j["status"] in TERMINAL_BAD and j.get("error")]
    if bad:
        with st.expander("Failure details"):
            for j in bad:
                st.code(j["error"][:1500])
    return s["active"]


def render():
    p = need_account()
    if not p:
        return
    c = ctx()
    sub = p.submission()
    ports = p.portfolios()

    # ------------------------------------------------------------ run
    section("1  Run analyses in RMS (optional)", "Skip this if the analyses already exist in RMS.")
    prof = p.profiles()
    st.warning("The model-profile IDs below are copied from the old RunRMSAnalysis macro and are **unverified**. Your current analyses use named "
               "profiles (e.g. AS_EQ_PREBIND, AS_TO_PREBIND, output AS_Amtrust_output). Check each ID in RMS before submitting.",
               icon=":material/rule:")
    with st.expander("Model settings", expanded=False):
        pf = pd.DataFrame(prof["perils"]).rename(columns={"code": "Code", "label": "Peril", "model_profile_id": "Model profile ID",
                                                         "event_rate_scheme_id": "Event rate scheme ID"})
        edited = st.data_editor(pf, hide_index=True, width="stretch", disabled=["Code", "Peril"], key=f"prof_edit_{p.id}")
        o1, o2, o3 = st.columns(3)
        out_id = o1.number_input("Output profile ID", value=int(prof["output_profile_id"]), step=1)
        vint = o2.text_input("Currency vintage", prof["currency"]["vintage"])
        asof = o3.text_input("Currency as-of date", prof["currency"]["asOfDate"])
        if st.button("Save settings for this quote"):
            prof["perils"] = [{"code": r["Code"], "label": r["Peril"], "model_profile_id": int(r["Model profile ID"]),
                               "event_rate_scheme_id": int(r["Event rate scheme ID"])} for _, r in edited.iterrows()]
            prof["output_profile_id"] = int(out_id)
            prof["currency"].update({"vintage": vint, "asOfDate": asof})
            st.success(f"Settings {p.put('profiles', prof).text()}")
    if not ports:
        st.info("Load the portfolios on the Exposure step first.")
    else:
        lab = {x["portfolio_id"]: f"{x['layer']}  ({x['portfolio_name']})" for x in ports}
        a, b = st.columns(2)
        pids = a.multiselect("Layers (portfolios)", list(lab), format_func=lab.get, default=list(lab))
        perils = b.multiselect("Perils", [x["code"] for x in prof["perils"]],
                               format_func=lambda k: next(f"{x['code']}  {x['label']}" for x in prof["perils"] if x["code"] == k))
        n = len(pids) * len(perils)
        ok = st.checkbox(f"Submit {n} analysis job(s) to RMS ({len(pids)} layer(s) x {len(perils)} peril(s))", disabled=n == 0)
        if st.button("Submit analyses", type="primary", disabled=not (ok and n and need_rms())):
            cb, done = progress_cb()
            res = p.submit_analyses(pids, perils, progress=cb)
            done()
            okn, skip = sum(1 for r in res if r.get("ok")), sum(1 for r in res if "skipped" in r)
            st.success(f"{okn} submitted" + (f", {skip} skipped (already submitted)" if skip else ""))
            for r in res:
                if r.get("ok") is False:
                    st.error(f"{r['portfolio_id']} / {r['peril']}: {r['error']}")

    section("2  Job status")
    active = _jobs_table(p)
    if need_rms():
        bcol = st.columns([1.4, 1.4, 5])
        if bcol[0].button("Refresh status"):
            with st.spinner("Checking RMS..."):
                p.refresh_jobs()
            st.rerun()
        auto = bcol[1].toggle("Auto-refresh", value=False, help="Checks RMS every 10 seconds while jobs are running")
        if auto and active:
            @st.fragment(run_every=10)
            def _tick():
                s = p.refresh_jobs()
                if s["active"] == 0:
                    st.rerun()
                st.caption(f"Checked {uk_dt(p.jobs()[-1].get('last_polled'))} - {s['active']} still running")
            _tick()

    # ------------------------------------------------------------ analysis list
    section("3  Analyses to use in the report", "Matched to this EDM by RMS exposure name first, then by the macro's name matching. Untick anything that does not belong.")
    if need_rms() and st.button("Find analyses in RMS", type="primary" if not p.analyses() else "secondary"):
        with st.spinner("Reading the analysis list from RMS..."):
            try:
                p.refresh_analyses()
            except Exception as exc:                            # noqa: BLE001
                st.error(f"Could not read analyses: {exc}")
        st.rerun()
    items = p.analyses()
    if not items:
        st.caption("No analyses found yet.")
        return
    df = pd.DataFrame([{"Use": a["selected"], "Layer": a["layer"], "Peril": a.get("peril") or a.get("perilCode"), "Analysis": a["analysisName"],
                        "Analysis ID": a["analysisId"], "App ID": a["appAnalysisId"], "Portfolio ID": a["exposureResourceId"],
                        "Created": a["createDate"][:10], "Match": a["match"]} for a in items])
    sig = abs(hash(tuple(a["analysisId"] for a in items))) % 10**8       # new list -> new editor state (stale row edits must not carry over)
    ed = st.data_editor(df, hide_index=True, width="stretch", key=f"an_edit_{p.id}_{sig}", height=min(80 + 35 * len(df), 460),
                        disabled=[c for c in df.columns if c != "Use"],
                        column_config={"Use": st.column_config.CheckboxColumn("Use", width="small")})
    chosen = set(ed.loc[ed["Use"], "Analysis ID"])
    if chosen != {a["analysisId"] for a in items if a["selected"]}:
        p.set_selection(chosen)
    fuzzy = [a for a in items if a["match"] != "exact EDM" and a["analysisId"] in chosen]
    if fuzzy:
        flags([("warn", f"{len(fuzzy)} selected analysis(es) were matched by name only, not by EDM. Check they belong to this account.")])
    dup = {}
    for a in items:
        if a["analysisId"] in chosen:
            dup.setdefault((a["layer"], a.get("perilCode") or a.get("peril")), []).append(a["analysisId"])
    for (l, pr), ids in dup.items():
        if len(ids) > 1:
            flags([("warn", f"Layer {l}, {pr}: {len(ids)} analyses selected - only the first is used in the report.")])
    st.caption(f"{len(chosen)} of {len(items)} selected")

    # ------------------------------------------------------------ grouping
    section("4  Group perils into an All-Perils analysis", "One grouping job per portfolio (analyses with a portfolio ID above 0), as the macro did.")
    sel_items = [a for a in items if a["analysisId"] in chosen]
    groups = {}
    for a in sel_items:
        try:
            if float(a["exposureResourceId"]) > 0:
                groups.setdefault(str(int(float(a["exposureResourceId"]))), []).append(a)
        except ValueError:
            pass
    if groups:
        st.dataframe(pd.DataFrame([{"Portfolio ID": k, "Layer": v[0]["layer"], "Analyses": len(v), "Perils": ", ".join(sorted({x.get('perilCode') or x['peril'] for x in v}))}
                                   for k, v in groups.items()]), hide_index=True, width="stretch")
        has_key = bool(c.settings.grouping_key) or c.demo
        if not has_key:
            st.error("GROUPING_API_KEY is not set.", icon=":material/key_off:")
        elif c.settings.grouping_key_source not in ("GROUPING_API_KEY", ""):
            st.caption(f"Grouping key source: {c.settings.grouping_key_source}")
        if st.button("Submit grouping jobs", disabled=not need_rms() or not has_key):
            cb, done = progress_cb()
            res = p.submit_grouping(sel_items, cb)
            done()
            for r in res:
                if r.get("skipped"):
                    st.info(f"Portfolio {r['portfolio_id']}: {r['skipped']}")
                elif r.get("ok"):
                    st.success(f"Portfolio {r['portfolio_id']}: submitted")
                else:
                    st.error(f"Portfolio {r['portfolio_id']}: not submitted")
                    st.code(r.get("error", "")[:1500])
    else:
        st.caption("Nothing to group - the selected analyses have no portfolio ID.")
