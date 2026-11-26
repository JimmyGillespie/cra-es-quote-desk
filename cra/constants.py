"""Shared constants. Each one notes the VBA it came from so the migration stays traceable."""
from __future__ import annotations

import json
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"

# mod_EP.bas  RP_LIST
RP_LIST = [10000, 5000, 1000, 500, 250, 200, 100, 50, 25, 10, 5, 2]
EXPOSURE_TYPE = 8017                 # mod_RDS.bas
RDS_API_LIMIT = 1000
LOC_PAGE_LIMIT = 1000                # Get_Loc_Hazard_Refresh_ALL.bas pageSize
EDM_PAGE_LIMIT = 100                 # Get_EDMandPort.bas
PORTFOLIO_PAGE_LIMIT = 200
ANALYSES_PAGE_SIZE = 5000            # Get_Analyses.bas size=5000

PERSPECTIVES = ("GU", "GR", "RL")

# EP tabs: column order and headings (EP GrUp / layer tabs rows 5-6)
PERIL_COLUMNS = [("YY", "All Perils"), ("WS", "Windstorm"), ("EQ", "EQ"), ("CS", "SCS"),
                 ("WT", "Winterstorm"), ("WF", "Wildfire"), ("FL", "Flood"), ("TR", "Terrorism")]
PERIL_ALIASES = {"TO": "CS", "FR": "WF", "HU": "WS"}      # RunAnalysis.bas labels -> RMS perilCode
PERIL_NAMES = {"YY": "All Perils", "WS": "Windstorm", "EQ": "Earthquake", "CS": "Severe convective storm",
               "WT": "Winterstorm", "WF": "Wildfire", "FL": "Flood", "TR": "Terrorism"}
PERIL_FROM_TEXT = {"multi-peril": "YY", "windstorm": "WS", "hurricane": "WS", "earthquake": "EQ",
                   "severe convective storm": "CS", "winterstorm": "WT", "wildfire": "WF", "flood": "FL",
                   "terrorism": "TR"}

# One consistent, restrained palette for every chart (app and workbook)
PERIL_COLORS = {"YY": "#0B2545", "WS": "#2F6DB5", "EQ": "#B5651D", "CS": "#2A9D8F", "WT": "#8A9BB5",
                "WF": "#C8434B", "FL": "#3A86FF", "TR": "#6D597A"}
SERIES_COLORS = ["#0B2545", "#2F6DB5", "#C8434B", "#2A9D8F", "#B5651D", "#6D597A", "#8A9BB5", "#E0A100"]

RP_PROBABILITY = {10000: 0.0001, 5000: 0.0002, 1000: 0.001, 500: 0.002, 250: 0.004, 200: 0.005,
                  100: 0.01, 50: 0.02, 25: 0.04, 10: 0.1, 5: 0.2, 2: 0.5}
HIGHLIGHT_RP = 250
TAIL_RP = 1000                       # return periods at or above this are 'tail' for reliability checks

GROUPING_TERMINAL = {"FINISHED", "FAILED", "CANCELED", "CANCELLED"}
GROUPING_ACTIVE = {"PENDING", "QUEUED", "RUNNING", "CANCEL_REQUESTED", "CANCELING"}
WORKFLOW_TERMINAL = {"FINISHED", "SUCCEEDED", "COMPLETED", "FAILED", "CANCELLED", "CANCELED", "ERROR"}
WORKFLOW_SUCCESS = {"FINISHED", "SUCCEEDED", "COMPLETED"}

# Review thresholds (the AI Summary spec on Controls: 'TIV delta > 5%', 'RED if majority', ...)
TIV_DELTA_WARN = 0.05
TIV_DELTA_ALERT = 0.10
LARGEST_LOC_WARN = 0.10
COUNTY_CONC_WARN = 0.50
DUP_COORD_WARN = 0.10
CV_MATERIAL = 500
PP_MATERIAL_SHARE = 0.05

DEFAULT_RMS_HOST = "api-euw1.rms.com"
DEFAULT_MAILBOX = "exposuremanagement@amtrustgroup.com"
DEFAULT_QUOTES_ROOT = r"\\uk1nas02\Exposure_Management\AmTrust US\Property\E&S\Quotes\2026"

LOC_COLUMNS = [
    "LocID", "Location Name", "Account Name", "Account Number",
    "Location TIV", "Buildings TIV", "Contents TIV", "BI TIV",
    "Construction Name", "Construction Scheme", "Construction Code",
    "Occupancy Name", "Occupancy Scheme", "Occupancy Code",
    "# of Stories", "Year Built", "User Text 1", "User Text 2", "User Defined 1", "User Defined 2",
    "Country", "CRESTA", "Admin1", "Admin2", "Admin3", "City", "Admin4", "Postal Code", "Street Address",
    "Latitude", "Longitude", "Geocoding Resolution", "Perils",
    "Year Built Band", "Stories Band", "Lat,Long",
]
HAZARD_COLUMNS = ["floodZone", "annualProbability", "DTC", "soilDescription", "liquefactionDescription",
                  "landslideDescription", "mmi100yr", "MMI_Description"]
WIND_COLUMN = "Wind Tier"
ALL_LOC_COLUMNS = LOC_COLUMNS + HAZARD_COLUMNS + [WIND_COLUMN]

MMI_DESC = {1: "Not Felt", 2: "Weak", 3: "Weak", 4: "Light", 5: "Moderate", 6: "Strong",
            7: "Very Strong", 8: "Severe", 9: "Violent", 10: "Extreme", 11: "Extreme", 12: "Extreme"}

STATE_NAME_TO_CODE = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA", "Colorado": "CO",
    "Connecticut": "CT", "Delaware": "DE", "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
    "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA",
    "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN",
    "Mississippi": "MS", "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY", "North Carolina": "NC",
    "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR", "Pennsylvania": "PA",
    "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX",
    "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV",
    "Wisconsin": "WI", "Wyoming": "WY", "District Of Columbia": "DC",
}
STATE_CODE_TO_NAME = {v: k for k, v in STATE_NAME_TO_CODE.items()}


def load_events() -> dict:
    """{eventId: name} for the RDS Events tab."""
    return {k: v["name"] for k, v in json.loads((DATA_DIR / "events.json").read_text(encoding="utf-8"))["events"].items()}


def load_event_perils() -> dict:
    """{eventId: peril code} - which peril model produces each RDS event."""
    return {k: v["peril"] for k, v in json.loads((DATA_DIR / "events.json").read_text(encoding="utf-8"))["events"].items()}


def load_profiles() -> dict:
    return json.loads((DATA_DIR / "analysis_profiles.json").read_text(encoding="utf-8"))
