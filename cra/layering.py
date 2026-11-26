"""Dynamic layering: add a layer to an EDM (for example 15M xs 10M) without importing anything.

    pick a base portfolio -> copy it WITH its accounts -> rename the portfolio (name + number) -> rename each copied
    account (name + number) -> set the layer terms on the copied policies (Part of, Excess of, Blanket limit %)
    -> read everything back and compare with the plan

The terms map to the ACC columns your EQ app writes: Part of = PARTOF, Excess of = UNDCOVAMT (the policy's
attachmentPoint), Blanket limit = BLANLIMAMT (a fraction: 100% = 1.0).

No Streamlit in here. Planning is pure; the builder talks to an api object (cra/rms/layer_api.py, or the in-memory demo)
and saves the run after every step, so a closed browser or a failed call never leaves the work in an unknown state.
"""
from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from .rms.layer_api import JOB_BAD, JOB_DONE, LayerApiError
from .rules.layers import layer_from_portfolio_name, layer_key, layer_terms

MAX_NUMBER = 20                  # v12 truncates portfolio numbers to 20; accounts follow the same rule
LONG_NAME = 60

STEPS = [("snapshot", "Read the EDM"), ("copy", "Copy the portfolio and its accounts"), ("resolve", "Find the new portfolio"),
         ("portfolio", "Rename the portfolio"), ("accounts", "Rename the accounts"),
         ("policies", "Set the layer terms"), ("verify", "Read it all back and check")]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- amounts and layer labels
