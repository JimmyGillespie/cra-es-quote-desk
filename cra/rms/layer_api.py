"""RMS calls for Dynamic Layering: copy a portfolio (with its accounts), rename it, set the layer terms on its policies.

Every call is a thin wrapper that returns plain dicts, so the orchestration in cra/layering.py can be tested with a fake.

Where each request shape comes from (RMS developer portal, read when this was written):
  [docs]    POST /platform/copy/v1/jobs                    resourceUri + resourceType PORTFOLIO + settings; header
                                                           x-rms-resource-group-id; 201 + Location header = job URL
  [docs]    GET  .../exposures/{id}/portfolios/{pid}/accounts
  [docs]    PUT  .../exposures/{id}/accounts/{aid}          accountName, accountNumber (required) + cedant etc.
  [docs]    PUT  .../exposures/{id}/policies/{pid}          policy: partOf, attachmentPoint, blanketLimit ...
                                                           (field names confirmed on the Risk Modeler v1 policy; the
                                                            Platform page shows the same policy model)
  [assumed] settings keys name / number / linkExistingAccounts for the copy job (the docs say the copy "must be
            renamed" and that portfolios link to existing child accounts unless told otherwise, but the page does not
            list the key names) - they are editable in the app and the portfolio is renamed explicitly afterwards.
  [assumed] GET/PUT .../portfolios/{pid} to rename a portfolio; GET .../accounts and GET .../policies?filter=...
None of this has been run against a live tenant yet.
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
DEFAULT_COPY_SETTINGS = {"linkExistingAccounts": False}


class LayerApiError(RuntimeError):
    def __init__(self, status: int, url: str, text: str):
        super().__init__(f"HTTP {status} from {url}: {(text or '')[:300]}")
        self.status, self.url, self.text = status, url, text or ""


def _as_list(parsed) -> list:
    """A JSON array, or an object wrapping one (RMS list calls are arrays, but be tolerant of wrappers)."""
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        for k in ("searchItems", "items", "data", "results", "accounts", "policies", "portfolios", "exposures"):
            if isinstance(parsed.get(k), list):
                return parsed[k]
    return []


# ---------------------------------------------------------------- normalisers (tolerant of key names)
def norm_portfolio(it: dict) -> dict:
    return {"id": gj(it, "portfolioId") or gj(it, "id"), "name": gj(it, "portfolioName") or gj(it, "name"),
            "number": gj(it, "portfolioNumber") or gj(it, "number")}


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
def copy_body(exposure_id, base_portfolio_id, name: str, number: str, settings: dict | None = None) -> dict:
    s = dict(DEFAULT_COPY_SETTINGS)
    s.update({"name": name, "number": number})
    s.update(settings or {})
    return {"resourceUri": f"/platform/riskdata/v1/exposures/{exposure_id}/portfolios/{base_portfolio_id}",
            "resourceType": "PORTFOLIO", "settings": s}


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


# ---------------------------------------------------------------- the client
class LayerApi:
    def __init__(self, svc):
        self.svc = svc
        self.http = svc.http
        self.c = svc.c

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

    def _put(self, url: str, body: dict) -> dict:
        r = self.http._do("PUT", url, self._key(), body=json.dumps(body))
        return {"ok": 200 <= r.status < 300, "http": r.status, "text": (r.text or "")[:600], "body": body}

    # ---- reads
    def list_portfolios(self, exposure_id) -> list:
        return [norm_portfolio(i) for i in self._paged(f"{self._exp(exposure_id)}/portfolios")]

    def list_portfolio_accounts(self, exposure_id, portfolio_id) -> list:
        return [norm_account(i) for i in self._paged(f"{self._exp(exposure_id)}/portfolios/{portfolio_id}/accounts")]

    def list_accounts(self, exposure_id) -> list:
        return [norm_account(i) for i in self._paged(f"{self._exp(exposure_id)}/accounts")]

    def list_policies(self, exposure_id, account_id) -> list:
        """Policies of one account. Tries the filtered search first, then the nested path; the first that returns any wins."""
        urls = (f"{self._exp(exposure_id)}/policies?filter={quote('accountId=' + str(account_id), safe='')}",
                f"{self._exp(exposure_id)}/accounts/{account_id}/policies")
        last, answered = None, False
        for url in urls:
            try:
                items = self._paged(url)
            except LayerApiError as exc:
                last = exc
                continue
            answered = True
            pols = [norm_policy(i) for i in items]
            pols = [p for p in pols if not p["account_id"] or p["account_id"] == str(account_id)]
            if pols:
                return pols
        if answered:
            return []
        raise last

    # ---- copy job
    def submit_copy_job(self, exposure_id, base_portfolio_id, name: str, number: str, settings: dict | None = None) -> dict:
        body = copy_body(exposure_id, base_portfolio_id, name, number, settings)
        rg = self.c.grouping_resource_group_id or self.svc.resource_group_id()
        headers = {"x-rms-resource-group-id": rg} if rg else None
        url = f"https://{self.c.host}/platform/copy/v1/jobs"
        r = self.http._do("POST", url, self._key(), body=json.dumps(body), headers=headers, retries=1)   # never double-submit
        loc = (r.headers or {}).get("Location") or (r.headers or {}).get("location") or ""
        return {"ok": 200 <= r.status < 300 and bool(loc), "http": r.status, "job_url": _job_url(self.c.host, loc),
                "text": (r.text or "")[:600] if not (200 <= r.status < 300) else (r.text or "")[:600] or "(no body)", "body": body}

    def poll_job(self, job_url: str) -> dict:
        r = self.http.get(job_url, self._key(), headers=None)
        status = "UNKNOWN"
        p = json_or_none(r.text) if 200 <= r.status < 300 else None
        if isinstance(p, dict):
            status = str(p.get("status") or p.get("jobStatus") or "UNKNOWN").upper()
        return {"http": r.status, "status": status, "raw": (r.text or "")[:4000]}

    @staticmethod
    def job_new_portfolio_id(raw: str, base_portfolio_id) -> str:
        ids = {i for i in re.findall(r"/portfolios/(\d+)", raw or "") if i != str(base_portfolio_id)}
        return ids.pop() if len(ids) == 1 else ""

    # ---- writes (each reads the current object first, so nothing the object already holds is lost)
    def update_portfolio(self, exposure_id, portfolio_id, name: str, number: str) -> dict:
        url = f"{self._exp(exposure_id)}/portfolios/{portfolio_id}"
        raw = self._get_json(url)
        return self._put(url, portfolio_update_body(raw if isinstance(raw, dict) else {}, name, number))

    def update_account(self, exposure_id, account_id, name: str, number: str) -> dict:
        url = f"{self._exp(exposure_id)}/accounts/{account_id}"
        raw = self._get_json(url)
        return self._put(url, account_update_body(raw if isinstance(raw, dict) else {}, name, number))

    def update_policy_terms(self, exposure_id, policy_id, limit: float, attachment: float, blanket: float) -> dict:
        url = f"{self._exp(exposure_id)}/policies/{policy_id}"
        raw = self._get_json(url)
        pol = norm_policy(raw if isinstance(raw, dict) else {})
        return self._put(url, policy_update_body(pol, limit, attachment, blanket))


def make_api(svc, demo: bool = False):
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
    return LayerApi(svc)
