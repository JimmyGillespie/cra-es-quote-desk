"""Run every covered peril: the panel at the top of the Analyses page.

It reads the policies of each layer to work out which perils are covered, shows them as a layers x perils grid (already ticked),
and ONE button runs the lot: submit -> wait -> find -> group -> pull the results. It keeps going while you watch, and picks up
again if you close the page. If the perils cannot be worked out, the grid starts empty, you tick them yourself, and the
'What the policies say' box shows exactly what RMS returned so the reading can be fixed.
"""
from __future__ import annotations

import hashlib

import pandas as pd
import streamlit as st

from cra.autorun import STAGES, AutoRunner, new_state, progress as run_progress
from cra.perils import PERIL_LABEL, PERIL_ORDER, derive_perils, describe
from cra.rms.layer_api import make_api
from cra.rules.layers import layer_terms

from .common import all_analyses, ctx, goto, section, uk_dt


def _is_full_value(layer: str) -> bool:
    """TIVxs0-style portfolios hold the whole schedule rather than a layer."""
    t = layer_terms(layer)
    return bool(t) and t[0] is None


def _detect(p, api, eid, ports, comments, earlier, available, force: bool) -> dict:
    """{portfolio_id: derive_perils(...) + diagnostics, or {'error': ...}} - read once and kept for the session."""
    key = f"ar_detect_{p.id}"
    sig = [[x["portfolio_id"] for x in ports], sorted(earlier), comments]
    cur = st.session_state.get(key)
    if cur and cur["sig"] == sig and not force:
        return cur["data"]
    data = {}
    with st.spinner("Reading the policies of every layer..."):
        for x in ports:
            try:
                pols = [pol for a in api.list_portfolio_accounts(eid, x["portfolio_id"]) for pol in api.list_policies(eid, a["id"])]
                r = derive_perils(pols, comments, earlier, available)
                r["sample_keys"] = sorted((pols[0].get("raw") or {}).keys())[:30] if pols else []
                data[x["portfolio_id"]] = r
            except Exception as exc:                                   # noqa: BLE001
                data[x["portfolio_id"]] = {"error": f"{type(exc).__name__}: {exc}"}
    st.session_state[key] = {"sig": sig, "data": data}
    return data


def _chooser(p, ports, detect, available) -> list:
    """The layers x perils grid. Returns [{'portfolio_id', 'layer', 'perils'}] for what is ticked."""
    codes = [c for c in PERIL_ORDER if c in available]
    focus = st.session_state.get("_autorun_focus")
    others = len(ports) > 1
    rows = []
    for x in ports:
        lay = x["layer"]
        r = detect.get(x["portfolio_id"], {})
        found = [] if "error" in r else r.get("codes", [])
        skip = (not focus and others and _is_full_value(lay)) or (focus is not None and lay not in focus)
        row = {"Layer": lay}
        row.update({PERIL_LABEL[c]: (c in found and not skip) for c in codes})
        row["Found from"] = f"could not read the policies: {r['error'][:90]}" if "error" in r else describe(r)
        rows.append(row)
    sig = hashlib.md5(repr([(x["portfolio_id"], detect.get(x["portfolio_id"], {}).get("codes"), sorted(focus or [])) for x in ports]).encode()).hexdigest()[:10]
    cfg = {PERIL_LABEL[c]: st.column_config.CheckboxColumn(PERIL_LABEL[c], width="small") for c in codes}
    ed = st.data_editor(pd.DataFrame(rows), hide_index=True, width="stretch", key=f"ar_grid_{p.id}_{sig}", disabled=["Layer", "Found from"],
                        column_config=cfg, height=min(60 + 35 * len(rows), 360))
    targets = []
    for x, (_i, r) in zip(ports, ed.iterrows()):
        picked = [c for c in codes if bool(r.get(PERIL_LABEL[c]))]
        if picked:
            targets.append({"portfolio_id": x["portfolio_id"], "layer": x["layer"], "perils": picked})
    return targets


