"""Layers step - Dynamic layering: add a layer to this EDM by copying the base account(s), creating (or reusing) the layer's
portfolio, attaching the copies, and setting the layer terms (Part of, Excess of, Blanket limit %).
Nothing is created until you tick the box and press Create."""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from cra.layering import (LayerBuilder, RunStore, apply_edits, build_plans, is_current, money, new_run, read_base,
                          reuse_candidate, terms_from_text, validate_plan)
from cra.rms.layer_api import DEFAULT_COPY_SETTINGS, copy_body, make_api, manage_accounts_body, portfolio_create_body
from cra.rules.layers import layer_terms

from .common import ctx, flags, goto, need_account, need_rms, progress_cb, section, uk_dt

ICON = {"done": "✓", "failed": "✗", "running": "…", "pending": "○", "skipped": "-"}
STATUS_TEXT = {"DONE": "Created and checked", "FAILED": "Stopped - see the step that failed", "PENDING": "RMS is still copying",
               "RUNNING": "Running", "PLANNED": "Not started"}


def _k(p, name: str) -> str:
    return f"lay_{name}_{p.id}"


def _base_data(api, eid, base_id, p, refresh: bool = False):
    key = f"{_k(p, 'base')}_{base_id}"
    if refresh or key not in st.session_state:
        with st.spinner("Reading the base portfolio from RMS..."):
            try:
                st.session_state[key] = read_base(api, eid, base_id)
            except Exception as exc:                                   # noqa: BLE001
                st.session_state.pop(key, None)
                st.error(f"Could not read the base portfolio: {exc}")
                return None
    return st.session_state[key]


def _forget_base(p) -> None:
    for k in [k for k in st.session_state if str(k).startswith(_k(p, "base"))]:
        st.session_state.pop(k, None)


def _refresh_portfolios(p) -> None:
    try:
        p.refresh_portfolios()
    except Exception as exc:                                           # noqa: BLE001
        st.warning(f"The layers were created, but the portfolio list could not be refreshed: {exc}")


def _fmt_terms(before, after) -> str:
    b = "-" if before is None else (f"{before:,.0f}" if abs(before) > 1 else f"{before:g}")
    a = f"{after:,.0f}" if abs(after) > 1 else f"{after:g}"
    return f"{b}  →  {a}"


def _fmt_pct(before, after) -> str:
    b = "-" if before is None else f"{before * 100:g}%"
    return f"{b}  →  {after * 100:g}%"


def _base_table(d) -> None:
    rows = []
    for a in d["accounts"]:
        pols = d["policies"].get(a["id"]) or [None]
        for pol in pols:
            rows.append({"Account": a["name"], "Number": a["number"], "Locations": a["locations"],
                         "Part of": money(pol["partOf"]) if pol else "no policy",
                         "Excess of": money(pol["attachment"]) if pol else "-",
                         "Blanket limit": (f"{pol['blanket'] * 100:g}%" if pol and pol["blanket"] is not None else "-")})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=min(40 + 35 * len(rows), 240))


def _seeds(plans: list) -> list:
    seeds = []
    for plan in plans:
        pf = plan["portfolio"]
        if pf.get("reuse_id"):
            s = [{"Item": f"Portfolio (reused, id {pf['reuse_id']})", "Now": pf["name"], "Current number": pf["number"],
                  "New name": pf["name"], "New number": pf["number"]}]
        else:
            s = [{"Item": "Portfolio (new)", "Now": plan["base"]["name"], "Current number": plan["base"]["number"],
                  "New name": pf["name"], "New number": pf["number"]}]
        s += [{"Item": f"Account copy ({a['locations']} locations)", "Now": a["old_name"], "Current number": a["old_number"],
               "New name": a["new_name"], "New number": a["new_number"]} for a in plan["accounts"]]
        seeds.append(s)
    return seeds


def _rebuild(state: dict, d: dict) -> None:
    reuse = [c if (state["reuse"][i] and c and c["reusable"]) else None for i, c in enumerate(state["cands"])]
    state["plans"] = build_plans(state["terms"], d["base"], d["accounts"], d["policies"], d["portfolios"], d["account_numbers"], reuse)
    state["seeds"] = _seeds(state["plans"])
    state["sig"] += 1


