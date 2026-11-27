"""Account overview: the front page of an open EDM. What is done, what is next, numbers at a glance, find results that already
exist in RMS, the underwriter's details, and housekeeping. Every read is guarded, so a bad file can never blank the page."""
from __future__ import annotations

import streamlit as st

from cra.rules import matching

from .common import (all_analyses, ctx, go_home, goto, kpis, money, need_account, need_rms, pct, progress_cb, section, snapshot_table,
                     uk_day, uk_dt)

PRIORITIES = ["", "High", "Normal", "Low"]
ORDER = ["exposure", "analyses", "results", "report"]        # the first one not done is highlighted as the next step


def _parts(p) -> dict:
    return (p.store.manifest(p.id) or {}).get("parts", {})


def _safe(fn, default, what: str):
    try:
        return fn()
    except Exception as exc:                                  # noqa: BLE001
        st.warning(f"Could not read {what}: {type(exc).__name__}: {exc}", icon=":material/warning:")
        return default


# ---------------------------------------------------------------- the top of the page
def _header(p, sub, manifest) -> None:
    st.markdown("<div class='home-h'>Account overview</div>", unsafe_allow_html=True)
    bits = [f"EDM **{sub.get('exposure_name', '')}**", f"exposure ID {sub.get('exposure_id', '')}"]
    if sub.get("underwriter"):
        bits.append(f"underwriter {sub['underwriter']}")
    if sub.get("priority"):
        bits.append(f"priority {sub['priority']}")
    if manifest.get("updated"):
        bits.append(f"last saved {uk_dt(manifest['updated'])}")
    st.caption("  ·  ".join(bits))


def _numbers(rec, ports, rev, has_results) -> None:
    layers = [x["layer"] for x in ports if x.get("layer")]
    tiv_rep = rec.get("tiv_reported") if rec else None
    c = rev["counts"] if rev else None
    kpis([("Locations", f"{rec['locations']:,}" if rec else "not loaded", "load them on the Exposure page" if not rec else None),
          ("TIV modelled", money(rec["tiv_modelled"]) if rec else "-", None),
          ("TIV reported", money(tiv_rep) if tiv_rep else "not set", (pct(rec["delta"]) + " vs modelled") if rec and rec.get("delta") is not None else None),
          ("Layers", str(len(layers)), ", ".join(layers[:4]) + (" ..." if len(layers) > 4 else "") if layers else "none read yet"),
          ("Review", f"{c['red']} red · {c['amber']} amber" if c and has_results else "-",
           f"{c['good']} checks passed" if c and has_results else "pull results first")])


# ---------------------------------------------------------------- what is done, what is next
def _status_rows(p, parts, rec, ports) -> list:
    sel = [a for a in p.analyses() if a.get("selected")] if "analyses" in parts else []
    res = p.results() if "results" in parts else []
    fetched = (p.get("results") or {}).get("fetched") if "results" in parts else None
    files = p.store.list_files(p.id, "reports")
    layers = [x["layer"] for x in ports if x.get("layer")]
    return [
        ("exposure", "Exposure", "locations" in parts,
         f"{rec['locations']:,} locations · TIV {money(rec['tiv_modelled'])}" if rec else "Not loaded yet", "exposure",
         "View exposure" if "locations" in parts else "Load exposure"),
        ("hazards", "Hazards", "hazards" in parts, "Flood, coast and earthquake hazards added" if "hazards" in parts else "Not added (optional)",
         "hazards", "View hazards" if "hazards" in parts else "Add hazards"),
        ("layers", "Layers", bool(layers), ", ".join(layers) if layers else "No portfolios read yet", "layers", "Add layers"),
        ("analyses", "Analyses", bool(sel), f"{len(sel)} {'analysis' if len(sel) == 1 else 'analyses'} selected" if sel else "None selected yet", "analyses",
         "Open analyses" if sel else "Run analyses"),
        ("results", "Results", "results" in parts,
         f"{len(res)} {'analysis' if len(res) == 1 else 'analyses'} pulled · {uk_dt(fetched)}" if res else "Not pulled yet", "results", "View results"),
        ("report", "Report", bool(files), f"{len(files)} saved · latest {files[0][0].split('/')[-1]}" if files else "Not built yet",
         "report", "Open report" if files else "Build report"),
    ]


def _checklist(rows) -> None:
    section("What is here")
    nxt = next((r[0] for key in ORDER for r in rows if r[0] == key and not r[2]), None)
    for key, name, done, detail, target, label in rows:
        c = st.columns([0.4, 1.5, 5, 1.7])
        c[0].markdown(":green[✓]" if done else ":gray[○]")
        c[1].markdown(f"<span class='row-name'>{name}</span>", unsafe_allow_html=True)
        c[2].markdown(f"<span class='row-sub'>{detail}</span>", unsafe_allow_html=True)
        if c[3].button(label, key=f"ov_go_{key}", type="primary" if key == nxt else "secondary", width="stretch"):
            goto(target)
            st.rerun()