def _draw(state: dict) -> None:
    cur, options = state["stage"], state["options"]
    keys = [k for k, _l in STAGES]
    skip = set()
    if not options.get("group"):
        skip |= {"group", "wait_groups", "find2"}
    if not options.get("pull"):
        skip |= {"pull"}
    c = keys.index(cur) if cur in keys else 0
    lines = []
    for k, label in STAGES:
        if k in skip or k == "done":
            continue
        i = keys.index(k)
        mark = "✓" if (state["status"] == "DONE" or i < c) else ("…" if i == c and state["status"] == "RUNNING" else ("✗" if i == c and state["status"] == "FAILED" else "○"))
        lines.append((mark, label))
    st.progress(run_progress(state), text=f"{state['status'].title()}: {dict(STAGES).get(cur, cur)}")
    colour = {"✓": "green", "…": "blue", "✗": "red", "○": "gray"}
    st.markdown("  \n".join(f":{colour[m]}[{m}]&nbsp;&nbsp;{label}" for m, label in lines))
    n_an = sum(len(t["perils"]) for t in state["targets"])
    st.caption(f"{n_an} analyses on {len(state['targets'])} layer(s): " + "; ".join(f"{t['layer']}: {', '.join(PERIL_LABEL.get(x, x) for x in t['perils'])}" for t in state["targets"]))
    if state["waiting"]:
        st.info(state["waiting"] + " - checking every few seconds.", icon=":material/hourglass_top:")
    for msg in state["issues"]:
        st.warning(msg, icon=":material/warning:")
    for msg in state["notes"]:
        st.info(msg, icon=":material/info:")
    if state["error"]:
        st.error(state["error"], icon=":material/error:")
    with st.expander("Log"):
        st.code("\n".join(state["log"]) or "(nothing yet)", language=None)


