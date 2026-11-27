"""Auto-run: run every covered peril on every chosen layer, then group, find and pull the results - without clicking through steps.

    submit  ->  wait for the analyses  ->  find them  ->  group perils (layers with 2+ perils)  ->  wait for the groups
            ->  find again  ->  pull EP / RDS results  ->  done

Each call to AutoRunner.advance() does ONE step of work and returns at once (it never sleeps), and the whole state is saved with
the quote after every call. So a page refresh, a closed browser or a restarted app never loses or repeats anything: the next call
carries on. Submitting is idempotent (the pipeline skips a layer + peril that was already submitted), so pressing the button
twice cannot run an analysis twice.

No Streamlit and no RMS calls in here: it drives the account pipeline (cra/pipeline.py) through its public methods only.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Callable

OK = {"FINISHED", "SUCCEEDED", "COMPLETED"}
BAD = {"FAILED", "SUBMIT_FAILED", "CANCELED", "CANCELLED", "ERROR"}
STAGES = [("submit", "Submit the analyses"), ("wait_analyses", "Wait for RMS to finish them"), ("find", "Find the analyses"),
          ("group", "Group the perils (All Perils)"), ("wait_groups", "Wait for the grouping"), ("find2", "Find the grouped analyses"),
          ("pull", "Pull the results"), ("done", "Done")]
STAGE_KEYS = [k for k, _ in STAGES]
MAX_WAIT_SECONDS = 4 * 3600


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pid(v) -> str:
    try:
        return str(int(float(v)))
    except (TypeError, ValueError):
        return str(v or "")


def new_state(targets: list, options: dict | None = None) -> dict:
    """targets: [{'portfolio_id', 'layer', 'perils': ['EQ', 'FL', ...]}] - what to run."""
    opts = {"group": True, "pull": True, "include_rl": True}
    opts.update(options or {})
    return {"id": uuid.uuid4().hex[:10], "created": now_iso(), "updated": now_iso(), "status": "RUNNING", "stage": "submit",
            "targets": [{"portfolio_id": _pid(t["portfolio_id"]), "layer": t.get("layer", ""), "perils": list(t["perils"])}
                        for t in targets if t.get("perils")],
            "options": opts, "submitted": [], "issues": [], "notes": [], "waiting": "", "error": "", "log": [],
            "found": 0, "grouped": [], "pulled": 0, "started_wait": None}


def planned(state: dict) -> set:
    return {(t["portfolio_id"], c) for t in state["targets"] for c in t["perils"]}


def progress(state: dict) -> float:
    """0..1 for a progress bar."""
    if state["status"] == "DONE":
        return 1.0
    i = STAGE_KEYS.index(state["stage"]) if state["stage"] in STAGE_KEYS else 0
    return min(i / (len(STAGE_KEYS) - 1), 1.0)


def profile_check(records: list, profiles: dict) -> list:
    """Warn when an analysis ran with a model profile other than the one your existing analyses use (names from analysis_profiles.json)."""
    ref = {p["code"]: (p.get("reference_profile") or "").strip() for p in (profiles or {}).get("perils", [])}
    out = []
    for r in records or []:
        want, got = ref.get(r.get("perilCode", ""), ""), (r.get("modelProfile") or "").strip()
        if want and got and want.lower() != got.lower():
            out.append(f"{r.get('layer', '')} {r.get('peril') or r.get('perilCode')}: ran with model profile '{got}', "
                       f"your existing analyses use '{want}'. Check the model settings.")
    return out


class AutoRunner:
    def __init__(self, pipeline, fetch_all: Callable[[], list], save: Callable[[dict], None] | None = None,
                 clock: Callable[[], float] = time.time):
        self.p, self.fetch_all, self.clock = pipeline, fetch_all, clock
        self.save = save or (lambda s: pipeline.put("autorun", s))

    # ------------------------------------------------------------ driver
    def advance(self, state: dict) -> dict:
        if state["status"] != "RUNNING" or state["stage"] == "done":
            return state
        stage = state["stage"]
        try:
            getattr(self, f"_{stage}")(state)
        except Exception as exc:                                               # noqa: BLE001
            state["status"], state["error"] = "FAILED", f"{STAGES[STAGE_KEYS.index(stage)][1]}: {type(exc).__name__}: {exc}"
            self._log(state, f"FAILED at {stage}: {exc}")
        state["updated"] = now_iso()
        if state["stage"] == "done" and state["status"] == "RUNNING":
            state["status"] = "DONE"
        self.save(state)
        return state

    def resume(self, state: dict) -> dict:
        if state["status"] == "FAILED":
            state["status"], state["error"] = "RUNNING", ""
            self.save(state)
        return state

    def cancel(self, state: dict) -> dict:
        if state["status"] == "RUNNING":
            state["status"], state["waiting"] = "CANCELLED", ""
            self._log(state, "stopped by the user (anything already submitted keeps running in RMS)")
            self.save(state)
        return state

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _log(state, msg):
        state["log"].append(f"{now_iso()[11:19]}  {msg}")
        del state["log"][:-100]

    def _next(self, state, stage):
        state["stage"], state["waiting"], state["started_wait"] = stage, "", None

    def _check_timeout(self, state):
        t0 = state.get("started_wait")
        if t0 is None:
            state["started_wait"] = self.clock()
        elif self.clock() - t0 > MAX_WAIT_SECONDS:
            raise TimeoutError("RMS has not finished after 4 hours - check the jobs in RMS, then Resume")

    # ------------------------------------------------------------ stages
    def _submit(self, state):
        ok = skipped = 0
        for t in state["targets"]:
            try:
                res = self.p.submit_analyses([t["portfolio_id"]], t["perils"])
            except Exception as exc:                                           # noqa: BLE001
                state["issues"].append(f"{t['layer']}: could not submit ({type(exc).__name__}: {exc})")
                continue
            for r in res:
                state["submitted"].append({"portfolio_id": t["portfolio_id"], "layer": t["layer"], "peril": r.get("peril"),
                                           "ok": bool(r.get("ok")), "skipped": r.get("skipped", ""), "error": r.get("error", "")})
                if r.get("ok"):
                    ok += 1
                elif r.get("skipped"):
                    skipped += 1
                else:
                    state["issues"].append(f"{t['layer']} {r.get('peril')}: RMS did not accept it - {str(r.get('error', ''))[:200]}")
        self._log(state, f"submitted {ok} analyses ({skipped} were already submitted)")
        if not ok and not skipped:
            raise RuntimeError("RMS accepted none of the analyses. " + (state["issues"][-1] if state["issues"] else ""))
        self._next(state, "wait_analyses")

    def _jobs(self, state, kind: str, keyfn) -> dict:
        """The newest job per key (a resubmitted combination replaces its failed predecessor)."""
        out = {}
        for j in self.p.jobs():
            if j.get("kind") == kind:
                k = keyfn(j)
                if k is not None:
                    out[k] = j
        return out

    def _wait_analyses(self, state):
        self.p.refresh_jobs()
        want = planned(state)
        jobs = self._jobs(state, "analysis", lambda j: (_pid(j.get("portfolio_id")), j.get("peril")) if (_pid(j.get("portfolio_id")), j.get("peril")) in want else None)
        running = [k for k, j in jobs.items() if j["status"] not in OK | BAD]
        done = sum(1 for j in jobs.values() if j["status"] in OK)
        state["waiting"] = f"{done} of {len(jobs)} analyses finished" if running else ""
        if running:
            self._check_timeout(state)
            return
        for k, j in jobs.items():
            if j["status"] in BAD:
                lay = next((t["layer"] for t in state["targets"] if t["portfolio_id"] == k[0]), k[0])
                state["issues"].append(f"{lay} {k[1]}: the analysis ended as {j['status']}. {str(j.get('error') or '')[:150]}".strip())
        self._log(state, f"{done} analyses finished, {sum(1 for j in jobs.values() if j['status'] in BAD)} failed")
        self._next(state, "find")

    def _find(self, state):
        items = self.p.refresh_analyses(self.fetch_all())
        state["found"] = sum(1 for a in items if a.get("selected"))
        self._log(state, f"found {state['found']} analyses to use")
        self._next(state, "group" if state["options"]["group"] else ("pull" if state["options"]["pull"] else "done"))

    def _group(self, state):
        by_pid = {}
        for a in self.p.analyses():
            if a.get("selected") and not a.get("grouped") and _pid(a.get("exposureResourceId")) in {t["portfolio_id"] for t in state["targets"]}:
                by_pid.setdefault(_pid(a["exposureResourceId"]), []).append(a)
        multi = {pid: lst for pid, lst in by_pid.items() if len({x.get("perilCode") for x in lst}) >= 2}
        single = [lst[0] for lst in by_pid.values() if len({x.get("perilCode") for x in lst}) == 1]
        if single and hasattr(self.p, "set_yy_alias"):                         # one peril: show it as All Perils (the Single_Peril macro)
            self.p.set_yy_alias(set(self.p.yy_alias()) | {a["analysisId"] for a in single})
            state["notes"].append(f"{len(single)} layer(s) have one peril, so it is shown as All Perils")
        if not multi:
            self._log(state, "nothing to group")
            self._next(state, "pull" if state["options"]["pull"] else "done")
            return
        res = self.p.submit_grouping([a for lst in multi.values() for a in lst])
        sent = [str(r.get("portfolio_id")) for r in res if r.get("ok")]
        for r in res:
            if not r.get("ok") and not r.get("skipped"):
                state["issues"].append(f"Grouping portfolio {r.get('portfolio_id')} was not accepted: {str(r.get('error', ''))[:200]}")
        state["grouped"] = sent
        self._log(state, f"grouping submitted for {len(sent)} portfolio(s)")
        self._next(state, "wait_groups" if sent else ("pull" if state["options"]["pull"] else "done"))

    def _wait_groups(self, state):
        self.p.refresh_jobs()
        pids = set(state["grouped"])
        jobs = self._jobs(state, "grouping", lambda j: _pid(j.get("portfolio_id")) if _pid(j.get("portfolio_id")) in pids else None)
        running = [k for k, j in jobs.items() if j["status"] not in OK | BAD]
        state["waiting"] = f"{len(jobs) - len(running)} of {len(jobs)} groupings finished" if running else ""
        if running:
            self._check_timeout(state)
            return
        for k, j in jobs.items():
            if j["status"] in BAD:
                state["issues"].append(f"Grouping for portfolio {k} ended as {j['status']}. {str(j.get('error') or '')[:150]}".strip())
        self._next(state, "find2")

    def _find2(self, state):
        self.p.refresh_analyses(self.fetch_all())
        self._next(state, "pull" if state["options"]["pull"] else "done")

    def _pull(self, state):
        ids = [a["analysisId"] for a in self.p.analyses() if a.get("selected")]
        if not ids:
            state["issues"].append("There were no analyses to pull results from.")
            self._next(state, "done")
            return
        out = self.p.pull_results(ids, include_rl=state["options"].get("include_rl", True), with_rds=True)
        state["pulled"] = out.get("records", 0)
        state["issues"] += [str(e) for e in out.get("errors", [])]
        try:
            state["notes"] += profile_check(self.p.results(), self.p.profiles())
        except Exception:                                                      # noqa: BLE001 - a check must never fail the run
            pass
        self._log(state, f"pulled {state['pulled']} analyses")
        self._next(state, "done")
