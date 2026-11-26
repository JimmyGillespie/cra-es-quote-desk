"""Dynamic layering: add a layer to an EDM (for example 15M xs 10M) without importing anything.

    copy each base ACCOUNT (a real, independent copy) -> create the portfolio (or reuse an existing one) -> attach the
    copied accounts to it (and unlink any ORIGINAL account that is attached) -> set the layer terms on the copied
    policies -> read everything back and compare with the plan

Why not copy the portfolio? RMS's portfolio copy links the new portfolio to the ORIGINAL accounts (seen on a live EDM), so
renaming "its" accounts would rename the originals. Copying accounts, then attaching, uses documented calls only and can
never touch an original account.

The terms map to the ACC columns your EQ app writes: Part of = PARTOF, Excess of = UNDCOVAMT (the policy's attachmentPoint),
Blanket limit = BLANLIMAMT (a fraction: 100% = 1.0).

No Streamlit in here. Planning is pure; the builder talks to an api object (cra/rms/layer_api.py, or the in-memory demo) and
saves the run after every step, so a closed browser or a failed call never leaves the work in an unknown state.
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

MAX_NUMBER = 20                  # portfolio and account numbers
PORTFOLIO_NAME_MAX = 40          # RMS: portfolioName length 0-40
ACCOUNT_NAME_WARN = 40

STEPS = [("snapshot", "Read the EDM"), ("accounts", "Copy the account(s)"), ("portfolio", "Create the portfolio"),
         ("attach", "Put the copied account(s) in the portfolio"), ("policies", "Set the layer terms"),
         ("verify", "Read it all back and check")]
STEP_KEYS = [k for k, _ in STEPS]


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


def is_layer_label(s) -> bool:
    return layer_terms((s or "").strip()) is not None


def strip_layer_prefix(s: str) -> str:
    """'10Mxs0_Ben Davis' -> 'Ben Davis' (only when the first token really is a layer)."""
    s = (s or "").strip()
    if "_" in s:
        head, tail = s.split("_", 1)
        if tail and layer_terms(head) is not None:
            return tail
    return s


def name_for(old: str, label: str) -> str:
    """'25Mxs0_TWAS' -> '10Mxs10M_TWAS'.  A name that IS a layer ('25MXS0') becomes just the new layer ('10Mxs10M')."""
    old = (old or "").strip()
    if not old or is_layer_label(old):
        return label
    return f"{label}_{strip_layer_prefix(old)}"


def number_for(old: str, label: str, maxlen: int = MAX_NUMBER) -> str:
    old = (old or "").strip()
    if not old or is_layer_label(old):
        return label[:maxlen]
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


# ---------------------------------------------------------------- reading the EDM
class StepFailed(Exception):
    pass


class StepPending(Exception):
    pass


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


def reuse_candidate(api, exposure_id, portfolios, label: str, base_id, base_account_ids) -> dict | None:
    """An existing portfolio for this layer (another spelling counts), and whether it is safe to reuse: only when everything
    in it is an original account of the base portfolio (the leftover of a portfolio copy), never someone else's accounts."""
    key, base_set = layer_key(label), {str(x) for x in base_account_ids}
    for p in portfolios:
        if p["id"] != str(base_id) and layer_key(layer_from_portfolio_name(p["name"])) == key:
            accts = api.list_portfolio_accounts(exposure_id, p["id"])
            foreign = [a for a in accts if a["id"] not in base_set]
            return {"id": p["id"], "name": p["name"], "number": p["number"],
                    "accounts": [{"id": a["id"], "name": a["name"], "number": a["number"]} for a in accts],
                    "reusable": not foreign,
                    "why": ("" if not foreign else
                            "it holds account(s) that are not in the base portfolio: "
                            + ", ".join(f"{a['name']} ({a['number']})" for a in foreign))}
    return None


