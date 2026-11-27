"""Shared constants. Each one notes the VBA it came from so the migration stays traceable."""
from __future__ import annotations

import copy
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


# ---------------------------------------------------------------- reference data
# The RDS events and the default model profiles are built in, so a missing, damaged or older-format file in cra/data can
# never stop the app. cra/data/events.json and cra/data/analysis_profiles.json are optional: anything valid in them is added
# to (events) or replaces (profiles) the built-in values. Both file layouts are read:  {"events": {id: {"name", "peril"}}}
# (current) and {"events": {id: "name"}} (older).
BUILTIN_EVENTS = {
    '2874165': ('North East Windstorm', 'WS'),
    '2867710': ('Carolinas Windstorm', 'WS'),
    '2848918': ('Gulf of Mexico Windstorm', 'WS'),
    '2865409': ('Miami-Dade Windstorm', 'WS'),
    '2869298': ('Pinellas Windstorm', 'WS'),
    '15022534': ('Los Angeles EQ', 'EQ'),
    '15006191': ('San Francisco EQ', 'EQ'),
    '15355285': ('New Madrid RDS EQ', 'EQ'),
    '15631361': ('PNW/BC EQ 100yr', 'EQ'),
    '15631695': ('PNW/BC EQ 250yr', 'EQ'),
    '15623863': ('PNW/BC EQ 500yr', 'EQ'),
    '15669240': ('Quebec EQ 100yr', 'EQ'),
    '15675523': ('Quebec EQ 250yr', 'EQ'),
    '15666680': ('Quebec EQ 500yr', 'EQ'),
}

BUILTIN_PROFILES = {
    "_status": "LEGACY-UNVERIFIED model IDs, copied from RunAnalysis.bas. The analyses in current quotes use the named profiles in 'reference_profile' (read from the Meta tab). Confirm each ID in RMS before submitting from the app.",
    "currency": {
        "code": "USD",
        "scheme": "RMS",
        "asOfDate": "2024-04-03",
        "vintage": "RL24"
    },
    "global_analysis_settings": {
        "franchiseDeductible": False,
        "treatConstructionOccupancyAsUnknown": True
    },
    "output_profile_id": 11,
    "reference_output_profile": "AS_Amtrust_output",
    "perils": [
        {
            "code": "WS",
            "label": "Windstorm",
            "model_profile_id": 90,
            "event_rate_scheme_id": 578,
            "reference_profile": ""
        },
        {
            "code": "EQ",
            "label": "Earthquake",
            "model_profile_id": 89,
            "event_rate_scheme_id": 163,
            "reference_profile": "AS_EQ_PREBIND"
        },
        {
            "code": "CS",
            "label": "Severe convective storm",
            "model_profile_id": 91,
            "event_rate_scheme_id": 69,
            "reference_profile": "AS_TO_PREBIND"
        },
        {
            "code": "WT",
            "label": "Winter storm",
            "model_profile_id": 92,
            "event_rate_scheme_id": 27,
            "reference_profile": "AS_WTW_PREBIND"
        },
        {
            "code": "FL",
            "label": "Flood",
            "model_profile_id": 163,
            "event_rate_scheme_id": 49,
            "reference_profile": "FL_NumSamples_64"
        },
        {
            "code": "WF",
            "label": "Wildfire",
            "model_profile_id": 162,
            "event_rate_scheme_id": 0,
            "reference_profile": "WF_Samples1024"
        },
        {
            "code": "TR",
            "label": "Terrorism",
            "model_profile_id": 150,
            "event_rate_scheme_id": 0,
            "reference_profile": "TR_Sample980"
        }
    ],
    "grouping": {
        "num_simulations": 50000,
        "simulate_to_plt": True,
        "propagate_detailed_losses": False,
        "max_analysis_name": 64,
        "region_peril_simulation_set": [
            {
                "perilCode": "WS",
                "regionCode": "NA",
                "modelRegionCode": "NAWS",
                "eventRateSchemeId": 578,
                "simulationSetId": 131,
                "simulationPeriods": 100000,
                "engineVersion": "RL24"
            },
            {
                "perilCode": "CS",
                "regionCode": "NA",
                "modelRegionCode": "USCS",
                "eventRateSchemeId": 69,
                "simulationSetId": 84,
                "simulationPeriods": 100000,
                "engineVersion": "RL24"
            },
            {
                "perilCode": "EQ",
                "regionCode": "NA",
                "modelRegionCode": "NAEQ",
                "eventRateSchemeId": 163,
                "simulationSetId": 87,
                "simulationPeriods": 100000,
                "engineVersion": "RL24"
            },
            {
                "perilCode": "WT",
                "regionCode": "NA",
                "modelRegionCode": "NAWT",
                "eventRateSchemeId": 27,
                "simulationSetId": 58,
                "simulationPeriods": 100000,
                "engineVersion": "RL24"
            },
            {
                "perilCode": "FL",
                "regionCode": "US",
                "modelRegionCode": "USFL",
                "eventRateSchemeId": 0,
                "simulationSetId": 36,
                "simulationPeriods": 51200000,
                "engineVersion": "HDv1.2"
            },
            {
                "perilCode": "WF",
                "regionCode": "NA",
                "modelRegionCode": "NAWF",
                "eventRateSchemeId": 0,
                "simulationSetId": 53,
                "simulationPeriods": 102400000,
                "engineVersion": "HDv2.0"
            },
            {
                "perilCode": "TR",
                "regionCode": "ZZ",
                "modelRegionCode": "ZZTR",
                "eventRateSchemeId": 0,
                "simulationSetId": 46,
                "simulationPeriods": 2073776040,
                "engineVersion": "HDv1.0"
            }
        ]
    }
}

_PROFILE_KEYS = ("currency", "global_analysis_settings", "output_profile_id", "perils", "grouping")


def _read_json(name: str):
    """The parsed JSON file in cra/data, or None when it is missing or not valid JSON."""
    try:
        return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _peril_from_name(name: str) -> str:
    low = (name or "").lower()
    if "wind" in low or "hurricane" in low:
        return "WS"
    if "earthquake" in low or " eq" in f" {low}":
        return "EQ"
    return ""


def _event_table() -> dict:
    """{eventId: (name, peril)} - the built-in events plus whatever events.json adds, in either layout."""
    table = dict(BUILTIN_EVENTS)
    data = _read_json("events.json")
    rows = data.get("events", data) if isinstance(data, dict) else {}
    if isinstance(rows, dict):
        for k, v in rows.items():
            if isinstance(v, dict):
                name = str(v.get("name") or k)
                peril = str(v.get("peril") or "") or _peril_from_name(name)
            elif isinstance(v, str):
                name, peril = v, _peril_from_name(v)
            else:
                continue
            table[str(k)] = (name, peril or (table.get(str(k)) or ("", ""))[1])
    return table


def load_events() -> dict:
    """{eventId: name} for the RDS Events tab."""
    return {k: name for k, (name, _p) in _event_table().items()}


def load_event_perils() -> dict:
    """{eventId: peril code} - which peril model produces each RDS event."""
    return {k: peril for k, (_n, peril) in _event_table().items()}


def load_profiles() -> dict:
    """The analysis settings: cra/data/analysis_profiles.json when it is complete, otherwise the built-in defaults."""
    data = _read_json("analysis_profiles.json")
    if (isinstance(data, dict) and all(k in data for k in _PROFILE_KEYS) and isinstance(data["perils"], list)
            and data["perils"] and all(isinstance(p, dict) and "code" in p for p in data["perils"])):
        return data
    return copy.deepcopy(BUILTIN_PROFILES)