def _plan_cards(p, d, state) -> list:
    """Show every planned layer with editable names / numbers; returns the plans after the user's edits and re-validation."""
    plans, sig = state["plans"], state["sig"]
    claimed_pn, claimed_an, claimed_labels = [], [], []
    for i, plan in enumerate(plans):
        cand = state["cands"][i]
        with st.container(border=True):
            t = plan["terms"]
            st.markdown(f"**{plan['layer']}**   Part of {money(t['limit'])}  ·  Excess of {money(t['attachment'])}  ·  "
                        f"Blanket limit {t['blanket_pct']:g}%".replace("$", "\\$"))      # a bare $ would start a maths formula
            if cand:
                held = ", ".join(f"{a['name']} ({a['number']})" for a in cand["accounts"]) or "no accounts"
                if cand["reusable"]:
                    st.info(f"A portfolio for this layer already exists: **{cand['name']}** (id {cand['id']}). It holds: {held}. "
                            "That is what a portfolio copy leaves behind - it shares your ORIGINAL account instead of having its own copy. "
                            "You can reuse it: the copy is attached and the original is unlinked from it (nothing is deleted, and the "
                            "base portfolio keeps its account).", icon=":material/build:")
                    label = "Use a new portfolio instead" if state["reuse"][i] else "Reuse this portfolio"
                    if st.button(label, key=f"{_k(p, 'reuse')}_{sig}_{i}"):
                        state["reuse"][i] = not state["reuse"][i]
                        _rebuild(state, d)
                        st.rerun()
                else:
                    st.warning(f"A portfolio for this layer already exists: {cand['name']} (id {cand['id']}) and it cannot be reused - "
                               f"{cand['why']}.", icon=":material/block:")
            ed = st.data_editor(pd.DataFrame(state["seeds"][i]), hide_index=True, width="stretch", key=f"{_k(p, 'names')}_{sig}_{i}",
                                disabled=["Item", "Now", "Current number"], num_rows="fixed",
                                column_config={"New name": st.column_config.TextColumn("New name", help="Portfolio names: 40 characters at most"),
                                               "New number": st.column_config.TextColumn("New number", help="20 characters at most")})
            rows = ed.to_dict("records")
            apply_edits(plan, {"name": rows[0]["New name"], "number": rows[0]["New number"]},
                        [{"name": r["New name"], "number": r["New number"]} for r in rows[1:]])
            validate_plan(plan, d["portfolios"], d["account_numbers"], claimed_pn, claimed_an, claimed_labels)
            trows = [{"Account": a["new_number"], "Policy": pol["policy_number"] or pol["policy_id"],
                      "Part of": _fmt_terms(pol["before"]["partOf"], pol["after"]["partOf"]),
                      "Excess of": _fmt_terms(pol["before"]["attachment"], pol["after"]["attachment"]),
                      "Blanket limit": _fmt_pct(pol["before"]["blanket"], pol["after"]["blanket"])}
                     for a in plan["accounts"] for pol in a["policies"]]
            if trows:
                st.caption("Layer terms that will be set on the COPIED policies (the base account is not changed): before → after")
                st.dataframe(pd.DataFrame(trows), hide_index=True, width="stretch", height=min(40 + 35 * len(trows), 220))
            flags([("error", e) for e in plan["errors"]] + [("warn", w) for w in plan["warnings"]])
            if plan["ok"]:
                st.success("Ready to create.", icon=":material/check_circle:")
        claimed_pn.append(plan["portfolio"]["number"])
        claimed_an.extend(a["new_number"] for a in plan["accounts"])
        claimed_labels.append(plan["layer"])
    return plans


def _steps_table(run: dict) -> None:
    rows = [{"": ICON.get(s["state"], "?"), "Step": s["label"], "Result": s.get("detail", "")} for s in run["steps"]]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=40 + 35 * len(rows))


