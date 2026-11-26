"""In-memory stand-in for the RMS calls used by Dynamic Layering (CRA_DEMO=1 and the tests). Same methods as LayerApi.

Test switches:  copy_ignores_names  the account copy keeps its own name / number ("<name> (copy)", "<number>_C")
                reject_copy         the copy request is refused with HTTP 400
                slow_polls          polls that still say RUNNING before the job finishes
                drop_locations      the copy only brings half of the locations
                drop_policy         the copy brings no policy
                fail                {'update_policy_terms': 1}  -> that call raises once, then works
Helper:         add_linked_portfolio(name, number, base_portfolio_id)  = what a portfolio copy leaves behind in real RMS
"""
from __future__ import annotations

import copy
import itertools

from .layer_api import LayerApiError, copy_body, manage_accounts_body, portfolio_create_body


class DemoLayerApi:
    def __init__(self, svc=None, *, copy_ignores_names=False, reject_copy=False, slow_polls=1, fail=None,
                 drop_locations=False, drop_policy=False, seed: list | None = None):
        self.svc = svc
        self.copy_ignores_names, self.reject_copy, self.slow_polls = copy_ignores_names, reject_copy, slow_polls
        self.drop_locations, self.drop_policy, self.fail = drop_locations, drop_policy, dict(fail or {})
        self.portfolios, self.accounts, self.policies, self.jobs, self.calls, self.bodies = {}, {}, {}, {}, [], []
        self.edm_name = ""
        self._pid, self._aid, self._polid, self._job = (itertools.count(1), itertools.count(100), itertools.count(500),
                                                        itertools.count(1))
        for row in (seed if seed is not None else self._seed_from_svc()):
            self._add_base(**row)

    # ---------------------------------------------------------------- seed
    def _seed_from_svc(self) -> list:
        rows = []
        try:
            ports = self.svc.list_portfolios("") if self.svc is not None else []
        except Exception:                                              # noqa: BLE001
            ports = []
        from ..rules.layers import layer_terms
        for p in ports or [{"portfolio_name": "TIVxs0_Demo_Harbor_Properties", "layer": "TIVxs0"}]:
            t = layer_terms(p["layer"]) or (None, 0.0)
            rows.append({"name": p["portfolio_name"], "number": p["layer"] + "_DH", "limit": t[0] or 0.0,
                         "attachment": t[1] or 0.0})
        return rows

    def _add_base(self, name, number, limit=0.0, attachment=0.0, locations=30, acct_name=None, acct_number=None, description=""):
        pid = str(next(self._pid))
        aid = str(next(self._aid))
        label = name.split("_", 1)[0]
        self.portfolios[pid] = {"id": pid, "name": name, "number": number, "description": description, "accounts": [aid]}
        self.accounts[aid] = {"id": aid, "name": acct_name or f"{label}_Demo Harbor Properties", "number": acct_number or f"{label}_1001",
                              "locations": locations, "raw": {"cedant": {"cedantName": "ANV"}, "underwriter": {"underwriterName": "TR"}}}
        self._add_policy(aid, limit, attachment, 1.0)
        return pid

    def add_account(self, portfolio_id, name, number, locations=10, limit=0.0, attachment=0.0):
        aid = str(next(self._aid))
        self.accounts[aid] = {"id": aid, "name": name, "number": number, "locations": locations, "raw": {}}
        self.portfolios[str(portfolio_id)]["accounts"].append(aid)
        self._add_policy(aid, limit, attachment, 1.0)
        return aid

    def add_linked_portfolio(self, name, number, base_portfolio_id):
        """What RMS's portfolio copy leaves behind: a new portfolio that holds the ORIGINAL account(s)."""
        pid = str(next(self._pid))
        self.portfolios[pid] = {"id": pid, "name": name, "number": number, "description": "",
                                "accounts": list(self.portfolios[str(base_portfolio_id)]["accounts"])}
        return pid

    def _add_policy(self, aid, limit, attachment, blanket):
        pid = str(next(self._polid))
        self.policies[pid] = {"id": pid, "account_id": aid, "number": f"POL{pid}", "partOf": limit, "attachment": attachment,
                              "blanket": blanket, "attach_key": "attachmentPoint",
                              "raw": {"policyNumber": f"POL{pid}", "accountId": aid, "status": {"name": "Quote"},
                                      "currency": {"code": "USD"}, "coverages": [{"id": 1}], "partOf": limit,
                                      "attachmentPoint": attachment, "blanketLimit": blanket}}

    def _maybe_fail(self, what):
        self.calls.append(what)
        if self.fail.get(what, 0) > 0:
            self.fail[what] -= 1
            raise LayerApiError(500, f"demo://{what}", "simulated failure")

    # ---------------------------------------------------------------- reads (same shapes as LayerApi)
    def list_portfolios(self, exposure_id):
        self._maybe_fail("list_portfolios")
        return [{"id": p["id"], "name": p["name"], "number": p["number"], "description": p["description"]} for p in self.portfolios.values()]

    def list_portfolio_accounts(self, exposure_id, portfolio_id):
        self._maybe_fail("list_portfolio_accounts")
        p = self.portfolios.get(str(portfolio_id))
        if p is None:
            raise LayerApiError(404, "demo://portfolio", "not found")
        return [copy.deepcopy(self.accounts[a]) for a in p["accounts"]]

    def list_accounts(self, exposure_id):
        self._maybe_fail("list_accounts")
        return [copy.deepcopy(a) for a in self.accounts.values()]

    def list_policies(self, exposure_id, account_id):
        self._maybe_fail("list_policies")
        return [copy.deepcopy(p) for p in self.policies.values() if p["account_id"] == str(account_id)]

    # ---------------------------------------------------------------- copy job (ACCOUNT only)
    def submit_copy_job(self, exposure_id, resource_type, resource_id, name, number, settings=None):
        self.calls.append("submit_copy_job")
        body = copy_body(exposure_id, resource_type, resource_id, name, number, settings)
        self.bodies.append(body)
        if self.reject_copy:
            return {"ok": False, "http": 400, "job_url": "", "text": '{"message":"settings.name is not allowed"}', "body": body}
        if resource_type != "ACCOUNT":
            return {"ok": False, "http": 400, "job_url": "", "text": "demo only copies accounts", "body": body}
        jid = str(next(self._job))
        self.jobs[jid] = {"polls": 0, "src": str(resource_id), "name": name, "number": number}
        return {"ok": True, "http": 201, "job_url": f"demo://copy/jobs/{jid}", "text": "", "body": body}

    def poll_job(self, job_url):
        self.calls.append("poll_job")
        job = self.jobs[job_url.rsplit("/", 1)[1]]
        job["polls"] += 1
        if job["polls"] <= self.slow_polls:
            return {"http": 200, "status": "RUNNING", "raw": '{"status":"RUNNING"}'}
        if not job.get("made"):
            job["made"] = True
            self._copy_account(job)
        return {"http": 200, "status": "FINISHED", "raw": '{"status":"FINISHED"}'}

    def _copy_account(self, job):
        src = self.accounts[job["src"]]
        new = str(next(self._aid))
        locs = src["locations"] // 2 if self.drop_locations else src["locations"]
        self.accounts[new] = {**copy.deepcopy(src), "id": new, "locations": locs,
                              "name": job["name"] if not self.copy_ignores_names else src["name"] + " (copy)",
                              "number": job["number"] if not self.copy_ignores_names else src["number"] + "_C"}
        if not self.drop_policy:
            for pol in [p for p in self.policies.values() if p["account_id"] == job["src"]]:
                self._add_policy(new, pol["partOf"], pol["attachment"], pol["blanket"])

    # ---------------------------------------------------------------- writes
    def create_portfolio(self, exposure_id, name, number, description=""):
        self._maybe_fail("create_portfolio")
        body = portfolio_create_body(name, number, description)
        if any(p["number"].lower() == number.lower() for p in self.portfolios.values()):
            return {"ok": False, "http": 400, "text": '{"message":"portfolioNumber already exists"}', "body": body, "portfolio_id": ""}
        pid = str(next(self._pid))
        self.portfolios[pid] = {"id": pid, "name": name, "number": number, "description": description, "accounts": []}
        return {"ok": True, "http": 201, "text": "", "body": body, "portfolio_id": pid}

    def manage_portfolio_accounts(self, exposure_id, portfolio_id, add, remove):
        self._maybe_fail("manage_portfolio_accounts")
        body = manage_accounts_body(add, remove)
        p = self.portfolios[str(portfolio_id)]
        for a in remove:
            if str(a) in p["accounts"]:
                p["accounts"].remove(str(a))
        for a in add:
            if str(a) not in self.accounts:
                return {"ok": False, "http": 400, "text": f"account {a} not found", "body": body}
            if str(a) not in p["accounts"]:
                p["accounts"].append(str(a))
        return {"ok": True, "http": 200, "text": "", "body": body}

    def update_portfolio(self, exposure_id, portfolio_id, name, number):
        self._maybe_fail("update_portfolio")
        p = self.portfolios[str(portfolio_id)]
        p["name"], p["number"] = name, number
        return {"ok": True, "http": 204, "text": "", "body": {"portfolioName": name, "portfolioNumber": number}}

    def update_account(self, exposure_id, account_id, name, number):
        self._maybe_fail("update_account")
        a = self.accounts[str(account_id)]
        a["name"], a["number"] = name, number
        return {"ok": True, "http": 204, "text": "", "body": {"accountName": name, "accountNumber": number}}

    def update_policy_terms(self, exposure_id, policy_id, limit, attachment, blanket):
        self._maybe_fail("update_policy_terms")
        p = self.policies[str(policy_id)]
        p["partOf"], p["attachment"], p["blanket"] = limit, attachment, blanket
        self._register_demo_layer(p["account_id"], limit, attachment)
        return {"ok": True, "http": 204, "text": "", "body": {"partOf": limit, "attachmentPoint": attachment, "blanketLimit": blanket}}

    def _register_demo_layer(self, account_id, limit, attachment):
        """So the demo's Analyses step can run the new layer: add it to the demo model's layer list."""
        try:
            from . import demo as _demo
        except ImportError:
            return
        pf = next((p for p in self.portfolios.values() if account_id in p["accounts"]), None)
        label = (pf["name"].split("_", 1)[0]) if pf else ""
        if label and not any(x[0] == label for x in _demo.LAYERS):
            _demo.LAYERS.append((label, float(attachment), float(limit)))