def parse_amount(v):
    """'15M' -> 15000000, '2.5m', '500k', '15,000,000', 15000000 -> float; anything else -> None."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if v == v else None
    t = str(v).strip().replace("$", "").replace(",", "").replace(" ", "")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([kKmMbB]?)", t)
    if not m:
        return None
    return float(m.group(1)) * {"": 1.0, "k": 1e3, "m": 1e6, "b": 1e9}[m.group(2).lower()]


def fmt_label_amount(v: float) -> str:
    """The spelling used in your portfolio names: 10M, 25M, 2.5M, 500K, and a bare 0."""
    v = float(v)
    if v == 0:
        return "0"
    if v >= 1e6 and abs(v / 1e5 - round(v / 1e5)) < 1e-9:
        return f"{v / 1e6:g}M"
    if abs(v / 1e3 - round(v / 1e3)) < 1e-9:
        return f"{int(round(v / 1e3))}K"
    return str(int(round(v)))


def layer_label(limit: float, attachment: float) -> str:
    return f"{fmt_label_amount(limit)}xs{fmt_label_amount(attachment)}"


def money(v) -> str:
    return "-" if v is None else f"${v:,.0f}"


@dataclass
class LayerTerms:
    limit: float                 # Part of
    attachment: float            # Excess of
    blanket_pct: float = 100.0   # Blanket limit, percent

    @property
    def label(self) -> str:
        return layer_label(self.limit, self.attachment)

    @property
    def blanket_value(self) -> float:
        return round(self.blanket_pct / 100.0, 10)

    def problems(self) -> list:
        out = []
        if not self.limit or self.limit <= 0:
            out.append("Part of (the layer limit) must be greater than 0.")
        if self.attachment is None or self.attachment < 0:
            out.append("Excess of (the attachment) cannot be negative.")
        if not self.blanket_pct or self.blanket_pct <= 0 or self.blanket_pct > 100:
            out.append("Blanket limit must be more than 0% and at most 100%.")
        return out


def terms_from_text(text, blanket_pct=100.0):
    """'15Mxs10M', '15x10', '15M xs 10M' -> LayerTerms, or None when it is not a layer with a limit."""
    t = layer_terms(str(text or "").strip())
    if t is None or t[0] is None:
        return None
    try:
        pct = float(blanket_pct)
    except (TypeError, ValueError):
        pct = 100.0
    return LayerTerms(limit=t[0], attachment=t[1], blanket_pct=pct)


# ---------------------------------------------------------------- names and numbers
def _lc(s) -> str:
    return (s or "").strip().lower()


def _idkey(v):
    try:
        return (0, int(float(v)))
    except (TypeError, ValueError):
        return (1, str(v))


def strip_layer_prefix(s: str) -> str:
    """'10Mxs0_Ben Davis' -> 'Ben Davis' (only when the first token really is a layer)."""
    s = (s or "").strip()
    if "_" in s:
        head, tail = s.split("_", 1)
        if tail and layer_terms(head) is not None:
            return tail
    return s


def name_for(old: str, label: str) -> str:
    return f"{label}_{strip_layer_prefix(old)}"


def number_for(old: str, label: str, maxlen: int = MAX_NUMBER) -> str:
    core = strip_layer_prefix(old)
    cand = f"{label}_{core}" if core and core != label else label
    if len(cand) <= maxlen:
        return cand
    room = maxlen - len(label) - 1
    return f"{label}_{core[:room]}" if room > 0 else label[:maxlen]


def unique(value: str, taken: set, maxlen: int = MAX_NUMBER) -> str:
    cand, n = value[:maxlen], 2
    while _lc(cand) in taken:
        suffix = f"-{n}"
        cand = value[:maxlen - len(suffix)] + suffix
        n += 1
    return cand


def read_base(api, exposure_id, base_id) -> dict:
    """Everything the plan needs from RMS about the base portfolio and the EDM it sits in."""
    portfolios = api.list_portfolios(exposure_id)
    base = next((p for p in portfolios if p["id"] == str(base_id)), None)
    if base is None:
        raise StepFailed("The base portfolio was not found in this EDM.")
    accounts = api.list_portfolio_accounts(exposure_id, base_id)
    policies = {a["id"]: api.list_policies(exposure_id, a["id"]) for a in accounts}
    numbers = {a["number"] for a in api.list_accounts(exposure_id)}
    return {"portfolios": portfolios, "base": base, "accounts": accounts, "policies": policies, "account_numbers": numbers}


# ---------------------------------------------------------------- the plan
def build_plan(terms: LayerTerms, base: dict, base_accounts: list, base_policies: dict, portfolios: list,
               account_numbers, claimed_portfolio_numbers=(), claimed_account_numbers=()) -> dict:
    """What will be created, with every name, number and term before -> after. Pure: no RMS calls.
    base: {id,name,number}; base_accounts: [{id,name,number,locations}]; base_policies: {account_id: [norm_policy]}."""
    label = terms.label
    taken_pn = {_lc(p["number"]) for p in portfolios} | {_lc(x) for x in claimed_portfolio_numbers}
    taken_an = {_lc(n) for n in account_numbers} | {_lc(x) for x in claimed_account_numbers}
    pf_number = unique(number_for(base.get("number") or base["name"], label), taken_pn)
    accounts = []
    for a in sorted(base_accounts, key=lambda x: _idkey(x["id"])):
        num = unique(number_for(a["number"] or a["name"], label), taken_an)
        taken_an.add(_lc(num))
        pols = [{"policy_id": p["id"], "policy_number": p["number"],
                 "before": {"partOf": p["partOf"], "attachment": p["attachment"], "blanket": p["blanket"]},
                 "after": {"partOf": terms.limit, "attachment": terms.attachment, "blanket": terms.blanket_value}}
                for p in base_policies.get(a["id"], [])]
        accounts.append({"base_id": a["id"], "old_name": a["name"], "old_number": a["number"],
                         "new_name": name_for(a["name"] or a["number"], label), "new_number": num,
                         "locations": a.get("locations", 0), "policies": pols})
    plan = {"layer": label, "terms": {"limit": terms.limit, "attachment": terms.attachment, "blanket_pct": terms.blanket_pct},
            "base": {"id": base["id"], "name": base["name"], "number": base.get("number", "")},
            "portfolio": {"name": name_for(base["name"], label), "number": pf_number}, "accounts": accounts,
            "errors": [], "warnings": [], "ok": False}
    return validate_plan(plan, portfolios, account_numbers, claimed_portfolio_numbers, claimed_account_numbers)


def validate_plan(plan: dict, portfolios: list, account_numbers, claimed_portfolio_numbers=(),
                  claimed_account_numbers=(), claimed_labels=()) -> dict:
    """Recompute errors / warnings (also after the user edits a name or number). Returns the same dict."""
    t = plan["terms"]
    terms = LayerTerms(t["limit"], t["attachment"], t["blanket_pct"])
    errs, warns = list(terms.problems()), []
    label = plan["layer"]
    existing = {layer_key(layer_from_portfolio_name(p["name"])): p["name"] for p in portfolios}
    if layer_key(label) in existing:
        errs.append(f"A portfolio for layer {label} already exists in this EDM ({existing[layer_key(label)]}).")
    if layer_key(label) in {layer_key(x) for x in claimed_labels}:
        errs.append(f"Layer {label} is entered twice.")
    if not plan["accounts"]:
        errs.append("The base portfolio has no accounts, so there is nothing to copy.")
    pf = plan["portfolio"]
    if not pf["name"].strip():
        errs.append("The portfolio name is empty.")
    if not pf["number"].strip():
        errs.append("The portfolio number is empty.")
    if len(pf["number"]) > MAX_NUMBER:
        errs.append(f"The portfolio number '{pf['number']}' is longer than {MAX_NUMBER} characters.")
    if _lc(pf["number"]) in {_lc(p["number"]) for p in portfolios} | {_lc(x) for x in claimed_portfolio_numbers}:
        errs.append(f"The portfolio number '{pf['number']}' is already used.")
    if _lc(pf["name"]) in {_lc(p["name"]) for p in portfolios}:
        errs.append(f"The portfolio name '{pf['name']}' is already used.")
    if len(pf["name"]) > LONG_NAME:
        warns.append(f"The portfolio name is {len(pf['name'])} characters long - RMS fields can have length limits.")
    used = {_lc(n) for n in account_numbers} | {_lc(x) for x in claimed_account_numbers}
    seen = set()
    for a in plan["accounts"]:
        who = a["old_name"] or a["old_number"] or a["base_id"]
        if not a["new_name"].strip():
            errs.append(f"Account {who}: the new name is empty.")
        if not a["new_number"].strip():
            errs.append(f"Account {who}: the new number is empty.")
        if len(a["new_number"]) > MAX_NUMBER:
            errs.append(f"Account {who}: the number '{a['new_number']}' is longer than {MAX_NUMBER} characters.")
        if _lc(a["new_number"]) in used:
            errs.append(f"Account {who}: the number '{a['new_number']}' is already used in this EDM.")
        if _lc(a["new_number"]) in seen:
            errs.append(f"Account {who}: the number '{a['new_number']}' is used twice in this plan.")
        seen.add(_lc(a["new_number"]))
        if not a["policies"]:
            errs.append(f"Account {who} has no policy, so there is nowhere to set the layer terms.")
        for p in a["policies"]:
            b = p["before"]
            if b["partOf"] == terms.limit and b["attachment"] == terms.attachment and b["blanket"] == terms.blanket_value:
                warns.append(f"Account {who}: the base policy already has these terms.")
    plan["errors"], plan["warnings"], plan["ok"] = errs, warns, not errs
    return plan


def build_plans(terms_list: list, base, base_accounts, base_policies, portfolios, account_numbers) -> list:
    """Several layers at once: each plan is checked against the EDM and against the plans before it."""
    plans, cp, ca, labels = [], [], [], []
    for t in terms_list:
        p = build_plan(t, base, base_accounts, base_policies, portfolios, account_numbers, cp, ca)
        validate_plan(p, portfolios, account_numbers, cp, ca, labels)
        plans.append(p)
        cp.append(p["portfolio"]["number"])
        ca.extend(a["new_number"] for a in p["accounts"])
        labels.append(p["layer"])
    return plans


def apply_edits(plan: dict, portfolio_edit: dict | None, account_edits: list | None) -> dict:
    """User edits from the preview table: portfolio_edit {'name','number'}; account_edits in plan order [{'name','number'}]."""
    if portfolio_edit:
        plan["portfolio"]["name"] = str(portfolio_edit.get("name", plan["portfolio"]["name"]) or "").strip()
        plan["portfolio"]["number"] = str(portfolio_edit.get("number", plan["portfolio"]["number"]) or "").strip()
    for a, e in zip(plan["accounts"], account_edits or []):
        a["new_name"] = str(e.get("name", a["new_name"]) or "").strip()
        a["new_number"] = str(e.get("number", a["new_number"]) or "").strip()
    return plan


# ---------------------------------------------------------------- runs
class StepFailed(Exception):
    pass


class StepPending(Exception):
    pass


def new_run(plan: dict, settings: dict | None = None) -> dict:
    return {"id": uuid.uuid4().hex[:10], "created": now_iso(), "status": "PLANNED", "plan": plan, "settings": settings or {},
            "steps": [{"key": k, "label": l, "state": "pending", "detail": ""} for k, l in STEPS],
            "ids": {}, "copy": {}, "log": [], "error": "", "verify": None}


class RunStore:
    """Runs are kept with the quote ('layer_runs' part), so they survive a restart and can be resumed."""

    def __init__(self, pipeline):
        self.p = pipeline

    def runs(self) -> list:
        return list((self.p.get("layer_runs", {}) or {}).get("runs", []))

    def save(self, run: dict) -> None:
        d = self.p.get("layer_runs", {}) or {"runs": []}
        runs = d.setdefault("runs", [])
        i = next((k for k, r in enumerate(runs) if r["id"] == run["id"]), None)
        if i is None:
            runs.append(run)
        else:
            runs[i] = run
        rep = self.p.put("layer_runs", d)
        if not rep.ok:
            raise OSError("The layer run could not be saved: " + rep.text())

    def drop(self, run_id: str) -> None:
        d = self.p.get("layer_runs", {}) or {"runs": []}
        d["runs"] = [r for r in d.get("runs", []) if r["id"] != run_id]
        self.p.put("layer_runs", d)


def _close(a, b, tol=1e-6) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) <= tol * max(1.0, abs(float(b)))


class LayerBuilder:
    """Runs a plan against RMS one step at a time. Safe to call advance() again after any failure or timeout: finished
    steps are skipped, an earlier copy is adopted instead of copied twice, and nothing is renamed unless the copied
    accounts are genuinely new accounts."""

    def __init__(self, api, exposure_id, save: Callable[[dict], None], *, sleep=time.sleep, clock=time.time,
                 poll_every: float = 3.0, poll_timeout: float = 300.0):
        self.api, self.eid, self.save = api, str(exposure_id), save
        self.sleep, self.clock, self.poll_every, self.poll_timeout = sleep, clock, poll_every, poll_timeout

    def _log(self, run, msg):
        run["log"].append(f"{now_iso()[11:19]}  {msg}")
        del run["log"][:-200]

    # ------------------------------------------------------------ driver
    def advance(self, run: dict, progress: Callable[[float, str], None] | None = None) -> dict:
        run["status"], run["error"] = "RUNNING", ""
        self.save(run)
        fns = {"snapshot": self._snapshot, "copy": self._copy, "resolve": self._resolve, "portfolio": self._portfolio,
               "accounts": self._accounts, "policies": self._policies, "verify": self._verify}
        n = len(run["steps"])
        for i, st in enumerate(run["steps"]):
            if st["state"] in ("done", "skipped"):
                continue
            st["state"], st["started"] = "running", now_iso()
            self.save(run)
            if progress:
                progress(i / n, st["label"])
            try:
                st["detail"] = fns[st["key"]](run, progress) or ""
            except StepPending as exc:
                st["detail"], run["status"] = str(exc), "PENDING"
                self._log(run, f"{st['label']}: {exc}")
                self.save(run)
                return run
            except (StepFailed, LayerApiError) as exc:
                self._fail(run, st, str(exc))
                return run
            except Exception as exc:                                   # noqa: BLE001
                self._fail(run, st, f"{type(exc).__name__}: {exc}")
                return run
            st["state"], st["ended"] = "done", now_iso()
            self._log(run, f"{st['label']}: {st['detail']}")
            self.save(run)
        run["status"] = "DONE"
        self.save(run)
        if progress:
            progress(1.0, "Done")
        return run

    def _fail(self, run, st, msg):
        st["state"], st["detail"], st["ended"] = "failed", msg, now_iso()
        run["status"], run["error"] = "FAILED", msg
        self._log(run, f"{st['label']} FAILED: {msg}")
        self.save(run)

    # ------------------------------------------------------------ steps
    def _snapshot(self, run, progress):
        plan = run["plan"]
        pf = self.api.list_portfolios(self.eid)
        ids = {p["id"] for p in pf}
        if plan["base"]["id"] not in ids:
            raise StepFailed("The base portfolio no longer exists in this EDM.")
        for p in pf:
            if _lc(p["name"]) == _lc(plan["portfolio"]["name"]) or _lc(p["number"]) == _lc(plan["portfolio"]["number"]):
                raise StepFailed(f"A portfolio named '{p['name']}' (number {p['number']}) already exists. "
                                 "Resume the earlier run for it, or change the name / number.")
        accts = self.api.list_portfolio_accounts(self.eid, plan["base"]["id"])
        if len(accts) != len(plan["accounts"]):
            raise StepFailed(f"The base portfolio now has {len(accts)} account(s) but the plan was made for "
                             f"{len(plan['accounts'])}. Build the preview again.")
        run["ids"]["before"] = [p["id"] for p in pf]
        run["ids"]["base_accounts"] = sorted((a["id"] for a in accts), key=_idkey)
        return f"{len(pf)} portfolios in the EDM; the base has {len(accts)} account(s)"

    def _copy(self, run, progress):
        plan, cp = run["plan"], run["copy"]
        ids = run["ids"]
        if ids.get("portfolio_id"):
            return "already found"
        if not cp.get("job_url"):
            for p in self.api.list_portfolios(self.eid):                  # a crashed earlier attempt?
                if p["id"] not in set(ids["before"]) and (_lc(p["name"]) == _lc(plan["portfolio"]["name"])
                                                          or _lc(p["number"]) == _lc(plan["portfolio"]["number"])):
                    ids["portfolio_id"], cp["adopted"] = p["id"], True
                    return f"found the portfolio from an earlier attempt (id {p['id']}) - not copied again"
            res = self.api.submit_copy_job(self.eid, plan["base"]["id"], plan["portfolio"]["name"],
                                           plan["portfolio"]["number"], run.get("settings") or {})
            cp["body"], cp["http"] = res["body"], res["http"]
            if not res["ok"]:
                raise StepFailed(f"RMS did not accept the copy request (HTTP {res['http']}): {res['text'][:500]}")
            cp["job_url"], cp["submitted"] = res["job_url"], now_iso()
            self.save(run)
        t0, bad = self.clock(), 0
        while True:
            st = self.api.poll_job(cp["job_url"])
            cp["status"], cp["raw"] = st["status"], st["raw"]
            if st["status"] in JOB_DONE:
                self.save(run)
                return f"copy job {st['status'].lower()}"
            if st["status"] in JOB_BAD:
                raise StepFailed(f"The copy job ended as {st['status']}: {st['raw'][:500]}")
            bad = bad + 1 if not 200 <= st["http"] < 300 else 0
            if bad >= 5:
                raise StepFailed(f"The copy job could not be read (HTTP {st['http']}): {st['raw'][:300]}")
            if self.clock() - t0 > self.poll_timeout:
                self.save(run)
                raise StepPending(f"RMS is still copying (status {st['status']}). Press Resume to keep waiting.")
            if progress:
                progress(0.2, f"Copy job {st['status'].lower()}...")
            self.sleep(self.poll_every)

    def _resolve(self, run, progress):
        ids, plan, cp = run["ids"], run["plan"], run["copy"]
        pf = self.api.list_portfolios(self.eid)
        by_id = {p["id"]: p for p in pf}
        if ids.get("portfolio_id"):
            if ids["portfolio_id"] not in by_id:
                raise StepFailed("The portfolio found earlier no longer exists.")
            return f"portfolio {ids['portfolio_id']}"
        news = [p for p in pf if p["id"] not in set(ids["before"])]
        hint = self.api.job_new_portfolio_id(cp.get("raw", ""), plan["base"]["id"])
        if hint:
            news = [p for p in news if p["id"] == hint] or news
        if len(news) > 1:
            named = [p for p in news if _lc(p["name"]) == _lc(plan["portfolio"]["name"])]
            news = named or news
        if len(news) == 0:
            raise StepFailed("The copy job finished but no new portfolio appeared in the EDM.")
        if len(news) > 1:
            raise StepFailed("More than one new portfolio appeared (" + ", ".join(f"{p['name']} #{p['id']}" for p in news)
                             + "). Check RMS, then resume.")
        ids["portfolio_id"] = news[0]["id"]
        return f"new portfolio '{news[0]['name']}' (id {news[0]['id']})"

    def _portfolio(self, run, progress):
        pid, want = run["ids"]["portfolio_id"], run["plan"]["portfolio"]
        cur = next((p for p in self.api.list_portfolios(self.eid) if p["id"] == pid), None)
        if cur is None:
            raise StepFailed("The new portfolio disappeared.")
        if cur["name"] == want["name"] and cur["number"] == want["number"]:
            return "already named correctly"
        res = self.api.update_portfolio(self.eid, pid, want["name"], want["number"])
        if not res["ok"]:
            raise StepFailed(f"RMS did not accept the portfolio rename (HTTP {res['http']}): {res['text'][:400]}")
        return f"{want['name']} / {want['number']}"

    def _accounts(self, run, progress):
        ids, plan = run["ids"], run["plan"]
        new = sorted(self.api.list_portfolio_accounts(self.eid, ids["portfolio_id"]), key=lambda a: _idkey(a["id"]))
        base_ids = set(ids["base_accounts"])
        if any(a["id"] in base_ids for a in new):
            raise StepFailed("SAFETY STOP: the new portfolio is linked to the ORIGINAL accounts, not to copies of them, so "
                             "nothing was renamed. Set linkExistingAccounts to false in the advanced settings, remove the "
                             "new portfolio in RMS, and try again.")
        pl = sorted(plan["accounts"], key=lambda a: _idkey(a["base_id"]))
        if len(new) != len(pl):
            raise StepFailed(f"The copy has {len(new)} account(s) but the base has {len(pl)}. Check the new portfolio in RMS.")
        mapping, renamed = [], 0
        for pa, na in zip(pl, new):
            if pa["locations"] and na["locations"] and pa["locations"] != na["locations"]:
                raise StepFailed(f"Account {pa['old_name']} has {pa['locations']} locations but its copy has {na['locations']}.")
            if na["name"] != pa["new_name"] or na["number"] != pa["new_number"]:
                res = self.api.update_account(self.eid, na["id"], pa["new_name"], pa["new_number"])
                if not res["ok"]:
                    raise StepFailed(f"RMS did not accept the rename of account {na['id']} (HTTP {res['http']}): {res['text'][:400]}")
                renamed += 1
            mapping.append({"base_id": pa["base_id"], "new_id": na["id"]})
        ids["accounts"] = mapping
        return f"{len(new)} account(s), {renamed} renamed"

    def _policies(self, run, progress):
        t = run["plan"]["terms"]
        want = {"partOf": t["limit"], "attachment": t["attachment"], "blanket": round(t["blanket_pct"] / 100.0, 10)}
        changed = total = 0
        for m in run["ids"]["accounts"]:
            pols = self.api.list_policies(self.eid, m["new_id"])
            if not pols:
                raise StepFailed(f"The copied account {m['new_id']} has no policy to set the layer terms on.")
            for pol in pols:
                total += 1
                if _close(pol["partOf"], want["partOf"]) and _close(pol["attachment"], want["attachment"]) \
                        and _close(pol["blanket"], want["blanket"]):
                    continue
                res = self.api.update_policy_terms(self.eid, pol["id"], want["partOf"], want["attachment"], want["blanket"])
                if not res["ok"]:
                    raise StepFailed(f"RMS did not accept the policy update for policy {pol['id']} (HTTP {res['http']}): "
                                     f"{res['text'][:500]}")
                changed += 1
        return f"{total} polic{'y' if total == 1 else 'ies'}, {changed} updated"

    def _verify(self, run, progress):
        plan, ids = run["plan"], run["ids"]
        checks = []

        def add(label, ok, detail=""):
            checks.append({"label": label, "ok": bool(ok), "detail": detail})

        pf = next((p for p in self.api.list_portfolios(self.eid) if p["id"] == ids["portfolio_id"]), None)
        add("Portfolio name and number", pf and pf["name"] == plan["portfolio"]["name"] and pf["number"] == plan["portfolio"]["number"],
            f"{pf['name']} / {pf['number']}" if pf else "portfolio not found")
        new = {a["id"]: a for a in self.api.list_portfolio_accounts(self.eid, ids["portfolio_id"])}
        t = plan["terms"]
        for m, pa in zip(ids["accounts"], sorted(plan["accounts"], key=lambda a: _idkey(a["base_id"]))):
            a = new.get(m["new_id"])
            add(f"Account {pa['new_number']}", a and a["name"] == pa["new_name"] and a["number"] == pa["new_number"],
                f"{a['name']} / {a['number']}" if a else "account not found")
            pols = self.api.list_policies(self.eid, m["new_id"])
            ok = bool(pols) and all(_close(p["partOf"], t["limit"]) and _close(p["attachment"], t["attachment"])
                                    and _close(p["blanket"], t["blanket_pct"] / 100.0) for p in pols)
            add(f"Terms on account {pa['new_number']}", ok,
                "; ".join(f"Part of {money(p['partOf'])}, Excess of {money(p['attachment'])}, blanket {p['blanket']}" for p in pols)
                or "no policy")
        base_now = {a["id"]: a for a in self.api.list_portfolio_accounts(self.eid, plan["base"]["id"])}
        for pa in plan["accounts"]:
            a = base_now.get(pa["base_id"])
            add(f"Original account {pa['old_number'] or pa['old_name']} untouched",
                a and a["name"] == pa["old_name"] and a["number"] == pa["old_number"],
                f"{a['name']} / {a['number']}" if a else "not found")
        run["verify"] = {"ok": all(c["ok"] for c in checks), "checks": checks}
        if not run["verify"]["ok"]:
            raise StepFailed("The read-back does not match the plan: "
                             + "; ".join(f"{c['label']} ({c['detail']})" for c in checks if not c["ok"]))
        return f"{len(checks)} checks passed"