def _history(p, api, eid) -> None:
    store = RunStore(p)
    runs = store.runs()
    if not runs:
        return
    section("Layer runs", "Every layer created from this quote, with what each step did. A stopped run can be resumed - nothing is copied twice.")
    for run in reversed(runs):
        plan = run["plan"]
        current = is_current(run)
        with st.expander(f"{plan['layer']}  -  {STATUS_TEXT.get(run['status'], run['status'])}  ({uk_dt(run['created'])})",
                         expanded=run["status"] in ("FAILED", "PENDING") and current):
            if not current:
                st.warning("This run was made with the earlier method (copying the portfolio), which linked your original account. "
                           "It cannot be resumed. Remove it, then create the layer again - if its portfolio is still in RMS, choose "
                           "'Reuse this portfolio' in the preview.", icon=":material/history:")
            else:
                _steps_table(run)
            if run["error"] and current:
                st.error(run["error"], icon=":material/error:")
            if run["status"] == "PENDING" and current:
                st.info("RMS has not finished the copy yet. Press Resume to keep waiting.", icon=":material/hourglass_top:")
            if run.get("verify") and current:
                bad = [c for c in run["verify"]["checks"] if not c["ok"]]
                if not bad:
                    st.caption(f"Read back from RMS: {len(run['verify']['checks'])} checks passed.")
            with st.expander("Details"):
                st.caption(f"Portfolio: {plan['portfolio']['name']} / {plan['portfolio']['number']}   |   Run {run['id']}")
                for base_id, s in (run.get("ids", {}).get("accounts") or {}).items():
                    if s.get("body"):
                        st.caption(f"Copy request for account {base_id}:")
                        st.code(json.dumps(s["body"], indent=2), language="json")
                st.code("\n".join(run["log"][-40:]) or "(no log yet)", language=None)
            b1, b2, b3 = st.columns([1.2, 1.2, 4])
            if current and run["status"] in ("FAILED", "PENDING", "RUNNING", "PLANNED"):
                if b1.button("Resume", type="primary", key=f"lay_resume_{run['id']}"):
                    cb, done = progress_cb()
                    LayerBuilder(api, eid, store.save).advance(run, cb)
                    done()
                    if run["status"] == "DONE":
                        _refresh_portfolios(p)
                        _forget_base(p)
                    st.rerun()
            elif current and run["status"] == "DONE":
                if b1.button("Run analyses", key=f"lay_go_{run['id']}", help="Open the Analyses step - the new layer is in the list."):
                    goto("analyses")
                    st.rerun()
            if b2.button("Remove from list", key=f"lay_drop_{run['id']}", help="Only forgets the record here. Nothing is deleted in RMS."):
                store.drop(run["id"])
                st.rerun()


