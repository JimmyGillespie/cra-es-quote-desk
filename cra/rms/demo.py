"""Offline stand-in for RMS (set CRA_DEMO=1). Same interface as RmsService, deterministic synthetic data for a
fictional account - so the whole workflow (pick EDM -> exposure -> hazards -> run -> group -> results -> report) can be
tried, demonstrated and tested without keys or network. Nothing here touches RMS.
"""
from __future__ import annotations

import math
import random

from ..constants import RP_LIST
from ..rules import ep as ep_rules
from ..rules.layers import extract_layer, layer_from_portfolio_name
from .core import Creds
from .service import RmsService

DEMO_EDM = "EDM_Demo_Harbor_Properties_Y26"
DEMO_EXPOSURE_ID = "9000001"
LAYERS = [("TIVxs0", None, None), ("10Mxs0", 0.0, 10e6), ("25Mxs0", 0.0, 25e6), ("15Mxs10M", 10e6, 15e6)]
PERIL_SCALE = {"WS": 1.0, "EQ": 0.45, "CS": 0.6, "WF": 0.25}
PERIL_NAMES = {"WS": "Windstorm", "EQ": "Earthquake", "CS": "Severe Convective Storm", "WF": "Wildfire", "YY": "Multi-Peril"}
STATES = [("FLORIDA", "Miami-Dade County", 25.76, -80.19), ("TEXAS", "Harris County", 29.76, -95.37),
          ("CALIFORNIA", "Los Angeles County", 34.05, -118.24), ("NEW YORK", "Kings County", 40.65, -73.95),
          ("PENNSYLVANIA", "Washington County", 40.17, -79.85), ("SOUTH CAROLINA", "Charleston County", 32.78, -79.93)]


