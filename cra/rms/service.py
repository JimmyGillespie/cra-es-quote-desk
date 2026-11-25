"""RMS service layer: every API call the CRA workbook made, in one place.

Endpoints (all from the VBA):
  riskdata/v1    exposures, portfolios, locations, analyses, ep, stats
  riskmodeler/v2 portfolios?datasource=, portfolios/{id}/process, analyses/{appId}/elt
  grouping/v1    jobs
  li/<layer>/latest   Location Intelligence
Authorization is the bare key (no 'Bearer'), as in the VBA and the EQ-RMS app.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from ..constants import (ANALYSES_PAGE_SIZE, EDM_PAGE_LIMIT, EXPOSURE_TYPE, LOC_PAGE_LIMIT, PERSPECTIVES,
                         PORTFOLIO_PAGE_LIMIT, RDS_API_LIMIT)
from ..rules import ep as ep_rules
from ..rules import hazards as hz
from ..rules.layers import extract_layer, layer_from_portfolio_name, limit_group_name
from ..rules.rds import parse_elt
from .core import (Creds, Http, as_list, clean_key, gj, gobj, gv, json_or_none, key_fingerprint, safe_get,
                   to_num, url_encode)

Log = Callable[[str], None]


def _noop(_m: str) -> None:
    pass


class RmsError(RuntimeError):
    def __init__(self, status: int, url: str, body: str):
        super().__init__(f"HTTP {status} from {url}: {body[:300]}")
        self.status, self.url, self.body = status, url, body


class RmsService:
    def __init__(self, creds: Creds, http: Http | None = None):
        self.c = creds
        self.http = http or Http(verify=creds.verify)
        self._rg: str | None = None

    # ------------------------------------------------------------ urls
    @property
    def riskdata(self) -> str:
        return f"https://{self.c.host}/platform/riskdata/v1"

    @property
    def modeler_v2(self) -> str:
        return f"https://{self.c.host}/riskmodeler/v2"

    def _get_json(self, url: str, key: str | None = None):
        r = self.http.get(url, key or self.c.riskdata_key)
        if r.status != 200:
            raise RmsError(r.status, url, r.text)
        return json_or_none(r.text)

    # ------------------------------------------------------------ exposures / portfolios  (Get_EDMandPort.bas)
    def list_exposures(self, log: Log = _noop) -> list:
        out, offset = [], 0
        while True:
            url = f"{self.riskdata}/exposures?limit={EDM_PAGE_LIMIT}&offset={offset}"
            items = as_list(self._get_json(url))
            if not items:
                break
            for it in items:
                name, eid = gj(it, "exposureName").strip(), gj(it, "exposureId").strip()
                if name and eid:
                    out.append({"exposureName": name, "exposureId": eid})
            log(f"{len(out)} exposures")
            if len(items) < EDM_PAGE_LIMIT:
                break
            offset += EDM_PAGE_LIMIT
        return out

    def list_portfolios(self, exposure_id: str) -> list:
        out, offset = [], 0
        while True:
            url = f"{self.riskdata}/exposures/{exposure_id}/portfolios?limit={PORTFOLIO_PAGE_LIMIT}&offset={offset}"
            items = as_list(self._get_json(url))
            if not items:
                break
            for it in items:
                nm, pid = gj(it, "portfolioName").strip(), gj(it, "portfolioId").strip()
                out.append({"portfolio_name": nm, "portfolio_id": pid, "layer": layer_from_portfolio_name(nm)})
            if len(items) < PORTFOLIO_PAGE_LIMIT:
                break
            offset += PORTFOLIO_PAGE_LIMIT
        return out

    # ------------------------------------------------------------ locations
    def fetch_locations(self, exposure_id: str, log: Log = _noop) -> list:
        out, offset = [], 0
        while True:
            url = f"{self.riskdata}/exposures/{exposure_id}/locations?limit={LOC_PAGE_LIMIT}&offset={offset}"
            r = self.http.get(url, self.c.riskdata_key)
            if r.status != 200:
                raise RmsError(r.status, url, r.text)          # never return a silently short list
            items = json_or_none(r.text)
            if not isinstance(items, list) or not items:
                break
            out.extend(items)
            log(f"{len(out):,} locations downloaded")
            if len(items) < LOC_PAGE_LIMIT:
                break
            offset += LOC_PAGE_LIMIT
            if offset > 500000:
                break
        return out

    def fetch_location_detail(self, exposure_id: str, loc_id: str):
        if not loc_id:
            return None
        r = self.http.get(f"{self.riskdata}/exposures/{exposure_id}/locations/{loc_id}", self.c.riskdata_key)
        return json_or_none(r.text) if r.status == 200 else None

    def fetch_location_details(self, exposure_id: str, loc_ids: list, workers: int = 8,
                               progress: Callable[[int, int], None] | None = None) -> dict:
        """{loc_id: detail or None}. Failures are returned as None so the caller can count and report them."""
        out, done = {}, 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for lid, d in zip(loc_ids, pool.map(lambda i: self.fetch_location_detail(exposure_id, i), loc_ids)):
                out[lid] = d
                done += 1
                if progress and (done % 25 == 0 or done == len(loc_ids)):
                    progress(done, len(loc_ids))
        return out

    # ------------------------------------------------------------ analyses (Get_Analyses.bas)
    def list_analyses(self, log: Log = _noop) -> list:
        out, offset = [], 0
        while True:
            url = f"{self.riskdata}/analyses?sort=createDate%20desc&size={ANALYSES_PAGE_SIZE}"
            if offset:
                url += f"&offset={offset}"
            items = as_list(self._get_json(url))
            if not items:
                break
            for it in items:
                aid = gj(it, "analysisId")
                if not aid:
                    continue
                out.append({
                    "analysisName": gj(it, "analysisName"), "analysisId": aid,
                    "appAnalysisId": gj(it, "appAnalysisId") or gj(it, "appAnalysisID"),
                    "createDate": gj(it, "createDate"),
                    "analysisType": gj(it, "analysisType") or gj(it, "analysisRegroupType"),
                    "peril": gj(it, "peril"), "perilCode": gj(it, "perilCode"),
                    "exposureResourceId": gj(it, "exposureResourceId"), "exposureName": gj(it, "exposureName"),
                    "description": gj(it, "description"),
                })
            log(f"{len(out):,} analyses")
            if len(items) < ANALYSES_PAGE_SIZE:
                break
            offset += ANALYSES_PAGE_SIZE
        return out

    def get_meta(self, analysis_id: str):
        r = self.http.get(f"{self.riskdata}/analyses/{analysis_id}", self.c.riskdata_key)
        if r.status != 200:
            return None
        p = json_or_none(r.text)
        if isinstance(p, list):
            return p[0] if p and isinstance(p[0], dict) else None
        return p if isinstance(p, dict) else None

    def fetch_analysis(self, analysis_id: str, perspectives=PERSPECTIVES, meta=None) -> dict | None:
        """Meta + EP + stats for one analysis (GetEPResults_Simple_Combined). None when the meta call fails."""
        analysis_id = str(analysis_id)
        meta = meta or self.get_meta(analysis_id)
        if not meta:
            return None
        meta = dict(meta)
        meta.setdefault("analysisId", analysis_id)
        exp_type = gj(meta, "exposureResourceType") or str(EXPOSURE_TYPE)
        exp_id = gj(meta, "exposureResourceId")
        base = f"{self.riskdata}/analyses/{analysis_id}"
        jobs = []
        for p in perspectives:
            jobs.append((p, "ep", f"{base}/ep?exposureResourceType={exp_type}&exposureResourceId={exp_id}&perspectiveCode={p}"))
            jobs.append((p, "stats", f"{base}/stats?perspectiveCode={p}&exposureResourceId={exp_id}&exposureResourceType={exp_type}"))
        with ThreadPoolExecutor(max_workers=6) as pool:
            resps = list(pool.map(lambda j: self.http.get(j[2], self.c.riskdata_key), jobs))
        ep_by, st_by, failed = {}, {}, []
        for (p, kind, url), r in zip(jobs, resps):
            if kind == "ep":
                if r.status == 200 and r.text:
                    parsed = ep_rules.parse_ep(r.text)
                    if parsed:
                        ep_by[p] = parsed
                elif r.status not in (404,):
                    failed.append(f"{p} ep HTTP {r.status}")
            else:
                st_by[p] = list(ep_rules.parse_stats(r.text)) if (r.status == 200 and r.text) else [None, None, None]
        rec = ep_rules.analysis_record(meta, ep_by, st_by)
        rec["fetchWarnings"] = failed
        return rec

    def fetch_rds(self, rec: dict, event_map: dict) -> dict:
        """GU and GR event losses for one analysis (GetRMS_RDS_Events_GU_AllGR). Uses the short app ID with riskmodeler v2."""
        rds_id = rec.get("appAnalysisId") or rec["analysisId"]
        out = {"analysisId": rec["analysisId"], "layer": rec["layer"], "perilCode": rec["perilCode"],
               "GU": {}, "GR": {}, "log": []}
        meta = self._get_json(f"{self.modeler_v2}/analyses/{rds_id}", self.c.riskmodeler_key) if rds_id else None
        if isinstance(meta, list):
            meta = meta[0] if meta else None
        exp_id = _exposure_id_from_meta(meta if isinstance(meta, dict) else {})
        if not exp_id:
            out["log"].append(f"{rds_id}: no exposureId on the analysis meta - RDS skipped")
            return out
        ids_q = "&".join(f"eventIds={k}" for k in event_map)
        for persp in ("GU", "GR"):
            url = (f"{self.modeler_v2}/analyses/{rds_id}/elt?limit={RDS_API_LIMIT}&exposureId={exp_id}"
                   f"&exposureType={EXPOSURE_TYPE}&perspective={persp}&{ids_q}")
            r = self.http.get(url, self.c.riskmodeler_key)
            out["log"].append(f"{persp}: HTTP {r.status}")
            if r.status == 200:
                out[persp] = {k: v for k, v in parse_elt(r.text).items() if k in event_map}
        return out

    # ------------------------------------------------------------ analysis submission (RunAnalysis.bas)
    def resource_group_id(self) -> str:
        if self._rg is None:
            self._rg = ""
            r = self.http.get(f"https://{self.c.host}/platform/tenantdata/v1/entitlements/RI-RISKMODELER/resourcegroups",
                              self.c.riskdata_key)
            if r.status == 200:
                p = json_or_none(r.text)
                items = p if isinstance(p, list) else next(
                    (p[k] for k in ("resourceGroups", "items", "data") if isinstance(p, dict) and isinstance(p.get(k), list)), [])
                if items and isinstance(items[0], dict):
                    self._rg = str(items[0].get("resourceGroupId") or items[0].get("id") or "")
        return self._rg

    def list_modeler_portfolios(self, edm_name: str) -> list:
        """riskmodeler/v2/portfolios?datasource=<edm> -> [{'id', 'name'}] (RunRMSAnalysis step 1)."""
        j = self._get_json(f"{self.modeler_v2}/portfolios?datasource={url_encode(edm_name)}", self.c.riskmodeler_key)
        items = j.get("searchItems") if isinstance(j, dict) else as_list(j)
        return [{"id": gj(i, "id"), "name": gj(i, "name")} for i in (items or []) if isinstance(i, dict)]

    def submit_analysis(self, edm_name: str, portfolio_id: str, model_profile_id: int, event_rate_scheme_id: int,
                        output_profile_id: int, currency: dict, global_settings: dict) -> dict:
        url = f"{self.modeler_v2}/portfolios/{portfolio_id}/process"
        body = json.dumps({
            "currency": {"code": currency["code"], "scheme": currency.get("scheme", "RMS"),
                         "asOfDate": currency["asOfDate"], "vintage": currency["vintage"]},
            "globalAnalysisSettings": global_settings, "edm": edm_name, "exposureType": "PORTFOLIO",
            "modelProfileId": model_profile_id, "eventRateSchemeId": event_rate_scheme_id,
            "id": str(portfolio_id), "outputProfileId": output_profile_id})
        rg = self.resource_group_id()
        r = self.http.post(url, self.c.riskmodeler_key, body, headers={"x-rms-resource-group-id": rg} if rg else None)
        job = ""
        if 200 <= r.status < 300:
            p = json_or_none(r.text)
            if isinstance(p, dict):
                job = str(p.get("id") or p.get("jobId") or p.get("workflowId") or "")
            if not job:
                loc = r.headers.get("Location") or r.headers.get("location") or ""
                job = loc.rstrip("/").split("/")[-1] if loc else ""
        return {"ok": 200 <= r.status < 300 and bool(job), "http": r.status, "job_id": job, "text": r.text[:500]}

    def check_workflow(self, job_id: str, cached_url: str = "") -> tuple:
        """-> (STATUS, url, text). Probes the three workflow endpoints the EQ-RMS app uses."""
        urls = [cached_url] if cached_url else [
            f"https://{self.c.host}/riskmodeler/v1/workflows/{job_id}",
            f"https://{self.c.host}/platform/v1/workflows/{job_id}",
            f"https://{self.c.host}/riskmodeler/v2/workflows/{job_id}"]
        for u in urls:
            r = self.http.get(u, self.c.riskmodeler_key)
            if r.status == 200:
                p = json_or_none(r.text)
                st = str((p or {}).get("status") or (p or {}).get("jobStatus") or "").upper() if isinstance(p, dict) else ""
                return (st or "UNKNOWN"), u, r.text
        return "UNKNOWN", "", ""

    # ------------------------------------------------------------ grouping (Post_GroupAnalysis.bas)
    def _grouping_headers(self) -> dict:
        rg = self.c.grouping_resource_group_id or self.resource_group_id()
        return {"x-rms-resource-group-id": rg} if rg else {}

    def grouping_body(self, analysis_ids: list, name: str, meta: dict, grouping_cfg: dict, currency: dict) -> str:
        cur = gobj(meta or {}, "currency")
        body = {
            "resourceType": "analyses",
            "resourceUris": [f"/platform/riskdata/v1/analyses/{str(i).strip()}" for i in analysis_ids],
            "settings": {
                "analysisName": limit_group_name(name, grouping_cfg.get("max_analysis_name", 64)),
                "description": "",
                "simulateToPLT": grouping_cfg.get("simulate_to_plt", True),
                "propagateDetailedLosses": grouping_cfg.get("propagate_detailed_losses", False),
                "numOfSimulations": grouping_cfg.get("num_simulations", 50000),
                "currency": {"code": gj(cur, "currencyCode") or currency["code"], "scheme": "RMS",
                             "vintage": gj(cur, "currencyVintage") or currency["vintage"],
                             "asOfDate": gj(cur, "asOfDate") or currency["asOfDate"]},
                "regionPerilSimulationSet": grouping_cfg["region_peril_simulation_set"],
            },
        }
        return json.dumps(body, separators=(",", ":"))

    def submit_grouping(self, body: str) -> dict:
        """POST grouping/v1/jobs. Tries the key as entered, then with 'Bearer ' (what worked for the EQ-RMS app)."""
        url = f"https://{self.c.host}/platform/grouping/v1/jobs"
        raw = clean_key(self.c.grouping_key)
        if not raw:
            return {"ok": False, "http": 0, "job_url": "", "diag": "No Grouping API key (GROUPING_API_KEY) is set."}
        auths = [self.c.grouping_auth] if self.c.grouping_auth else [raw, "Bearer " + raw]
        attempts, r, r403 = [], None, None
        for auth in auths:
            r = self.http.post(url, auth, body, headers=self._grouping_headers())
            if r.status == 403:
                r403 = r
            attempts.append(f"{'key as entered' if auth == raw else 'with Bearer prefix'}: HTTP {r.status} {r.text[:300]!r}")
            if r.status not in (401, 403):
                self.c.grouping_auth = auth
                break
        if r403 is not None and r is not None and r.status == 401:
            r = r403
        status = r.status if r is not None else 0
        loc = (r.headers.get("Location") or r.headers.get("location") or "") if r is not None else ""
        job_url = _normalize_job_url(self.c.host, loc) if 200 <= status < 300 else ""
        diag = "\n".join(attempts) + f"\nGrouping key sent: {key_fingerprint(raw)}\nLocation: {loc!r}"
        if status in (401, 403):
            diag += ("\nRMS rejected the Grouping API key or resource group "
                     "(set GROUPING_API_KEY and, if needed, GROUPING_RESOURCE_GROUP_ID).")
        return {"ok": bool(job_url), "http": status, "job_url": job_url, "diag": diag}

    def poll_grouping(self, job_url: str) -> dict:
        r = self.http.get(job_url, self.c.grouping_auth or clean_key(self.c.grouping_key), headers=self._grouping_headers())
        out = {"http": r.status, "status": "UNKNOWN", "progress": None, "raw": r.text[:2000]}
        if 200 <= r.status < 300:
            p = json_or_none(r.text)
            if isinstance(p, dict):
                out["status"] = str(p.get("status") or "UNKNOWN").upper()
                pr = to_num(p.get("progress"))
                if pr is None:
                    tasks = p.get("tasks")
                    if isinstance(tasks, list) and tasks and isinstance(tasks[0], dict):
                        pr = to_num(tasks[0].get("percentComplete"))
                out["progress"] = pr
                for k in ("submittedAt", "startedAt", "endedAt"):
                    out[k] = gj(p, k)
        return out

    # ------------------------------------------------------------ Location Intelligence
    def hazards(self, lat, lon, code: str) -> dict:
        base = f"https://{self.c.host}/li"
        key = self.c.hazard_key

        def post(layer: str, body: dict):
            r = self.http.post(f"{base}/{layer}/latest", key, json.dumps(body))
            return json_or_none(r.text) if r.status != 0 else None

        out = hz.empty_hazards()
        out.update(hz.parse_flood(post("us_fema_firm", hz.address_body(lat, lon, code))))
        out["DTC"] = hz.parse_dtc(post("us_distance_to_inner_coast", hz.dtc_body(lat, lon, code)))
        out.update(hz.parse_eq(post("na_eq_hazard", hz.address_body(lat, lon, code))))
        out.update(hz.parse_mmi(post("us_mmi", hz.address_body(lat, lon, code))))
        return out

    # ------------------------------------------------------------ health check
    def test_connections(self) -> list:
        """One cheap call per key -> [(name, ok, message)]."""
        res = []

        def chk(name, url, key, extra=None):
            if not key:
                res.append((name, False, "not set"))
                return
            r = self.http.get(url, key, headers=extra)
            ok = 200 <= r.status < 300
            res.append((name, ok, "OK" if ok else f"HTTP {r.status} - {key_fingerprint(key)}"))
        chk("Risk Data API", f"{self.riskdata}/exposures?limit=1", self.c.riskdata_key)
        chk("Risk Modeler API", f"{self.modeler_v2}/portfolios?datasource=_probe_", self.c.riskmodeler_key)
        gk = self.c.grouping_key
        if gk:
            r = self.http.get(f"https://{self.c.host}/platform/grouping/v1/jobs?limit=1", gk, headers=self._grouping_headers())
            if r.status in (401, 403):
                r = self.http.get(f"https://{self.c.host}/platform/grouping/v1/jobs?limit=1", "Bearer " + gk,
                                  headers=self._grouping_headers())
            ok = r.status not in (0, 401, 403)
            res.append(("Grouping API", ok, "OK" if ok else f"HTTP {r.status} - {key_fingerprint(gk)}"))
        else:
            res.append(("Grouping API", False, "not set"))
        if self.c.hazard_key:
            r = self.http.post(f"https://{self.c.host}/li/us_mmi/latest", self.c.hazard_key,
                               json.dumps(hz.address_body(40.17135, -79.853901, "PA")))
            ok = 200 <= r.status < 300
            res.append(("Location Intelligence", ok, "OK" if ok else f"HTTP {r.status} - {key_fingerprint(self.c.hazard_key)}"))
        else:
            res.append(("Location Intelligence", False, "not set"))
        return res


def _normalize_job_url(host: str, loc: str) -> str:
    loc = (loc or "").strip()
    if not loc:
        return ""
    if loc.lower().startswith("http"):
        return loc
    return f"https://{host}{loc}" if loc.startswith("/") else f"https://{host}/{loc}"


def _exposure_id_from_meta(js: dict) -> str:
    v = gj(js, "exposureId")
    if v:
        return v
    v = gj(gobj(js, "exposure"), "id")
    if v:
        return v
    exps = gv(js, "exposures")
    if isinstance(exps, list):
        for it in exps:
            v = gj(it, "id")
            if v:
                return v
    return ""
