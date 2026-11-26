"""RMS calls for Dynamic Layering: copy an ACCOUNT, create a PORTFOLIO, attach accounts to it, set the layer terms.

Why account-first: copying a PORTFOLIO links the new portfolio to the ORIGINAL accounts (confirmed on a live EDM: one account
in the EDM after the copy), whatever linkExistingAccounts says. So the app copies each account (a real, independent copy),
creates the portfolio, and attaches the copies with the documented "Manage accounts by portfolio" call.

Where each request shape comes from (RMS developer portal):
  [docs]  POST  /platform/copy/v1/jobs                 resourceUri + resourceType (ACCOUNT | PORTFOLIO) + settings;
                                                       header x-rms-resource-group-id; 201 + Location = job URL.
                                                       RMS replied "settings.name / settings.number must not be blank".
  [docs]  POST  .../exposures/{id}/portfolios          portfolioName (<=40), portfolioNumber (<=20), description; 201 + Location
  [docs]  PATCH .../exposures/{id}/portfolios/{pid}/accounts   accountsToAdd + accountsToRemove (both required, may be empty)
  [docs]  PUT   .../exposures/{id}/accounts/{aid}      accountName, accountNumber (required)
  [docs]  PUT   .../exposures/{id}/policies/{pid}      partOf, attachmentPoint, blanketLimit (Risk Modeler v1 field names)
  [seen]  GET   .../portfolios, .../portfolios/{pid}/accounts, .../policies   worked on a live EDM
  [infer] GET/PUT .../portfolios/{pid} (rename), GET .../accounts; Risk Modeler v1 policy route as a fallback on 404
"""
from __future__ import annotations

import json
import re
from urllib.parse import quote

from .core import gj, gv, json_or_none, to_num

JOB_DONE = {"FINISHED", "SUCCEEDED", "COMPLETED"}
JOB_BAD = {"FAILED", "CANCELED", "CANCELLED", "ERROR"}
PAGE = 200
ATTACH_KEYS = ("attachmentPoint", "underlyingCoverage", "underlyingCoverageAmount", "underlyingAmount")
POLICY_DROP = ("coverages", "reinsuranceCessions", "policyConditions", "uri", "createDate", "stampDate")
ACCOUNT_KEEP = ("cedant", "producer", "underwriter", "branch", "userId1", "userId2", "userId3", "userId4",
                "userText1", "userText2")
PORTFOLIO_KEEP = ("description",)
DEFAULT_COPY_SETTINGS: dict = {}                   # extra keys for every copy job; editable in the app (Advanced)
COPY_PATHS = {"ACCOUNT": "accounts", "PORTFOLIO": "portfolios"}


class LayerApiError(RuntimeError):
    def __init__(self, status: int, url: str, text: str):
        super().__init__(f"HTTP {status} from {url}: {(text or '')[:300]}")
        self.status, self.url, self.text = status, url, text or ""


def _as_list(parsed) -> list:
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        for k in ("searchItems", "items", "data", "results", "accounts", "policies", "portfolios", "exposures"):
            if isinstance(parsed.get(k), list):
                return parsed[k]
    return []


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return v


# ---------------------------------------------------------------- normalisers (tolerant of key names)
def norm_portfolio(it: dict) -> dict:
    return {"id": gj(it, "portfolioId") or gj(it, "id"), "name": gj(it, "portfolioName") or gj(it, "name"),
            "number": gj(it, "portfolioNumber") or gj(it, "number"), "description": gj(it, "description")}


def norm_account(it: dict) -> dict:
    n = to_num(gj(it, "locationsCount") or gj(it, "numLocations"))
    return {"id": gj(it, "accountId") or gj(it, "id"), "name": gj(it, "accountName") or gj(it, "name"),
            "number": gj(it, "accountNumber") or gj(it, "number"), "locations": int(n or 0), "raw": it}


def norm_policy(it: dict) -> dict:
    attach_key = next((k for k in ATTACH_KEYS if k in it), "attachmentPoint")
    return {"id": gj(it, "policyId") or gj(it, "id"), "number": gj(it, "policyNumber") or gj(it, "number"),
            "account_id": gj(it, "accountId"), "partOf": to_num(gv(it, "partOf")),
            "attachment": to_num(gv(it, attach_key)), "blanket": to_num(gv(it, "blanketLimit")),
            "attach_key": attach_key, "raw": it}