class DemoService(RmsService):
    def __init__(self, creds: Creds | None = None):
        super().__init__(creds or Creds(host="demo.invalid", riskdata_key="demo"))
        self._analyses: list = []
        self._jobs: dict = {}
        self._next_id = 2_000_000
        self._locs = self._make_locations()

    # ------------------------------------------------------------ synthetic exposure
    def _make_locations(self) -> list:
        rnd = random.Random(7)
        out = []
        for i in range(1, 31):
            st, county, lat, lon = STATES[i % len(STATES)]
            tiv_b, tiv_c, tiv_bi = rnd.randrange(2, 9) * 500_000, rnd.randrange(1, 4) * 250_000, rnd.randrange(0, 3) * 300_000
            out.append({"tiv": float(tiv_b + tiv_c + tiv_bi), "_det": {
                "maxCvgBldgValuation": tiv_b, "locationCoverages": [
                    {"lossType": "Contents", "value": tiv_c}, {"lossType": "BI", "value": tiv_bi}]},
                "location": {"property": {
                    "locationId": 5000 + i, "locationNumber": i, "locationName": f"Harbor Building {i}", "accountId": 1,
                    "buildingClass": {"name": rnd.choice(["ATC - Masonry", "ATC - Wood Frame", "ATC - Steel"]), "code": rnd.randrange(1, 9)},
                    "buildingClassScheme": {"name": "ATC"},
                    "occupancyType": {"name": rnd.choice(["ATC - Apartments", "ATC - Retail", "ATC - Office"]), "id": rnd.randrange(1, 30)},
                    "occupancyTypeScheme": {"name": "ATC"}, "stories": rnd.choice([1, 2, 3, 5, 8, 12, 0]),
                    "yearBuilt": rnd.choice([1955, 1978, 1994, 2003, 2015, 2022, 9999, 0]), "perils": ["EQ", "WS"]},
                    "address": {"country": {"name": "United States"}, "area": {"name": "Unknown"}, "admin1Name": st,
                                "admin2Name": county.upper(), "cityName": county.split()[0].upper(),
                                "postalCode": f"{10000 + i * 37}", "streetAddress": f"{100 + i} HARBOR STREET",
                                "latitude": round(lat + rnd.uniform(-0.05, 0.05), 6),
                                "longitude": round(lon + rnd.uniform(-0.05, 0.05), 6),
                                "geoResolutionCode": {"name": rnd.choice(["Parcel", "Parcel", "Street", "Postal Code"])}}}})
        return out

    # ------------------------------------------------------------ exposures / portfolios / locations
    def list_exposures(self, log=lambda m: None) -> list:
        return [{"exposureName": DEMO_EDM, "exposureId": DEMO_EXPOSURE_ID},
                {"exposureName": "EDM_Demo_Other_Account_Y26", "exposureId": "9000002"}]

    def list_portfolios(self, exposure_id: str) -> list:
        return [{"portfolio_name": f"{l}_Demo_Harbor_Properties", "portfolio_id": str(i + 1),
                 "layer": layer_from_portfolio_name(f"{l}_Demo_Harbor_Properties")} for i, (l, _a, _b) in enumerate(LAYERS)]

    def fetch_locations(self, exposure_id: str, log=lambda m: None) -> list:
        log(f"{len(self._locs)} locations downloaded")
        return [{k: v for k, v in lw.items() if k != "_det"} for lw in self._locs]

    def fetch_location_detail(self, exposure_id: str, loc_id: str):
        for lw in self._locs:
            if str(lw["location"]["property"]["locationId"]) == str(loc_id):
                return lw["_det"]
        return None

    # ------------------------------------------------------------ analyses
    def _curve(self, peril: str, layer: str, persp: str, mult: float = 1.0) -> dict:
        _l, att, lim = next(x for x in LAYERS if x[0] == layer)
        scale = PERIL_SCALE.get(peril, 1.4) * 4.0e6 * mult
        gu = [scale * (rp ** 0.55) / 10 for rp in RP_LIST]
        if persp == "GU" or att is None:
            vals = gu
        else:
            vals = [min(max(v - att, 0.0), lim) for v in gu]
        mean = sum(v * p for v, p in zip(vals, [0.0001, 0.0002, 0.001, 0.002, 0.004, 0.005, 0.01, 0.02, 0.04, 0.1, 0.2, 0.5])) * 1.8
        return {"AEP": vals, "OEP": [v * 0.93 for v in vals], "stats": [mean, mean * 3.2, 3.2]}

    def _add_analysis(self, portfolio_id: str, layer: str, peril: str, grouped=False) -> dict:
        self._next_id += 7
        aid = str(self._next_id)
        pname = f"{layer}_Demo_Harbor_Properties"
        name = f"{DEMO_EDM}: PORTFOLIO: {pname}"
        a = {"analysisName": name, "analysisId": aid, "appAnalysisId": str(self._next_id // 37), "createDate": "2026-11-24T10:00:00Z",
             "analysisType": "Exceedance Probability", "peril": PERIL_NAMES[peril], "perilCode": peril,
             "exposureResourceId": "0" if grouped else str(portfolio_id), "exposureName": "" if grouped else DEMO_EDM,
             "description": "demo"}
        self._analyses.append(a)
        return a

    def list_analyses(self, log=lambda m: None) -> list:
        return [dict(a) for a in self._analyses] + [
            {"analysisName": "EDM_Someone_Else_Y26: PORTFOLIO: 1M_x", "analysisId": "111", "appAnalysisId": "3", "createDate": "",
             "analysisType": "", "peril": "Earthquake", "perilCode": "EQ", "exposureResourceId": "1", "exposureName": "EDM_Someone_Else_Y26",
             "description": ""}]

    def get_meta(self, analysis_id: str):
        a = next((x for x in self._analyses if x["analysisId"] == str(analysis_id)), None)
        if not a:
            return None
        return {**a, "analysisId": a["analysisId"], "exposureResourceType": "8017", "subPeril": "", "region": "North America",
                "engineVersion": "RL24", "lossAmplification": "Building, Contents, BI",
                "currency": {"currencyCode": "USD", "currencyName": "US Dollar", "currencyVintage": "RL24", "asOfDate": "2024-04-03"},
                "modelProfile": {"name": f"DEMO_{a['perilCode']}"}, "outputProfile": {"name": "DEMO_output"}, "username": "demo"}

    def fetch_analysis(self, analysis_id: str, perspectives=("GU", "GR", "RL"), meta=None):
        meta = self.get_meta(analysis_id)
        if not meta:
            return None
        layer = extract_layer(meta["analysisName"])
        ep_by, st_by = {}, {}
        for p in perspectives:
            yy = meta["perilCode"] == "YY"
            c = self._curve("WS" if yy else meta["perilCode"], layer, "GU" if p == "GU" else "GR", 1.35 if yy else 1.0)
            ep_by[p] = {"AEP": c["AEP"], "OEP": c["OEP"]}
            st_by[p] = c["stats"]
        rec = ep_rules.analysis_record(meta, ep_by, st_by)
        rec["fetchWarnings"] = []
        return rec

    def fetch_rds(self, rec: dict, event_map: dict) -> dict:
        _l, att, lim = next(x for x in LAYERS if x[0] == rec["layer"])
        gu, gr = {}, {}
        for i, eid in enumerate(event_map):
            if (i + (0 if rec["perilCode"] in ("WS", "YY") else 7)) % 3 == 0:
                v = 3.0e7 * (1 + i % 4)
                gu[eid] = v
                gr[eid] = v if att is None else min(max(v - att, 0), lim)
        return {"analysisId": rec["analysisId"], "layer": rec["layer"], "perilCode": rec["perilCode"], "GU": gu, "GR": gr, "log": []}

    # ------------------------------------------------------------ submission / jobs (complete after two polls)
    def resource_group_id(self) -> str:
        return "demo-rg"

    def submit_analysis(self, edm_name, portfolio_id, model_profile_id, event_rate_scheme_id, output_profile_id, currency, global_settings):
        jid = f"wf{len(self._jobs) + 1}"
        self._jobs[jid] = {"polls": 0, "portfolio_id": str(portfolio_id), "model": model_profile_id}
        return {"ok": True, "http": 201, "job_id": jid, "text": ""}

    def check_workflow(self, job_id: str, cached_url: str = ""):
        j = self._jobs.get(job_id)
        if not j:
            return "UNKNOWN", "", ""
        j["polls"] += 1
        if j["polls"] < 2:
            return "RUNNING", f"demo://{job_id}", ""
        if not j.get("made"):
            j["made"] = True
            layer = next((p["layer"] for p in self.list_portfolios("") if p["portfolio_id"] == j["portfolio_id"]), "TIVxs0")
            peril = {90: "WS", 89: "EQ", 91: "CS", 162: "WF"}.get(j["model"], "WS")
            if not any(a["analysisName"].endswith(f"{layer}_Demo_Harbor_Properties") and a["perilCode"] == peril for a in self._analyses):
                self._add_analysis(j["portfolio_id"], layer, peril)
        return "FINISHED", f"demo://{job_id}", ""

    def grouping_body(self, analysis_ids, name, meta, grouping_cfg, currency) -> str:
        return "|".join(analysis_ids) + "#" + name

    def submit_grouping(self, body: str) -> dict:
        jid = f"grp{len(self._jobs) + 1}"
        self._jobs[jid] = {"polls": 0, "group": body}
        return {"ok": True, "http": 201, "job_url": f"demo://{jid}", "diag": ""}

    def poll_grouping(self, job_url: str) -> dict:
        jid = job_url.split("//")[1]
        j = self._jobs[jid]
        j["polls"] += 1
        if j["polls"] < 2:
            return {"http": 200, "status": "RUNNING", "progress": 40.0, "raw": ""}
        if not j.get("made"):
            j["made"] = True
            ids, name = j["group"].split("#", 1)[0].split("|"), j["group"].split("#", 1)[1]
            layer = extract_layer(name)
            pid = next((p["portfolio_id"] for p in self.list_portfolios("") if p["layer"] == layer), "1")
            self._add_analysis(pid, layer, "YY", grouped=True)
        return {"http": 200, "status": "FINISHED", "progress": 100.0, "raw": ""}

    # ------------------------------------------------------------ hazards (deterministic)
    def hazards(self, lat, lon, code: str) -> dict:
        r = random.Random(f"{lat}{lon}")
        mmi = r.choice([5, 6, 7, 8])
        from ..constants import MMI_DESC
        return {"floodZone": r.choice(["X", "AE", "SHX", "VE"]), "annualProbability": r.choice(["Between 0.2% and 1%", "Greater than 1%"]),
                "DTC": r.choice([">10 miles", 2.5, 0.8, 6.1]), "soilDescription": r.choice(["Rock to Soft Rock", "Soft Soil"]),
                "liquefactionDescription": r.choice(["Very Low to Low", "Moderate"]), "landslideDescription": "Very Low",
                "mmi100yr": mmi, "MMI_Description": MMI_DESC[mmi]}

    def test_connections(self) -> list:
        return [(n, True, "demo mode") for n in ("Risk Data API", "Risk Modeler API", "Grouping API", "Location Intelligence")]