# ---------------------------------------------------------------- results that already exist in RMS
def _find_results(p, parts) -> None:
    has = "results" in parts
    with st.expander("Find results in RMS", expanded=not has):
        st.caption("Looks for every analysis RMS holds for this EDM (the newest run per layer and peril) and pulls the EP and RDS results. "
                   "You do not need to load the exposure or the hazards first.")
        if not need_rms():
            return
        if st.button("Check RMS again" if "analyses" in parts else "Check RMS for this EDM's analyses", key="ov_check"):
            with st.spinner("Matching analyses to the EDM..."):
                try:
                    p.refresh_portfolios()
                    p.refresh_analyses(all_analyses())
                except Exception as exc:                      # noqa: BLE001
                    st.error(f"Could not read the analyses from RMS: {exc}")
                    return
            parts = _parts(p)
        if "analyses" not in parts:
            return
        items = p.analyses()
        sure = [a for a in items if a.get("match") in matching.CONFIDENT]
        chosen = [a for a in items if a.get("selected")]
        if not sure:
            st.warning("RMS holds no analyses that are clearly named for this EDM." +
                       (f" {len(items)} loose name match(es) were found and are not used." if items else ""))
            return
        latest = max((a.get("createDate", "") for a in sure), default="")
        kpis([("Analyses found", str(len(sure)), "named for this EDM"), ("In the latest run", str(len(chosen)), "newest per layer and peril"),
              ("Layers", str(len({a["layer"] for a in chosen})), None), ("Latest run", uk_day(latest), None)])
        st.write("")
        cov = matching.coverage(items)
        if len(cov):
            st.dataframe(cov.fillna(""), hide_index=True, width="stretch", height=min(40 + 35 * len(cov), 240))
        if st.button("Pull the results again" if has else "Pull the results", type="secondary" if has else "primary", key="ov_pull", disabled=not chosen,
                     help="Reads the EP curves, statistics and RDS events for these analyses and opens the Results page."):
            cb, done = progress_cb()
            try:
                p.pull_results([a["analysisId"] for a in chosen], include_rl=True, progress=cb, with_rds=True)
            except Exception as exc:                          # noqa: BLE001
                done()
                st.error(f"Could not pull the results: {exc}")
            else:
                done()
                goto("results")
                st.rerun()


# ---------------------------------------------------------------- snapshot, details, housekeeping
def _snapshot(rev, has_results) -> None:
    if not rev:
        return
    with st.expander("Account snapshot", expanded=has_results):
        snapshot_table(rev["snapshot"])
        st.caption("The review - the red and amber flags and what to check - is on the Results page.")


def _details(p, sub) -> None:
    section("Details", "The underwriter's request. These appear on the report and in the e-mail reply.")
    with st.form("ov_details"):
        f = st.columns(4)
        acct = f[0].text_input("Account name", sub.get("account_name", ""))
        uw = f[1].text_input("Underwriter", sub.get("underwriter", ""))
        cur = sub.get("priority", "") or ""
        options = PRIORITIES + ([cur] if cur not in PRIORITIES else [])               # keep an unusual value from the e-mail
        prio = f[2].selectbox("Priority", options, index=options.index(cur))
        tiv = f[3].number_input("TIV reported", min_value=0.0, value=float(sub.get("tiv_reported") or 0.0), step=100000.0, format="%.0f")
        terms = st.text_area("Quote terms / comments", sub.get("comments", ""), height=80)
        if st.form_submit_button("Save details"):
            sub.update({"account_name": acct, "underwriter": uw, "priority": prio, "tiv_reported": tiv or None, "comments": terms})
            rep = p.put("submission", sub, header={"account_name": acct})
            (st.success if rep.ok else st.error)(f"Details {rep.text()}")
    try:
        from .exposure import _email_finder                   # the e-mail search lives with the Microsoft sign-in code
    except Exception:                                         # noqa: BLE001
        return
    _safe(lambda: _email_finder(p, sub), None, "the e-mail search")


def _manage(p, c) -> None:
    with st.expander("Manage this account"):
        d = c.store.describe()
        st.caption(f"Account {p.id}  ·  saved to " + ", ".join(f"`{r}`" for r in d["writable"]))
        b = st.columns([1.5, 1.5, 4])
        if b[0].button("Check saved files", key="ov_check_files", help="Verifies every saved file against its checksum."):
            rep = c.store.check_account(p.id)
            (st.success if not rep["damaged"] else st.error)(
                f"{len(rep['ok'])} file(s) verified" + (f"; damaged: {', '.join(rep['damaged'])}" if rep["damaged"] else ""))
        if b[1].button("Delete this account", key="ov_delete"):
            st.session_state["ov_confirm_delete"] = p.id
        if st.session_state.get("ov_confirm_delete") == p.id:
            st.warning("Delete this account and all its saved files (locations, results, reports)? Nothing in RMS is touched, "
                       "and opening the EDM again starts a fresh account.", icon=":material/delete:")
            y, n, _s = st.columns([1.3, 1, 4])
            if y.button("Yes, delete it", key="ov_delete_yes", type="primary"):
                c.store.delete_account(p.id)
                go_home()
                st.rerun()
            if n.button("Cancel", key="ov_delete_no"):
                st.session_state.pop("ov_confirm_delete", None)
                st.rerun()


# ---------------------------------------------------------------- the page
def render() -> None:
    p = need_account()
    if not p:
        return
    c = ctx()
    sub = p.submission()
    manifest = c.store.manifest(p.id) or {}
    parts = manifest.get("parts", {})
    _header(p, sub, manifest)
    ports = p.portfolios()
    rec = _safe(p.exposure_check, None, "the locations") if "locations" in parts else None
    has_results = "results" in parts
    rev = _safe(p.review, None, "the review")
    _numbers(rec, ports, rev, has_results)
    _checklist(_safe(lambda: _status_rows(p, parts, rec, ports), [], "the account status"))
    _find_results(p, parts)
    _snapshot(rev, has_results)
    _details(p, sub)
    _manage(p, c)
