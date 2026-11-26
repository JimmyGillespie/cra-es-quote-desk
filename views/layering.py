"""Layers step - Dynamic layering: add a layer to this EDM by copying a portfolio and its accounts, renaming them and
setting the layer terms (Part of, Excess of, Blanket limit %). Nothing is created until you press the button."""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from cra.layering import (LayerBuilder, RunStore, apply_edits, build_plans, money, new_run, read_base, terms_from_text,
                          validate_plan)
from cra.rms.layer_api import DEFAULT_COPY_SETTINGS, copy_body, make_api
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


def _plan_cards(p, d, state, settings_ok: bool) -> list:
    """Show every planned layer with editable names / numbers; returns the plans after the user's edits and re-validation."""
    plans, sig = state["plans"], state["sig"]
    claimed_pn, claimed_an, claimed_labels = [], [], []
    for i, plan in enumerate(plans):
        seed = state["seeds"][i]
        with st.container(border=True):
            t = plan["terms"]
            st.markdown(f"**{plan['layer']}**   Part of {money(t['limit'])}  ·  Excess of {money(t['attachment'])}  ·  "
                        f"Blanket limit {t['blanket_pct']:g}%".replace("$", "\\$"))      # a bare $ would start a maths formula
            ed = st.data_editor(pd.DataFrame(seed), hide_index=True, width="stretch", key=f"{_k(p, 'names')}_{sig}_{i}",
                                disabled=["Item", "Now", "Current number"], num_rows="fixed",
                                column_config={"New name": st.column_config.TextColumn("New name"),
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
                st.caption("Layer terms that will be set on the copied policies (before → after)")
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
        with st.expander(f"{plan['layer']}  -  {STATUS_TEXT.get(run['status'], run['status'])}  ({uk_dt(run['created'])})",
                         expanded=run["status"] in ("FAILED", "PENDING")):
            _steps_table(run)
            if run["error"]:
                st.error(run["error"], icon=":material/error:")
            if run["status"] == "PENDING":
                st.info("RMS has not finished the copy yet. Press Resume to keep waiting.", icon=":material/hourglass_top:")
            if run.get("verify"):
                bad = [c for c in run["verify"]["checks"] if not c["ok"]]
                if not bad:
                    st.caption(f"Read back from RMS: {len(run['verify']['checks'])} checks passed.")
            with st.expander("Details"):
                st.caption(f"Portfolio: {plan['portfolio']['name']} / {plan['portfolio']['number']}   |   Run {run['id']}")
                if run["copy"].get("body"):
                    st.code(json.dumps(run["copy"]["body"], indent=2), language="json")
                st.code("\n".join(run["log"][-40:]) or "(no log yet)", language=None)
            b1, b2, b3 = st.columns([1.2, 1.2, 4])
            if run["status"] in ("FAILED", "PENDING", "RUNNING", "PLANNED"):
                if b1.button("Resume", type="primary", key=f"lay_resume_{run['id']}"):
                    cb, done = progress_cb()
                    LayerBuilder(api, eid, store.save).advance(run, cb)
                    done()
                    if run["status"] == "DONE":
                        _refresh_portfolios(p)
                        _forget_base(p)
                    st.rerun()
            elif run["status"] == "DONE":
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
            "Add a layer without importing anything: the app copies a portfolio together with its accounts, renames them, and "
            "sets Part of, Excess of and Blanket limit on the copied policies. Nothing is created until you press Create.")
    if not need_rms():
        return
    api = make_api(c.svc, c.demo)
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
                           key=_k(p, "basepick"), help="Usually the full-value portfolio (e.g. TIVxs0). Its accounts are duplicated.")
    c2.markdown("<div style='height:1.7rem'></div>", unsafe_allow_html=True)
    refresh = c2.button("Re-read", key=_k(p, "reread"), help="Read this portfolio again from RMS")
    d = _base_data(api, eid, base_id, p, refresh=refresh)
    if d is None:
        _history(p, api, eid)
        return
    _base_table(d)
    existing = ", ".join(x["layer"] for x in ports)
    st.caption(f"Layers already in this EDM: {existing}")

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
            plans = build_plans(terms, d["base"], d["accounts"], d["policies"], d["portfolios"], d["account_numbers"])
            seeds = []
            for plan in plans:
                s = [{"Item": "Portfolio", "Now": plan["base"]["name"], "Current number": plan["base"]["number"],
                      "New name": plan["portfolio"]["name"], "New number": plan["portfolio"]["number"]}]
                s += [{"Item": f"Account ({a['locations']} locations)", "Now": a["old_name"], "Current number": a["old_number"],
                       "New name": a["new_name"], "New number": a["new_number"]} for a in plan["accounts"]]
                seeds.append(s)
            prev = st.session_state.get(plans_key)
            st.session_state[plans_key] = {"plans": plans, "seeds": seeds, "base": base_id, "sig": (prev["sig"] + 1) if prev else 1}
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
        plans = _plan_cards(p, d, state, settings_ok)
        with st.expander("Advanced: the copy request sent to RMS"):
            st.caption("Settings added to the copy job. linkExistingAccounts must stay false so the accounts are duplicated "
                       "instead of shared. If RMS rejects the request, adjust the keys here to match your tenant's copy API.")
            st.text_area("Copy job settings (JSON)", value=txt, key=_k(p, "settings"), height=90)
            if not settings_ok:
                st.error("That is not a valid JSON object.")
            elif plans:
                st.code(json.dumps(copy_body(eid, base_id, plans[0]["portfolio"]["name"], plans[0]["portfolio"]["number"], settings),
                                   indent=2), language="json")
        ready = bool(plans) and all(x["ok"] for x in plans) and settings_ok
        n_acc = sum(len(x["accounts"]) for x in plans)
        sure = st.checkbox(f"Create {len(plans)} layer(s) in RMS: this copies {len(plans)} portfolio(s) and {n_acc} account(s) "
                           f"in {sub['exposure_name']}", key=f"{_k(p, 'sure')}_{state['sig']}", disabled=not ready)
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