# ---------------------------------------------------------------- request bodies (pure, so they can be shown and tested)
def copy_body(exposure_id, resource_type: str, resource_id, name: str, number: str, settings: dict | None = None) -> dict:
    """The copy job. resource_type ACCOUNT or PORTFOLIO. RMS requires settings.name and settings.number."""
    kind = COPY_PATHS[resource_type]
    s = dict(DEFAULT_COPY_SETTINGS)
    s.update({"name": name, "number": number})
    s.update(settings or {})
    return {"resourceUri": f"/platform/riskdata/v1/exposures/{exposure_id}/{kind}/{resource_id}",
            "resourceType": resource_type, "settings": s}


def portfolio_create_body(name: str, number: str, description: str = "") -> dict:
    return {"portfolioName": name, "portfolioNumber": number, "description": (description or "")[:255]}


def manage_accounts_body(add: list, remove: list) -> dict:
    return {"accountsToAdd": [_int(x) for x in add], "accountsToRemove": [_int(x) for x in remove]}


def account_update_body(raw: dict, name: str, number: str) -> dict:
    body = {k: raw[k] for k in ACCOUNT_KEEP if raw.get(k) is not None}
    body["accountName"], body["accountNumber"] = name, number
    return body


def portfolio_update_body(raw: dict, name: str, number: str) -> dict:
    body = {k: raw[k] for k in PORTFOLIO_KEEP if raw.get(k) is not None}
    body["portfolioName"], body["portfolioNumber"] = name, number
    return body


def policy_update_body(policy: dict, limit: float, attachment: float, blanket: float) -> dict:
    """Read-modify-write: everything the policy already has (child lists dropped) with the three layer terms replaced."""
    body = {k: v for k, v in (policy.get("raw") or {}).items() if k not in POLICY_DROP}
    body["partOf"] = limit
    body[policy.get("attach_key") or "attachmentPoint"] = attachment
    body["blanketLimit"] = blanket
    return body


def _job_url(host: str, loc: str) -> str:
    loc = (loc or "").strip()
    if not loc:
        return ""
    if loc.lower().startswith("http"):
        return loc
    return f"https://{host}{loc}" if loc.startswith("/") else f"https://{host}/{loc}"


def _trailing_id(loc: str) -> str:
    m = re.search(r"/(\d+)/?(?:\?.*)?$", loc or "")
    return m.group(1) if m else ""