# ---------------------------------------------------------------- the plan
def build_plan(terms: LayerTerms, base: dict, base_accounts: list, base_policies: dict, portfolios: list,
               account_numbers, claimed_portfolio_numbers=(), claimed_account_numbers=(), reuse: dict | None = None) -> dict:
    """What will be created, with every name, number and term before -> after. Pure: no RMS calls.
    reuse: reuse_candidate() dict (reusable) to put the copies into an existing portfolio instead of creating one."""
    label = terms.label
    rid = str(reuse["id"]) if reuse else None
    others = [p for p in portfolios if p["id"] != rid]
    taken_pn = {_lc(p["number"]) for p in others} | {_lc(x) for x in claimed_portfolio_numbers}
    taken_an = {_lc(n) for n in account_numbers} | {_lc(x) for x in claimed_account_numbers}
    if reuse:
        pf = {"name": reuse["name"], "number": reuse["number"], "reuse_id": rid, "description": base.get("description", "")}
    else:
        pf = {"name": name_for(base["name"], label), "number": unique(number_for(base.get("number") or base["name"], label), taken_pn),
              "reuse_id": None, "description": base.get("description", "")}
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
            "portfolio": pf, "accounts": accounts, "errors": [], "warnings": [], "ok": False}
    return validate_plan(plan, portfolios, account_numbers, claimed_portfolio_numbers, claimed_account_numbers)


def validate_plan(plan: dict, portfolios: list, account_numbers, claimed_portfolio_numbers=(),
                  claimed_account_numbers=(), claimed_labels=()) -> dict:
    """Recompute errors / warnings (also after the user edits a name or number). Returns the same dict."""
    t = plan["terms"]
    terms = LayerTerms(t["limit"], t["attachment"], t["blanket_pct"])
    errs, warns = list(terms.problems()), []
    label, pf = plan["layer"], plan["portfolio"]
    rid = pf.get("reuse_id")
    others = [p for p in portfolios if p["id"] != rid]
    existing = {layer_key(layer_from_portfolio_name(p["name"])): p["name"] for p in others}
    if layer_key(label) in existing:
        errs.append(f"A portfolio for layer {label} already exists in this EDM ({existing[layer_key(label)]}). "
                    "Choose 'Reuse it' if it is a leftover from an earlier attempt.")
    if layer_key(label) in {layer_key(x) for x in claimed_labels}:
        errs.append(f"Layer {label} is entered twice.")
    if not plan["accounts"]:
        errs.append("The base portfolio has no accounts, so there is nothing to copy.")
    if not pf["name"].strip():
        errs.append("The portfolio name is empty.")
    if not pf["number"].strip():
        errs.append("The portfolio number is empty.")
    if len(pf["name"]) > PORTFOLIO_NAME_MAX:
        errs.append(f"The portfolio name '{pf['name']}' is {len(pf['name'])} characters; RMS allows {PORTFOLIO_NAME_MAX}.")
    if len(pf["number"]) > MAX_NUMBER:
        errs.append(f"The portfolio number '{pf['number']}' is longer than {MAX_NUMBER} characters.")
    if _lc(pf["number"]) in {_lc(p["number"]) for p in others} | {_lc(x) for x in claimed_portfolio_numbers}:
        errs.append(f"The portfolio number '{pf['number']}' is already used.")
    if _lc(pf["name"]) in {_lc(p["name"]) for p in others}:
        errs.append(f"The portfolio name '{pf['name']}' is already used.")
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
        if len(a["new_name"]) > ACCOUNT_NAME_WARN:
            warns.append(f"Account {who}: the new name is {len(a['new_name'])} characters - RMS may limit it to {ACCOUNT_NAME_WARN}.")
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


