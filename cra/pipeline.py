"""Orchestration for one account. No Streamlit in here - the pages call these methods and the tests call them with a
fake RMS service. Every step persists its result to the account store before returning, so a closed browser, a
restarted app or a second user never loses work, and a step can be re-run on its own.
"""
from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import pandas as pd

from .constants import (ALL_LOC_COLUMNS, GROUPING_TERMINAL, HAZARD_COLUMNS, PERSPECTIVES, WORKFLOW_SUCCESS,
                        WORKFLOW_TERMINAL, load_event_perils, load_events, load_profiles)
from .rms.core import gj
from .rms.service import RmsService
from .rules import ep as ep_rules
from .rules import hazards as hz
from .rules import locations as loc_rules
from .rules import matching, review
from .rules.layers import assign_layer
from .rules.rds import merge_rds
from .store import AccountStore, now_iso

Progress = Callable[[float, str], None]


def _np(_f: float, _m: str) -> None:
    pass


def _is_error(h: dict) -> bool:
    return any(h.get(k) == "Error" for k in ("floodZone", "soilDescription", "mmi100yr"))


class AccountPipeline:
    def __init__(self, svc: RmsService | None, store: AccountStore, account_id: str):
        self.svc, self.store, self.id = svc, store, account_id

    # ------------------------------------------------------------ generic part helpers
    def get(self, part: str, default=None):
        return self.store.load_part(self.id, part, default)

    def put(self, part: str, obj, **kw):
        return self.store.save_part(self.id, part, obj, **kw)

    def submission(self) -> dict:
        return self.get("submission", {}) or {}

    def _need_svc(self) -> RmsService:
        if self.svc is None:
            raise RuntimeError("RMS is not connected - set RISKDATA_API_KEY.")
        return self.svc

    def _time(self, step: str, t0: float) -> None:
        t = dict(self.get("timings", {}) or {})
        t[step] = round(time.time() - t0, 1)
        t["updated"] = now_iso()
        self.put("timings", t)

    def timings(self) -> dict:
        return self.get("timings", {}) or {}

    # ------------------------------------------------------------ account creation / lookup
    @classmethod
    def create(cls, svc, store: AccountStore, exposure_name: str, exposure_id: str, **fields) -> "AccountPipeline":
        aid = store.new_account_id(exposure_id, exposure_name)
        p = cls(svc, store, aid)
        sub = p.submission() or {}
        sub.update({"exposure_name": exposure_name, "exposure_id": str(exposure_id)})
        sub.update({k: v for k, v in fields.items() if v not in (None, "")})
        rep = p.put("submission", sub, header={"exposure_name": exposure_name, "exposure_id": str(exposure_id),
                                               "account_name": sub.get("account_name") or exposure_name})
        if not rep.ok:
            raise OSError(rep.text())
        return p

    @classmethod
    def existing_for(cls, svc, store: AccountStore, exposure_id: str) -> "AccountPipeline | None":
        for m in store.list_accounts():
            if str(m.get("exposure_id")) == str(exposure_id):
                return cls(svc, store, m["account_id"])
        return None

    @classmethod
    def open_results(cls, svc, store: AccountStore, exposure_name: str, exposure_id: str, all_analyses: list,
                     progress: Progress = _np, include_rl: bool = True, with_rds: bool = True, **fields) -> "AccountPipeline":
        """One call: open (or create) the quote for an EDM, find its analyses in RMS and pull the results."""
        p = cls.existing_for(svc, store, exposure_id) or cls.create(svc, store, exposure_name, exposure_id, **fields)
        progress(0.02, "Reading the EDM's portfolios")
        p.refresh_portfolios()
        progress(0.08, "Matching analyses to the EDM")
        items = p.refresh_analyses(all_analyses)
        ids = [a["analysisId"] for a in items if a["selected"]]
        if not ids:
            raise LookupError("No analyses were found in RMS for this EDM.")
        p.pull_results(ids, include_rl=include_rl, with_rds=with_rds, progress=lambda f, m: progress(0.1 + 0.9 * f, m))
        return p

    # ------------------------------------------------------------ step 1: portfolios / layers
    def refresh_portfolios(self) -> list:
        ports = self._need_svc().list_portfolios(self.submission()["exposure_id"])
        self.put("portfolios", {"fetched": now_iso(), "items": ports})
        return ports

    def portfolios(self) -> list:
        return (self.get("portfolios", {}) or {}).get("items", [])

    def layer_order(self) -> list:
        return list(dict.fromkeys(p["layer"] for p in self.portfolios() if p["layer"]))

    # ------------------------------------------------------------ step 2: exposure
    def load_exposure(self, progress: Progress = _np, workers: int = 8) -> pd.DataFrame:
        t0 = time.time()
        svc = self._need_svc()
        sub = self.submission()
        eid, name = sub["exposure_id"], sub["exposure_name"]
        progress(0.02, "Downloading locations")
        raw = svc.fetch_locations(eid, lambda m: progress(0.1, m))
        ids = [loc_rules.location_id(lw) for lw in raw]
        progress(0.15, f"Reading cover splits for {len(ids):,} locations")
        details = svc.fetch_location_details(eid, ids, workers=workers,
                                             progress=lambda d, n: progress(0.15 + 0.8 * d / max(n, 1), f"Cover splits {d:,}/{n:,}"))
        failed = [i for i in ids if details.get(i) is None]
        df = loc_rules.rows_to_frame([loc_rules.build_location_row(lw, details.get(i), name) for lw, i in zip(raw, ids)])
        df = self._apply_hazards(df, (self.get("hazards", {}) or {}).get("cache", {}))
        rep = self.put("locations", {"fetched": now_iso(), "rms_count": len(raw), "detail_failures": len(failed),
                                     "rows": df.to_dict("records")}, compress=True)
        if not rep.ok:
            raise OSError(rep.text())
        self._time("locations", t0)
        progress(1.0, f"{len(df):,} locations saved")
        return self.locations()

    def all_locations(self) -> pd.DataFrame | None:
        d = self.get("locations")
        return pd.DataFrame(d["rows"], columns=ALL_LOC_COLUMNS) if d else None

    def account_mode(self) -> tuple:
        sub = self.submission()
        return sub.get("account_mode", "latest"), sub.get("accounts", [])

    def set_account_mode(self, mode: str, accounts: list | None = None) -> None:
        sub = self.submission()
        sub["account_mode"], sub["accounts"] = mode, [str(a) for a in (accounts or [])]
        self.put("submission", sub)

    def locations(self) -> pd.DataFrame | None:
        df = self.all_locations()
        if df is None:
            return None
        mode, accts = self.account_mode()
        return loc_rules.filter_accounts(df, mode, accts)

    def exposure_check(self) -> dict | None:
        d, df = self.get("locations"), self.locations()
        if d is None or df is None:
            return None
        mode, _a = self.account_mode()
        rec = loc_rules.reconcile(df, self.submission().get("tiv_reported"),
                                  expected_count=d["rms_count"] if mode == "all" else None)
        if d.get("detail_failures"):
            rec["flags"].append(("warn", f"{d['detail_failures']:,} location(s): the cover-split call failed, so Building / Contents / BI "
                                         f"are 0 for them. Re-run the exposure load."))
        return rec

    # ------------------------------------------------------------ step 3: hazards (chargeable - manual only)
    @staticmethod
    def _apply_hazards(df: pd.DataFrame, cache: dict) -> pd.DataFrame:
        df = df.copy()
        for i, r in df.iterrows():
            ok, code = hz.eligible(r.to_dict())
            if ok:
                h = cache.get(hz.hazard_key(r["Latitude"], r["Longitude"], code))
                if h:
                    for c in HAZARD_COLUMNS:
                        df.at[i, c] = h.get(c)
        return df

    def hazard_plan(self) -> dict:
        df = self.locations()
        if df is None:
            return {"locations": 0, "eligible": 0, "unique_points": 0, "cached": 0, "to_fetch": 0}
        cache = (self.get("hazards", {}) or {}).get("cache", {})
        keys, elig = set(), 0
        for _, r in df.iterrows():
            ok, code = hz.eligible(r.to_dict())
            if ok:
                elig += 1
                keys.add(hz.hazard_key(r["Latitude"], r["Longitude"], code))
        cached = sum(1 for k in keys if k in cache and not _is_error(cache[k]))
        return {"locations": len(df), "eligible": elig, "unique_points": len(keys), "cached": cached, "to_fetch": len(keys) - cached}

    def enrich_hazards(self, progress: Progress = _np, workers: int = 4, batch: int = 40, retry_errors: bool = True) -> dict:
        t0 = time.time()
        svc = self._need_svc()
        df = self.locations()                       # what the user selected - only these are charged
        if df is None:
            raise RuntimeError("Load the exposure first.")
        cache = dict((self.get("hazards", {}) or {}).get("cache", {}))
        todo = {}
        for _, r in df.iterrows():
            ok, code = hz.eligible(r.to_dict())
            if ok:
                k = hz.hazard_key(r["Latitude"], r["Longitude"], code)
                if k not in cache or (retry_errors and _is_error(cache[k])):
                    todo[k] = (r["Latitude"], r["Longitude"], code)
        keys, done = list(todo), 0
        for i in range(0, len(keys), batch):
            chunk = keys[i:i + batch]
            with ThreadPoolExecutor(max_workers=workers) as pool:
                res = list(pool.map(lambda k: svc.hazards(*todo[k]), chunk))
            for k, h in zip(chunk, res):
                cache[k] = h
            done += len(chunk)
            rep = self.put("hazards", {"updated": now_iso(), "cache": cache}, compress=True)   # resumable after every batch
            progress(done / max(len(keys), 1), f"Hazards {done:,}/{len(keys):,} points")
            if not rep.ok:
                raise OSError(rep.text())
        full, d = self.all_locations(), self.get("locations")
        self.put("locations", {**{k: v for k, v in d.items() if k != "rows"},
                               "rows": self._apply_hazards(full, cache).to_dict("records")}, compress=True)
        self._time("hazards", t0)
        return {"requested": len(keys), "errors": sum(1 for k in todo if _is_error(cache.get(k, {})))}

    # ------------------------------------------------------------ step 4: analyses
    def refresh_analyses(self, all_analyses: list | None = None, log=lambda m: None) -> list:
        svc = self._need_svc() if all_analyses is None else self.svc
        sub = self.submission()
        items = all_analyses if all_analyses is not None else svc.list_analyses(log)
        prev = {a["analysisId"]: a["selected"] for a in self.analyses()}
        out = matching.select_for_edm(sub["exposure_name"], items, self.portfolios(), previous_selection=prev)
        # a NEW run replaces the previous one for the same layer + peril unless the user chose otherwise
        new_keys = {(a["layer"], a["perilCode"]) for a in out if a["analysisId"] not in prev and a["selected"]}
        for a in out:
            if a["analysisId"] in prev and (a["layer"], a["perilCode"]) in new_keys:
                a["selected"] = False
                a["superseded"] = True
        self.put("analyses", {"fetched": now_iso(), "items": out})
        return out

    def analyses(self) -> list:
        return (self.get("analyses", {}) or {}).get("items", [])

    def set_selection(self, selected_ids: set) -> None:
        d = self.get("analyses", {}) or {"items": []}
        for a in d["items"]:
            a["selected"] = a["analysisId"] in selected_ids
        self.put("analyses", d)

    def pull_results(self, analysis_ids: list, include_rl: bool = True, progress: Progress = _np, with_rds: bool = True) -> dict:
        t0 = time.time()
        svc = self._need_svc()
        persp = PERSPECTIVES if include_rl else ("GU", "GR")
        stored = {a["analysisId"]: a for a in self.analyses()}
        ports = self.portfolios()
        records, errors = [], []
        n = len(analysis_ids)
        for i, aid in enumerate(analysis_ids):
            progress(i / max(n, 1) * (0.6 if with_rds else 1.0), f"EP results {i + 1}/{n} (analysis {aid})")
            rec = svc.fetch_analysis(aid, persp)
            if rec is None:
                errors.append(f"Analysis {aid}: metadata could not be read.")
                continue
            rec["layer_raw"] = rec["layer"]
            rec["layer"] = stored[aid]["layer"] if aid in stored else assign_layer(rec, ports)
            if not rec["perilCode"] and aid in stored:
                rec["perilCode"] = stored[aid]["perilCode"]
            records.append(rec)
        cols = ep_rules.results_columns(records)
        self.put("results", {"fetched": now_iso(), "records": records, "include_rl": include_rl})
        rds_rows, rds_warn = [], []
        if with_rds:
            events = self.get("events") or load_events()
            for i, rec in enumerate(records):
                progress(0.6 + 0.4 * i / max(len(records), 1), f"RDS {i + 1}/{len(records)}")
                r = svc.fetch_rds(rec, events)
                rds_rows.append(r)
            df, rds_warn = merge_rds(rds_rows, events, self.layer_order())
            self.put("rds", {"fetched": now_iso(), "per_analysis": rds_rows, "events": events})
        self.put("results_check", {"errors": errors, "rds_warnings": rds_warn})
        self._time("results", t0)
        progress(1.0, "Results saved")
        return {"records": len(records), "errors": errors, "warnings": self.result_checks()}

    def results(self) -> list:
        return (self.get("results", {}) or {}).get("records", [])

    def rds_frame(self) -> pd.DataFrame | None:
        d = self.get("rds")
        if not d:
            return None
        df, _w = merge_rds(d["per_analysis"], d.get("events") or load_events(), self.layer_order())
        return df

    def yy_alias(self) -> set:
        return set((self.get("results_settings", {}) or {}).get("yy_alias", []))

    def set_yy_alias(self, analysis_ids: set) -> None:
        self.put("results_settings", {"yy_alias": sorted(str(a) for a in analysis_ids)})

    def result_columns(self) -> list:
        return ep_rules.results_columns(self.results(), self.yy_alias())

    def result_checks(self) -> list:
        recs = self.results()
        if not recs:
            return []
        chk = self.get("results_check", {}) or {}
        out = [("error", e) for e in chk.get("errors", [])]
        out += ep_rules.check_results(recs, self.result_columns())
        out += [tuple(w) for w in chk.get("rds_warnings", [])]
        return out

    # ------------------------------------------------------------ review (the Controls-tab checklist) and report
    def review(self) -> dict:
        recs, cols = self.results(), self.result_columns()
        return review.run_review(sub=self.submission(), locations=self.locations(), recon=self.exposure_check(),
                                 portfolios=self.portfolios(), records=recs, cols=cols, rds_df=self.rds_frame(),
                                 event_perils=load_event_perils(), layer_order=self.layer_order(),
                                 result_checks=self.result_checks())

    def report_inputs(self) -> dict:
        recs = self.results()
        recon, rev = self.exposure_check(), self.review()
        warnings = (list(recon["flags"]) if recon else []) + self.result_checks()
        return {"submission": self.submission(), "locations": self.locations(), "recon": recon, "records": recs,
                "layer_order": self.layer_order(), "rds": self.rds_frame(), "warnings": warnings, "yy_alias": self.yy_alias(),
                "review": rev, "timings": self.timings(), "event_perils": load_event_perils()}

    def build_report(self) -> bytes:
        from .report.excel import build_workbook
        return build_workbook(**self.report_inputs())

    # ------------------------------------------------------------ jobs: analysis submission + grouping
    def jobs(self) -> list:
        return (self.get("jobs", {}) or {}).get("jobs", [])

    def _save_jobs(self, jobs: list) -> None:
        rep = self.put("jobs", {"updated": now_iso(), "jobs": jobs})
        if not rep.ok:
            raise OSError("Job book could not be saved - stopping before anything else is submitted. " + rep.text())

    def profiles(self) -> dict:
        return self.get("profiles") or load_profiles()

    def submit_analyses(self, portfolio_ids: list, peril_codes: list, force: bool = False, progress: Progress = _np) -> list:
        """One RMS analysis per portfolio x peril. Idempotent: a combination already submitted is skipped unless forced.
        The job book is saved after EVERY submission so a refresh mid-run can never double-submit."""
        svc = self._need_svc()
        sub, prof = self.submission(), self.profiles()
        by_pid = {p["portfolio_id"]: p for p in self.portfolios()}
        perils = {p["code"]: p for p in prof["perils"]}
        jobs, results = self.jobs(), []
        todo = [(pid, code) for pid in portfolio_ids for code in peril_codes]
        bad = ("FAILED", "SUBMIT_FAILED", "CANCELLED", "CANCELED", "ERROR")
        for i, (pid, code) in enumerate(todo):
            progress(i / max(len(todo), 1), f"Submitting portfolio {pid} / {code}")
            existing = [j for j in jobs if j["kind"] == "analysis" and j["portfolio_id"] == str(pid) and j["peril"] == code and j["status"] not in bad]
            if existing and not force:
                results.append({"portfolio_id": pid, "peril": code, "skipped": f"already {existing[0]['status']}"})
                continue
            pf = perils[code]
            r = svc.submit_analysis(sub["exposure_name"], str(pid), pf["model_profile_id"], pf["event_rate_scheme_id"],
                                    prof["output_profile_id"], prof["currency"], prof["global_analysis_settings"])
            job = {"id": uuid.uuid4().hex[:10], "kind": "analysis", "portfolio_id": str(pid),
                   "portfolio_name": (by_pid.get(str(pid)) or {}).get("portfolio_name", ""),
                   "layer": (by_pid.get(str(pid)) or {}).get("layer", ""), "peril": code, "job_id": r["job_id"],
                   "status": "SUBMITTED" if r["ok"] else "SUBMIT_FAILED", "progress": None, "submitted": now_iso(),
                   "last_polled": None, "status_url": "", "error": "" if r["ok"] else f"HTTP {r['http']}: {r['text']}"}
            jobs.append(job)
            self._save_jobs(jobs)
            results.append({"portfolio_id": pid, "peril": code, "ok": r["ok"], "job_id": r["job_id"], "error": job["error"]})
        return results

    def submit_grouping(self, analyses: list, progress: Progress = _np) -> list:
        """One grouping job per portfolio (analysis portfolio ID > 0), as Submit_GroupingJobs_PortfolioID_GT0.
        Existing All Perils results are never members of a group (in the real data they carry a portfolio ID too)."""
        svc = self._need_svc()
        prof = self.profiles()
        groups = {}
        for a in analyses:
            if matching.is_grouped(a):
                continue
            try:
                if float(a.get("exposureResourceId") or 0) > 0:
                    groups.setdefault(str(int(float(a["exposureResourceId"]))), []).append(a)
            except ValueError:
                continue
        jobs, out = self.jobs(), []
        bad = ("FAILED", "SUBMIT_FAILED", "CANCELED", "CANCELLED")
        for i, (pid, members) in enumerate(groups.items()):
            ids = [m["analysisId"] for m in members]
            sig = ",".join(sorted(ids))
            if any(j["kind"] == "grouping" and j.get("signature") == sig and j["status"] not in bad for j in jobs):
                out.append({"portfolio_id": pid, "skipped": "already submitted for these analyses"})
                continue
            progress(i / max(len(groups), 1), f"Grouping portfolio {pid} ({len(ids)} analyses)")
            meta = svc.get_meta(ids[0]) or {}
            name = gj(meta, "analysisName") or f"Grouped_PortfolioID_{pid}"
            r = svc.submit_grouping(svc.grouping_body(ids, name, meta, prof["grouping"], prof["currency"]))
            job = {"id": uuid.uuid4().hex[:10], "kind": "grouping", "portfolio_id": pid, "layer": members[0].get("layer", ""),
                   "peril": "YY", "analysis_ids": ids, "signature": sig, "name": name, "job_url": r["job_url"],
                   "status": "PENDING" if r["ok"] else "SUBMIT_FAILED", "progress": None, "submitted": now_iso(),
                   "last_polled": None, "error": "" if r["ok"] else r["diag"]}
            jobs.append(job)
            self._save_jobs(jobs)
            out.append({"portfolio_id": pid, "ok": r["ok"], "job_url": r["job_url"], "error": job["error"]})
        return out

    def refresh_jobs(self) -> dict:
        svc = self._need_svc()
        jobs = self.jobs()
        for j in jobs:
            if j["status"] in GROUPING_TERMINAL | WORKFLOW_TERMINAL | {"SUBMIT_FAILED"}:
                continue
            if j["kind"] == "grouping" and j.get("job_url"):
                r = svc.poll_grouping(j["job_url"])
                if 200 <= r["http"] < 300:
                    j["status"], j["progress"] = r["status"], r.get("progress")
                    j["ended"] = r.get("endedAt", "")
                else:
                    j["error"] = f"poll HTTP {r['http']}"
            elif j["kind"] == "analysis" and j.get("job_id"):
                st, url, _t = svc.check_workflow(j["job_id"], j.get("status_url", ""))
                if st != "UNKNOWN":
                    j["status"], j["status_url"] = st, url
            j["last_polled"] = now_iso()
        self._save_jobs(jobs)
        return self.job_summary(jobs)

    @staticmethod
    def job_summary(jobs: list) -> dict:
        s = {"total": len(jobs), "active": 0, "done": 0, "failed": 0}
        for j in jobs:
            st = j["status"]
            if st in WORKFLOW_SUCCESS:
                s["done"] += 1
            elif st in ("FAILED", "SUBMIT_FAILED", "CANCELED", "CANCELLED", "ERROR"):
                s["failed"] += 1
            else:
                s["active"] += 1
        return s