# ---------------------------------------------------------------- the client
class LayerApi:
    def __init__(self, svc, edm_name: str = ""):
        self.svc, self.http, self.c = svc, svc.http, svc.c
        self.edm_name = edm_name                      # only needed for the Risk Modeler v1 policy fallback

    # ---- plumbing
    def _exp(self, exposure_id) -> str:
        return f"https://{self.c.host}/platform/riskdata/v1/exposures/{exposure_id}"

    def _key(self) -> str:
        return self.c.riskdata_key

    def _get_json(self, url: str):
        r = self.http.get(url, self._key())
        if r.status != 200:
            raise LayerApiError(r.status, url, r.text)
        return json_or_none(r.text)

    def _paged(self, url: str) -> list:
        out, offset = [], 0
        sep = "&" if "?" in url else "?"
        while True:
            items = _as_list(self._get_json(f"{url}{sep}limit={PAGE}&offset={offset}"))
            if not items:
                break
            out.extend(i for i in items if isinstance(i, dict))
            if len(items) < PAGE or offset > 100000:
                break
            offset += PAGE
        return out

    def _send(self, method: str, url: str, body: dict, retries: int = 3) -> dict:
        r = self.http._do(method, url, self._key(), body=json.dumps(body), retries=retries)
        return {"ok": 200 <= r.status < 300, "http": r.status, "text": (r.text or "")[:600], "body": body,
                "headers": r.headers or {}}

    # ---- reads
    def list_portfolios(self, exposure_id) -> list:
        return [norm_portfolio(i) for i in self._paged(f"{self._exp(exposure_id)}/portfolios")]

    def list_portfolio_accounts(self, exposure_id, portfolio_id) -> list:
        return [norm_account(i) for i in self._paged(f"{self._exp(exposure_id)}/portfolios/{portfolio_id}/accounts")]

    def list_accounts(self, exposure_id) -> list:
        return [norm_account(i) for i in self._paged(f"{self._exp(exposure_id)}/accounts")]

    def list_policies(self, exposure_id, account_id) -> list:
        """Policies of one account: the filtered search, then the nested path, then the whole EDM filtered here."""
        aid = str(account_id)
        routes = ((f"{self._exp(exposure_id)}/policies?filter={quote('accountId=' + aid, safe='')}", False),
                  (f"{self._exp(exposure_id)}/accounts/{aid}/policies", False),
                  (f"{self._exp(exposure_id)}/policies", True))
        last, answered = None, False
        for url, strict in routes:
            try:
                items = self._paged(url)
            except LayerApiError as exc:
                last = exc
                continue
            answered = True
            pols = [norm_policy(i) for i in items]
            pols = [p for p in pols if p["account_id"] == aid or (not strict and not p["account_id"])]
            if pols:
                return pols
        if answered:
            return []
        raise last

    # ---- copy job
    def submit_copy_job(self, exposure_id, resource_type: str, resource_id, name: str, number: str,
                        settings: dict | None = None) -> dict:
        body = copy_body(exposure_id, resource_type, resource_id, name, number, settings)
        rg = self.c.grouping_resource_group_id or self.svc.resource_group_id()
        headers = {"x-rms-resource-group-id": rg} if rg else None
        url = f"https://{self.c.host}/platform/copy/v1/jobs"
        r = self.http._do("POST", url, self._key(), body=json.dumps(body), headers=headers, retries=1)   # never double-submit
        loc = (r.headers or {}).get("Location") or (r.headers or {}).get("location") or ""
        return {"ok": 200 <= r.status < 300 and bool(loc), "http": r.status, "job_url": _job_url(self.c.host, loc),
                "text": (r.text or "")[:600], "body": body}

    def poll_job(self, job_url: str) -> dict:
        r = self.http.get(job_url, self._key(), headers=None)
        status = "UNKNOWN"
        p = json_or_none(r.text) if 200 <= r.status < 300 else None
        if isinstance(p, dict):
            status = str(p.get("status") or p.get("jobStatus") or "UNKNOWN").upper()
        return {"http": r.status, "status": status, "raw": (r.text or "")[:4000]}

    # ---- portfolio and account writes
    def create_portfolio(self, exposure_id, name: str, number: str, description: str = "") -> dict:
        url = f"{self._exp(exposure_id)}/portfolios"
        res = self._send("POST", url, portfolio_create_body(name, number, description), retries=1)   # never double-create
        loc = {k.lower(): v for k, v in res["headers"].items()}.get("location", "")
        res["portfolio_id"] = _trailing_id(loc) if res["ok"] else ""
        return res

    def manage_portfolio_accounts(self, exposure_id, portfolio_id, add: list, remove: list) -> dict:
        url = f"{self._exp(exposure_id)}/portfolios/{portfolio_id}/accounts"
        res = self._send("PATCH", url, manage_accounts_body(add, remove))
        p = json_or_none(res["text"]) if res["ok"] else None
        if isinstance(p, dict):
            for key in ("addAccounts", "removeAccounts"):
                part = p.get(key) or {}
                if part.get("total") is not None and part.get("completed") != part.get("total"):
                    res["ok"] = False
                    res["text"] = f"RMS processed only part of the request: {res['text']}"
        return res

    def update_portfolio(self, exposure_id, portfolio_id, name: str, number: str) -> dict:
        url = f"{self._exp(exposure_id)}/portfolios/{portfolio_id}"
        raw = self._get_json(url)
        return self._send("PUT", url, portfolio_update_body(raw if isinstance(raw, dict) else {}, name, number))

    def update_account(self, exposure_id, account_id, name: str, number: str) -> dict:
        url = f"{self._exp(exposure_id)}/accounts/{account_id}"
        raw = self._get_json(url)
        return self._send("PUT", url, account_update_body(raw if isinstance(raw, dict) else {}, name, number))

    def update_policy_terms(self, exposure_id, policy_id, limit: float, attachment: float, blanket: float) -> dict:
        url = f"{self._exp(exposure_id)}/policies/{policy_id}"
        try:
            raw = self._get_json(url)
        except LayerApiError as exc:
            if exc.status not in (404, 405) or not self.edm_name:
                raise
            url = f"https://{self.c.host}/riskmodeler/v1/policies/{policy_id}?datasource={quote(self.edm_name, safe='')}"
            raw = self._get_json(url)                                   # Risk Modeler v1 route
        pol = norm_policy(raw if isinstance(raw, dict) else {})
        return self._send("PUT", url, policy_update_body(pol, limit, attachment, blanket))


def make_api(svc, demo: bool = False, edm_name: str = ""):
    """The real client, or the in-memory demo when the app runs with CRA_DEMO=1."""
    if demo:
        from .demo_layering import DemoLayerApi
        api = getattr(svc, "_layer_demo_api", None)
        if api is None:                       # one in-memory RMS for the whole demo, so state survives Streamlit reruns
            api = DemoLayerApi(svc)
            try:
                svc._layer_demo_api = api
            except AttributeError:
                pass
        return api
    return LayerApi(svc, edm_name)