def _diagnostics(ports, detect) -> None:
    with st.expander("What the policies say (open this if the ticks look wrong)"):
        st.caption("For each layer: how many policies were read, the peril text on them, and the fields a policy carries. "
                   "If a peril is shown here but not ticked above, its wording is not recognised yet - tell me the text and it is added.")
        rows = []
        for x in ports:
            r = detect.get(x["portfolio_id"], {})
            if "error" in r:
                rows.append({"Layer": x["layer"], "Policies": "-", "Peril text on the policies": "(could not read)", "Not recognised": r["error"][:120], "Policy fields": ""})
            else:
                rows.append({"Layer": x["layer"], "Policies": r["n_policies"], "Peril text on the policies": ", ".join(r["policy_texts"]) or "(none)",
                             "Not recognised": ", ".join(r["unmapped"]) or "-", "Policy fields": ", ".join(r.get("sample_keys", []))})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def render_autorun(p) -> None:
    c = ctx()
    section("Run all covered perils", "Works out which perils each layer's policies cover, runs every one, groups them into All Perils and pulls the results. "
                                      "Change the ticks if you want something different, then press the button.")
    if c.svc is None:
        st.info("RMS is not connected, so the auto-run is not available.")
        return
    sub = p.submission()
    eid = sub["exposure_id"]
    ports = p.portfolios()
    if not ports:                                                       # nobody has opened the Exposure or Add layers pages yet: read them now
        with st.spinner("Reading the portfolios of this EDM..."):
            try:
                p.refresh_portfolios()
            except Exception as exc:                                    # noqa: BLE001
                st.error(f"Could not read the portfolios of this EDM from RMS: {exc}")
                return
        ports = p.portfolios()
    if not ports:
        st.info("This EDM has no portfolios, so there is nothing to run. Use Add layers to create one.", icon=":material/info:")
        return
    api = make_api(c.svc, c.demo, sub.get("exposure_name", ""))
    prof = p.profiles()
    available = [x["code"] for x in prof["perils"]]
    runner = AutoRunner(p, lambda: all_analyses(refresh=True))
    state = p.get("autorun")

    if state and state["status"] == "RUNNING":                          # a run is in progress: watch it
        @st.fragment(run_every=8)
        def _live():
            s = runner.advance(p.get("autorun") or state)
            _draw(s)
            if s["status"] != "RUNNING":
                st.rerun()
        _live()
        if st.button("Stop", key=f"ar_stop_{p.id}", help="Stops watching. Anything already submitted keeps running in RMS."):
            runner.cancel(p.get("autorun"))
            st.rerun()
        return

    if state:                                                           # the last run, finished or not
        label = {"DONE": "Finished", "FAILED": "Stopped", "CANCELLED": "Stopped by you"}.get(state["status"], state["status"])
        with st.expander(f"Last run: {label} ({uk_dt(state['updated'])})", expanded=state["status"] in ("DONE", "FAILED")):
            _draw(state)
            b1, b2, _b = st.columns([1.5, 1.5, 4])
            if state["status"] == "DONE" and b1.button("Open the results", type="primary", key=f"ar_open_{p.id}"):
                goto("results")
                st.rerun()
            if state["status"] == "FAILED" and b1.button("Resume", type="primary", key=f"ar_resume_{p.id}"):
                runner.resume(state)
                st.rerun()

    comments = sub.get("comments", "")
    earlier = {a.get("perilCode") for a in p.analyses() if a.get("perilCode") not in ("", "YY", None)}
    force = st.button("Re-read the policies", key=f"ar_reread_{p.id}", help="Reads every layer's policies from RMS again")
    detect = _detect(p, api, eid, ports, comments, earlier, available, force)
    targets = _chooser(p, ports, detect, available)
    _diagnostics(ports, detect)
    if any(r.get("flake") for r in detect.values() if "error" not in r):
        st.info("The underwriter's comments mention FLAKE. Flood and earthquake are run if the policies cover them - check whether they are excluded.", icon=":material/info:")
    unread = {t for r in detect.values() if "error" not in r for t in r.get("unmapped", [])}
    if unread:
        st.warning("Some policy perils could not be recognised: " + ", ".join(sorted(unread)) + ". Tick the perils yourself, and tell me these values so they can be added.",
                   icon=":material/help:")
    if not any(("error" not in r and r.get("codes")) for r in detect.values()):
        st.warning("No perils could be worked out from the policies, so nothing is ticked. Tick the perils you want in the grid above.", icon=":material/help:")
    with st.expander("Options and the model settings used"):
        group = st.toggle("Then group the perils into All Perils", value=True, key=f"ar_group_{p.id}")
        pull = st.toggle("Then pull the results", value=True, key=f"ar_pull_{p.id}")
        st.dataframe(pd.DataFrame([{"Peril": PERIL_LABEL.get(x["code"], x["code"]), "Model profile ID": x.get("model_profile_id"),
                                    "Event rate scheme": x.get("event_rate_scheme_id"), "Your existing analyses use": x.get("reference_profile") or "-"}
                                   for x in prof["perils"]]), hide_index=True, width="stretch")
        st.caption("These IDs come from the old macro and have not been checked against your RMS. After the run, a warning appears if an analysis used a "
                   "different model profile than your existing ones.")
    n_an = sum(len(t["perils"]) for t in targets)
    go = st.button(f"Run {n_an} {'analysis' if n_an == 1 else 'analyses'} on {len(targets)} layer{'s' if len(targets) != 1 else ''}" if targets else "Tick at least one peril above to run",
                   type="primary", disabled=not targets, key=f"ar_go_{p.id}")
    auto = st.session_state.pop("_autorun_go", False)
    if auto and targets:
        go = True
    elif auto:
        st.warning("The perils for the new layers could not be worked out from the policies - tick them above and press Run.", icon=":material/help:")
    if go:
        st.session_state.pop("_autorun_focus", None)
        runner.advance(new_state(targets, {"group": group, "pull": pull}))
        st.rerun()