def render():
    p = need_account()
    if not p:
        return
    c = ctx()
    sub = p.submission()
    eid = sub["exposure_id"]
    section("Dynamic layering",
            "Add a layer without importing anything: the app copies the base account(s), creates the layer's portfolio, attaches the "
            "copies, and sets Part of, Excess of and Blanket limit on the COPIED policies. The base portfolio and its accounts are never "
            "changed. Nothing is created until you press Create.")
    if not need_rms():
        return
    api = make_api(c.svc, c.demo, sub.get("exposure_name", ""))
    ports = p.portfolios()
    if not ports:
        st.info("Load the portfolios on the Exposure step first.", icon=":material/arrow_back:")
        return

    flash = st.session_state.pop(_k(p, "flash"), None)
    if flash and all(status == "DONE" for _l, status in flash):
        st.session_state.pop(_k(p, "editor"), None)               # the layers just created are no longer "to add"
        st.session_state[_k(p, "rows")] = []
    if flash:
        for layer, status in flash:
            (st.success if status == "DONE" else st.warning if status == "PENDING" else st.error)(
                f"{layer}: {STATUS_TEXT.get(status, status)}")

    # ---- 1  base portfolio
    labels = {x["portfolio_id"]: f"{x['layer']}   ({x['portfolio_name']})" for x in ports}
    default = next((i for i, x in enumerate(ports) if (layer_terms(x["layer"]) or (0, 0))[0] is None), 0)
    c1, c2 = st.columns([5, 1])
    base_id = c1.selectbox("Copy from this portfolio", list(labels), format_func=labels.get, index=default,
                           key=_k(p, "basepick"), help="Usually the full-value portfolio (e.g. TIVxs0). Its account(s) are copied.")
    c2.markdown("<div style='height:1.7rem'></div>", unsafe_allow_html=True)
    refresh = c2.button("Re-read", key=_k(p, "reread"), help="Read this portfolio again from RMS")
    d = _base_data(api, eid, base_id, p, refresh=refresh)
    if d is None:
        _history(p, api, eid)
        return
    _base_table(d)
    st.caption(f"Layers already in this EDM: {', '.join(x['layer'] for x in ports)}")

    # ---- 2  layers to add
    section("Layers to add", "Type each layer as you write it - 15Mxs10M, 15x10 or 15M xs 10M - and set the blanket limit if it is not 100%.")
    seed_rows = st.session_state.get(_k(p, "rows")) or [{"Layer": "", "Blanket limit (%)": 100.0}]
    entered = st.data_editor(pd.DataFrame(seed_rows), num_rows="dynamic", hide_index=True, width="stretch", key=_k(p, "editor"),
                             column_config={"Layer": st.column_config.TextColumn("Layer", help="Part of xs Excess of, e.g. 15Mxs10M"),
                                            "Blanket limit (%)": st.column_config.NumberColumn(min_value=0.0, max_value=100.0, step=1.0,
                                                                                                format="%.2f")})
    plans_key = _k(p, "plans")
    if st.button("Preview", type="primary", key=_k(p, "preview")):
        rows = [r for r in entered.to_dict("records") if str(r.get("Layer") or "").strip()]
        terms, bad = [], []
        for r in rows:
            t = terms_from_text(r["Layer"], r.get("Blanket limit (%)") if r.get("Blanket limit (%)") not in (None, "") else 100.0)
            (terms.append(t) if t else bad.append(str(r["Layer"])))
        if bad:
            st.error("These are not layers I can read: " + ", ".join(f"'{b}'" for b in bad)
                     + ". Use the form 15Mxs10M, 15x10 or 15M xs 10M.")
        if not terms:
            st.session_state.pop(plans_key, None)
            if not bad:
                st.info("Enter at least one layer.")
        else:
            base_ids = [a["id"] for a in d["accounts"]]
            try:
                cands = [reuse_candidate(api, eid, d["portfolios"], t.label, base_id, base_ids) for t in terms]
            except Exception as exc:                                   # noqa: BLE001
                st.error(f"Could not check the existing portfolios: {exc}")
                cands = None
            if cands is not None:
                prev = st.session_state.get(plans_key)
                state = {"terms": terms, "cands": cands, "reuse": [False] * len(terms), "base": base_id, "sig": prev["sig"] if prev else 0}
                _rebuild(state, d)
                st.session_state[plans_key] = state
    state = st.session_state.get(plans_key)
    if state and state["base"] != base_id:
        st.session_state.pop(plans_key, None)
        state = None

    # ---- 3  preview and create
    if state:
        section("Preview", "Check the names and numbers - you can change any of them here - then create.")
        txt = st.session_state.get(_k(p, "settings"), json.dumps(DEFAULT_COPY_SETTINGS))
        settings_ok, settings = True, dict(DEFAULT_COPY_SETTINGS)
        try:
            settings = json.loads(txt)
            settings_ok = isinstance(settings, dict)
        except ValueError:
            settings_ok = False
        plans = _plan_cards(p, d, state)
        with st.expander("Advanced: the requests sent to RMS"):
            st.caption("1) one copy job per account  2) create the portfolio (skipped when reusing)  3) attach the copies. Extra copy settings "
                       "(JSON) are added to every copy job - leave {} unless RMS asks for more.")
            st.text_area("Extra copy job settings (JSON)", value=txt, key=_k(p, "settings"), height=70)
            if not settings_ok:
                st.error("That is not a valid JSON object.")
            elif plans and plans[0]["accounts"]:
                pl, a0 = plans[0], plans[0]["accounts"][0]
                st.code("POST /platform/copy/v1/jobs\n" + json.dumps(copy_body(eid, "ACCOUNT", a0["base_id"], a0["new_name"], a0["new_number"], settings), indent=2)
                        + ("" if pl["portfolio"].get("reuse_id") else "\n\nPOST /platform/riskdata/v1/exposures/<EDM>/portfolios\n"
                           + json.dumps(portfolio_create_body(pl["portfolio"]["name"], pl["portfolio"]["number"], pl["portfolio"].get("description", "")), indent=2))
                        + "\n\nPATCH /platform/riskdata/v1/exposures/<EDM>/portfolios/<portfolio>/accounts\n"
                        + json.dumps(manage_accounts_body(["<new account>"], ["<original, only if linked>"] if pl["portfolio"].get("reuse_id") else []), indent=2),
                        language="json")
        ready = bool(plans) and all(x["ok"] for x in plans) and settings_ok
        n_acc = sum(len(x["accounts"]) for x in plans)
        n_new = sum(1 for x in plans if not x["portfolio"].get("reuse_id"))
        sure = st.checkbox(f"Create {len(plans)} layer(s) in RMS: this copies {n_acc} account(s) and creates {n_new} portfolio(s) "
                           f"({len(plans) - n_new} reused) in {sub['exposure_name']}", key=f"{_k(p, 'sure')}_{state['sig']}", disabled=not ready)
        if st.button("Create layers", type="primary", key=_k(p, "create"), disabled=not (ready and sure)):
            store = RunStore(p)
            builder = LayerBuilder(api, eid, store.save)
            cb, done = progress_cb()
            results = []
            for i, plan in enumerate(plans):
                run = new_run(json.loads(json.dumps(plan)), settings)
                builder.advance(run, lambda f, m, i=i, name=plan["layer"]: cb((i + f) / len(plans), f"{name}: {m}"))
                results.append((plan["layer"], run["status"]))
            done()
            if any(s == "DONE" for _l, s in results):
                _refresh_portfolios(p)
            st.session_state.pop(plans_key, None)
            _forget_base(p)
            st.session_state[_k(p, "flash")] = results
            st.rerun()

    _history(p, api, eid)