def build_plans(terms_list: list, base, base_accounts, base_policies, portfolios, account_numbers, reuse_list=None) -> list:
    """Several layers at once: each plan is checked against the EDM and against the plans before it."""
    plans, cp, ca, labels = [], [], [], []
    for i, t in enumerate(terms_list):
        reuse = reuse_list[i] if reuse_list and i < len(reuse_list) else None
        p = build_plan(t, base, base_accounts, base_policies, portfolios, account_numbers, cp, ca, reuse=reuse)
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
def new_run(plan: dict, settings: dict | None = None) -> dict:
    return {"id": uuid.uuid4().hex[:10], "created": now_iso(), "status": "PLANNED", "plan": plan, "settings": settings or {},
            "steps": [{"key": k, "label": l, "state": "pending", "detail": ""} for k, l in STEPS],
            "ids": {}, "log": [], "error": "", "verify": None}


def is_current(run: dict) -> bool:
    """False for a run made by the earlier (portfolio-copy) version: it cannot be resumed."""
    return [s.get("key") for s in run.get("steps", [])] == STEP_KEYS


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
    steps are skipped, accounts or a portfolio that an earlier attempt already created are adopted instead of created again,
    and an ORIGINAL account is only ever detached from the NEW portfolio - never changed, never removed from the base."""

    def __init__(self, api, exposure_id, save: Callable[[dict], None], *, sleep=time.sleep, clock=time.time,
                 poll_every: float = 3.0, poll_timeout: float = 300.0):
        self.api, self.eid, self.save = api, str(exposure_id), save
        self.sleep, self.clock, self.poll_every, self.poll_timeout = sleep, clock, poll_every, poll_timeout

    def _log(self, run, msg):
        run["log"].append(f"{now_iso()[11:19]}  {msg}")
        del run["log"][:-200]

    # ------------------------------------------------------------ driver
    def advance(self, run: dict, progress: Callable[[float, str], None] | None = None) -> dict:
        if not is_current(run):
            run["status"], run["error"] = "FAILED", "This run was made by an earlier version and cannot be resumed. Remove it and create the layer again."
            self.save(run)
            return run
        run["status"], run["error"] = "RUNNING", ""
        self.save(run)
        fns = {"snapshot": self._snapshot, "accounts": self._accounts, "portfolio": self._portfolio, "attach": self._attach,
               "policies": self._policies, "verify": self._verify}
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

    # ------------------------------------------------------------ helpers
    def _wait_job(self, run, holder: dict, progress, what: str) -> str:
        t0, bad = self.clock(), 0
        while True:
            st = self.api.poll_job(holder["job_url"])
            holder["status"], holder["raw"] = st["status"], (st["raw"] or "")[:1500]
            if st["status"] in JOB_DONE:
                self.save(run)
                return st["status"]
            if st["status"] in JOB_BAD:
                raise StepFailed(f"The copy job for {what} ended as {st['status']}: {(st['raw'] or '')[:500]}")
            bad = bad + 1 if not 200 <= st["http"] < 300 else 0
            if bad >= 5:
                raise StepFailed(f"The copy job for {what} could not be read (HTTP {st['http']}): {(st['raw'] or '')[:300]}")
            if self.clock() - t0 > self.poll_timeout:
                self.save(run)
                raise StepPending(f"RMS is still copying {what} (status {st['status']}). Press Resume to keep waiting.")
            self.sleep(self.poll_every)

    def _new_ids(self, run) -> list:
        return [run["ids"]["accounts"][pa["base_id"]]["new_id"] for pa in run["plan"]["accounts"]]

    # ------------------------------------------------------------ steps
    def _snapshot(self, run, progress):
        plan, ids = run["plan"], run["ids"]
        pf = self.api.list_portfolios(self.eid)
        by_id = {p["id"]: p for p in pf}
        base_id, rid = plan["base"]["id"], plan["portfolio"].get("reuse_id")
        if base_id not in by_id:
            raise StepFailed("The base portfolio no longer exists in this EDM.")
        if rid:
            if rid not in by_id:
                raise StepFailed("The portfolio chosen for reuse no longer exists in this EDM.")
            if rid == base_id:
                raise StepFailed("SAFETY STOP: the portfolio to reuse is the base portfolio itself.")
        else:
            for p in pf:
                if _lc(p["name"]) == _lc(plan["portfolio"]["name"]) or _lc(p["number"]) == _lc(plan["portfolio"]["number"]):
                    raise StepFailed(f"A portfolio named '{p['name']}' (number {p['number']}) already exists. Reuse it, or "
                                     "change the name / number.")
        base_accts = self.api.list_portfolio_accounts(self.eid, base_id)
        if len(base_accts) != len(plan["accounts"]):
            raise StepFailed(f"The base portfolio now has {len(base_accts)} account(s) but the plan was made for "
                             f"{len(plan['accounts'])}. Build the preview again.")
        base_ids = sorted((a["id"] for a in base_accts), key=_idkey)
        if rid:
            foreign = [a for a in self.api.list_portfolio_accounts(self.eid, rid) if a["id"] not in set(base_ids)]
            if foreign:
                raise StepFailed("The portfolio chosen for reuse holds account(s) that are not in the base portfolio ("
                                 + ", ".join(f"{a['name']} / {a['number']}" for a in foreign) + "). Nothing was changed.")
        every = self.api.list_accounts(self.eid)
        used = {_lc(a["number"]): a for a in every}
        for pa in plan["accounts"]:
            if _lc(pa["new_number"]) in used:
                raise StepFailed(f"An account numbered '{pa['new_number']}' already exists in this EDM. Change the number, or "
                                 "remove the leftover account first.")
        ids["before_portfolios"], ids["before_accounts"] = [p["id"] for p in pf], [a["id"] for a in every]
        ids["base_accounts"] = base_ids
        ids["accounts"] = {}
        return f"{len(pf)} portfolios, {len(every)} account(s) in the EDM; the base has {len(base_accts)} account(s)" + \
            (f"; reusing portfolio {rid}" if rid else "")

    def _find_new_account(self, run, pa) -> dict | None:
        before = set(run["ids"]["before_accounts"])
        claimed = {s.get("new_id") for s in run["ids"]["accounts"].values()}
        cands = [a for a in self.api.list_accounts(self.eid) if a["id"] not in before and a["id"] not in claimed]
        exact = [a for a in cands if _lc(a["number"]) == _lc(pa["new_number"])]
        if len(exact) == 1:
            return exact[0]
        return cands[0] if len(cands) == 1 else None

    def _accounts(self, run, progress):
        plan, ids = run["plan"], run["ids"]
        state = ids.setdefault("accounts", {})
        copied = adopted = renamed = 0
        for pa in sorted(plan["accounts"], key=lambda a: _idkey(a["base_id"])):
            s = state.setdefault(pa["base_id"], {})
            if not s.get("new_id"):
                if not s.get("job_url"):
                    early = self._find_new_account(run, pa)         # a crashed earlier attempt?
                    if early is not None and _lc(early["number"]) == _lc(pa["new_number"]):
                        s["new_id"], s["adopted"] = early["id"], True
                        adopted += 1
                        self.save(run)
                        continue
                    res = self.api.submit_copy_job(self.eid, "ACCOUNT", pa["base_id"], pa["new_name"], pa["new_number"],
                                                   run.get("settings") or {})
                    s["body"], s["http"] = res["body"], res["http"]
                    if not res["ok"]:
                        raise StepFailed(f"RMS did not accept the copy request for account {pa['old_name'] or pa['base_id']} "
                                         f"(HTTP {res['http']}): {res['text'][:500]}")
                    s["job_url"], s["submitted"] = res["job_url"], now_iso()
                    self.save(run)
                self._wait_job(run, s, progress, f"account {pa['old_name'] or pa['base_id']}")
                new = self._find_new_account(run, pa)
                if new is None:
                    raise StepFailed(f"The copy job for account {pa['old_name'] or pa['base_id']} finished but I could not tell "
                                     "which account it created. Check the EDM, then resume.")
                s["new_id"] = new["id"]
                copied += 1
                self.save(run)
        every = {a["id"]: a for a in self.api.list_accounts(self.eid)}
        for pa in plan["accounts"]:
            nid = state[pa["base_id"]]["new_id"]
            if nid in set(ids["base_accounts"]):
                raise StepFailed("SAFETY STOP: the 'copy' is one of the original accounts. Nothing was renamed.")
            cur = every.get(nid)
            if cur is None:
                raise StepFailed(f"The copied account {nid} cannot be found in the EDM.")
            if cur["name"] != pa["new_name"] or cur["number"] != pa["new_number"]:
                res = self.api.update_account(self.eid, nid, pa["new_name"], pa["new_number"])
                if not res["ok"]:
                    raise StepFailed(f"RMS did not accept the rename of account {nid} (HTTP {res['http']}): {res['text'][:400]}")
                renamed += 1
        return f"{copied} copied, {adopted} found from an earlier attempt, {renamed} renamed"

    def _portfolio(self, run, progress):
        plan, ids = run["plan"], run["ids"]
        want, rid = plan["portfolio"], plan["portfolio"].get("reuse_id")
        pf = self.api.list_portfolios(self.eid)
        by_id = {p["id"]: p for p in pf}
        if ids.get("portfolio_id"):
            if ids["portfolio_id"] not in by_id:
                raise StepFailed("The portfolio recorded earlier no longer exists.")
            return f"portfolio {ids['portfolio_id']}"
        if rid:
            cur = by_id.get(rid)
            if cur is None:
                raise StepFailed("The portfolio chosen for reuse no longer exists.")
            ids["portfolio_id"] = rid
            if cur["name"] != want["name"] or cur["number"] != want["number"]:
                res = self.api.update_portfolio(self.eid, rid, want["name"], want["number"])
                if not res["ok"]:
                    raise StepFailed(f"RMS did not accept the portfolio rename (HTTP {res['http']}): {res['text'][:400]}")
            return f"reusing '{want['name']}' (id {rid})"
        before = set(ids["before_portfolios"])
        for p in pf:                                                      # a crashed earlier attempt?
            if p["id"] not in before and (_lc(p["name"]) == _lc(want["name"]) or _lc(p["number"]) == _lc(want["number"])):
                ids["portfolio_id"] = p["id"]
                return f"found the portfolio from an earlier attempt (id {p['id']}) - not created again"
        res = self.api.create_portfolio(self.eid, want["name"], want["number"], want.get("description", ""))
        if not res["ok"]:
            raise StepFailed(f"RMS did not accept the new portfolio (HTTP {res['http']}): {res['text'][:500]}")
        new_id = res.get("portfolio_id") or ""
        if not new_id:
            made = [p for p in self.api.list_portfolios(self.eid) if p["id"] not in before and _lc(p["number"]) == _lc(want["number"])]
            new_id = made[0]["id"] if len(made) == 1 else ""
        if not new_id:
            raise StepFailed("The portfolio was created but I could not find its id. Check the EDM, then resume.")
        ids["portfolio_id"] = new_id
        return f"created '{want['name']}' / {want['number']} (id {new_id})"

    def _attach(self, run, progress):
        plan, ids = run["plan"], run["ids"]
        pid, base_set, new_ids = ids["portfolio_id"], set(ids["base_accounts"]), self._new_ids(run)
        if pid == plan["base"]["id"]:
            raise StepFailed("SAFETY STOP: the target portfolio is the base portfolio. Nothing was changed.")
        cur_ids = {a["id"] for a in self.api.list_portfolio_accounts(self.eid, pid)}
        foreign = cur_ids - base_set - set(new_ids)
        if foreign:
            raise StepFailed("The portfolio holds account(s) that are neither originals nor the new copies (ids "
                             + ", ".join(sorted(foreign)) + "). They were left alone and nothing was changed.")
        add = [i for i in new_ids if i not in cur_ids]
        remove = sorted((i for i in cur_ids if i in base_set), key=_idkey)      # ORIGINALS linked by an earlier portfolio copy
        if not add and not remove:
            return "already correct"
        res = self.api.manage_portfolio_accounts(self.eid, pid, add, remove)
        if not res["ok"]:
            raise StepFailed(f"RMS did not accept the account attach (HTTP {res['http']}): {res['text'][:500]}")
        return f"{len(add)} added" + (f", {len(remove)} original(s) unlinked from this portfolio (not deleted)" if remove else "")

    def _policies(self, run, progress):
        t = run["plan"]["terms"]
        want = {"partOf": t["limit"], "attachment": t["attachment"], "blanket": round(t["blanket_pct"] / 100.0, 10)}
        changed = total = 0
        for nid in self._new_ids(run):
            pols = self.api.list_policies(self.eid, nid)
            if not pols:
                raise StepFailed(f"The copied account {nid} has no policy to set the layer terms on. Did the copy include "
                                 "the policies?")
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

        pid, new_ids, t = ids["portfolio_id"], self._new_ids(run), plan["terms"]
        pf = next((p for p in self.api.list_portfolios(self.eid) if p["id"] == pid), None)
        add("Portfolio name and number", pf and pf["name"] == plan["portfolio"]["name"] and pf["number"] == plan["portfolio"]["number"],
            f"{pf['name']} / {pf['number']}" if pf else "portfolio not found")
        members = {a["id"]: a for a in self.api.list_portfolio_accounts(self.eid, pid)}
        add("The portfolio holds exactly the new account(s)", set(members) == set(new_ids),
            f"holds {len(members)}: " + ", ".join(f"{a['name']} ({a['number']})" for a in members.values()))
        add("No original account is in the new portfolio", not (set(members) & set(ids["base_accounts"])), "")
        for pa in plan["accounts"]:
            nid = ids["accounts"][pa["base_id"]]["new_id"]
            a = members.get(nid)
            add(f"Account {pa['new_number']}: name and number", a and a["name"] == pa["new_name"] and a["number"] == pa["new_number"],
                f"{a['name']} / {a['number']}" if a else "not in the portfolio")
            if pa["locations"] and a and a["locations"]:
                add(f"Account {pa['new_number']}: locations copied", a["locations"] == pa["locations"],
                    f"{a['locations']} locations; the original has {pa['locations']}")
            pols = self.api.list_policies(self.eid, nid)
            ok = bool(pols) and all(_close(p["partOf"], t["limit"]) and _close(p["attachment"], t["attachment"])
                                    and _close(p["blanket"], t["blanket_pct"] / 100.0) for p in pols)
            add(f"Account {pa['new_number']}: layer terms", ok,
                "; ".join(f"Part of {money(p['partOf'])}, Excess of {money(p['attachment'])}, blanket {p['blanket']}" for p in pols)
                or "no policy")
        base_now = {a["id"]: a for a in self.api.list_portfolio_accounts(self.eid, plan["base"]["id"])}
        for pa in plan["accounts"]:
            a = base_now.get(pa["base_id"])
            add(f"Original account {pa['old_number'] or pa['old_name']} still in {plan['base']['name']}, unchanged",
                a and a["name"] == pa["old_name"] and a["number"] == pa["old_number"],
                f"{a['name']} / {a['number']}" if a else "not found")
            if pa["policies"]:
                now = {p["id"]: p for p in self.api.list_policies(self.eid, pa["base_id"])}
                same = all(p["policy_id"] in now and _close(now[p["policy_id"]]["partOf"], p["before"]["partOf"])
                           and _close(now[p["policy_id"]]["attachment"], p["before"]["attachment"])
                           and _close(now[p["policy_id"]]["blanket"], p["before"]["blanket"]) for p in pa["policies"])
                add(f"Original account {pa['old_number'] or pa['old_name']}: layer terms unchanged", same, "")
        run["verify"] = {"ok": all(c["ok"] for c in checks), "checks": checks}
        if not run["verify"]["ok"]:
            raise StepFailed("The read-back does not match the plan: "
                             + "; ".join(f"{c['label']} ({c['detail']})" for c in checks if not c["ok"]))
        return f"{len(checks)} checks passed"
