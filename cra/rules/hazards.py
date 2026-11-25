"""Location Intelligence hazard parsing (AddHazardsToLocations_Combined in Get_Loc_Hazard_Refresh_ALL.bas).

Four LI layers per US location: us_fema_firm (floodZone, annualProbability), us_distance_to_inner_coast (DTC),
na_eq_hazard (soil / liquefaction / landslide) and us_mmi (mmi100yr + description).
Value rules are the macro's: a missing field -> 'N/A', an unusable reply -> 'Error'.
"""
from __future__ import annotations

from ..constants import HAZARD_COLUMNS, MMI_DESC, STATE_NAME_TO_CODE
from ..rms.core import to_num

LI_LAYERS = ("us_fema_firm", "us_distance_to_inner_coast", "na_eq_hazard", "us_mmi")


def proper_case(text: str) -> str:
    return " ".join(w[:1].upper() + w[1:] for w in (text or "").lower().split())


def admin1_code(admin1_text: str) -> str:
    return STATE_NAME_TO_CODE.get(proper_case((admin1_text or "").strip()), "")


def is_us(country) -> bool:
    return str(country or "").strip().upper() in ("US", "UNITED STATES")


def eligible(row: dict) -> tuple:
    """(ok, code). The macro skipped non-US rows, rows with no recognised state, and rows with no lat/long."""
    if not is_us(row.get("Country")):
        return False, ""
    code = admin1_code(str(row.get("Admin1") or ""))
    lat, lon = row.get("Latitude"), row.get("Longitude")
    if not code or lat is None or lon is None or str(lat).strip() == "" or str(lon).strip() == "":
        return False, code
    return True, code


def hazard_key(lat, lon, code: str) -> str:
    """Cache key: identical points are charged once."""
    try:
        return f"{float(lat):.6f},{float(lon):.6f},{code}"
    except (TypeError, ValueError):
        return f"{lat},{lon},{code}"


def address_body(lat, lon, code: str) -> dict:
    return {"location": {"address": {"countryScheme": "ISO2A", "countryCode": "US", "latitude": lat, "longitude": lon,
                                     "admin1Code": code, "countryRmsCode": "US", "rmsGeoModelResolutionCode": 1}}}


def dtc_body(lat, lon, code: str) -> dict:
    b = address_body(lat, lon, code)
    b["layerOptions"] = {"distanceUnit": "miles"}
    return b


def parse_flood(j) -> dict:
    if not isinstance(j, dict):
        return {"floodZone": "Error", "annualProbability": "Error"}
    return {"floodZone": j["floodZone"] if "floodZone" in j else "N/A",
            "annualProbability": j["annualProbability"] if "annualProbability" in j else "N/A"}


def parse_dtc(j):
    val = "N/A"
    if isinstance(j, dict):
        if "distanceToInnerCoast" in j:
            val = j["distanceToInnerCoast"]
        elif "distance" in j:
            val = j["distance"]
        elif isinstance(j.get("results"), list) and j["results"] and isinstance(j["results"][0], dict):
            first = j["results"][0]
            if "distanceToInnerCoast" in first:
                val = first["distanceToInnerCoast"]
            elif "distance" in first:
                val = first["distance"]
    n = to_num(val)
    return n if n is not None else (val if val not in (None, "") else "N/A")


def parse_eq(j) -> dict:
    keys = ("soilDescription", "liquefactionDescription", "landslideDescription")
    if not isinstance(j, dict):
        return {k: "Error" for k in keys}
    return {k: (j[k] if k in j else "N/A") for k in keys}


def parse_mmi(j) -> dict:
    if not isinstance(j, dict):
        return {"mmi100yr": "Error", "MMI_Description": "Error"}
    if "mmi100yr" not in j:
        return {"mmi100yr": "N/A", "MMI_Description": "N/A"}
    v = j["mmi100yr"]
    n = to_num(v)
    desc = MMI_DESC.get(int(round(n)), "Unknown") if n is not None else "Unknown"
    return {"mmi100yr": v, "MMI_Description": desc}


def empty_hazards() -> dict:
    return dict.fromkeys(HAZARD_COLUMNS)
