"""Location rows, bands, map aggregation, account selection and reconciliation.

Port of GetALLExposureLocationsWithCorrectTIV_Combined (Get_Loc_Hazard_Refresh_ALL.bas): same 37 columns, same TIV rules
(total from the list call; Building / Contents / BI from the per-location detail call), same Wind Tier column.

Deliberate differences (each covered by a test):
  * Year Built blank / 0 / 9999 -> 'Unknown'  (the macro put 9999 into '2020-2030': 5 of 58 Ben Davis locations)
  * Stories blank / 0 -> 'Unknown'
"""
from __future__ import annotations

import pandas as pd

from ..constants import ALL_LOC_COLUMNS, WIND_COLUMN
from ..rms.core import gj, gobj, gv, safe_get, to_num
from .windtier import wind_tier


def strip_prefix(txt: str) -> str:
    """StripPrefix: 'ATC - Wholesale Trade' -> 'Wholesale Trade'."""
    parts = (txt or "").split(" - ")
    return parts[1] if len(parts) >= 2 else txt


def year_band(y) -> str:
    n = to_num(y)
    if n is None or n <= 0 or n >= 2100:
        return "Unknown"
    if n >= 2020:
        return "2020-2030"
    if n >= 2010:
        return "2010-2020"
    if n >= 2000:
        return "2000-2010"
    if n >= 1990:
        return "1990-2000"
    return "Pre-1990"


def stories_band(s) -> str:
    n = to_num(s)
    if n is None or n <= 0:
        return "Unknown"
    if n >= 50:
        return "50+"
    if n >= 21:
        return "21-50"
    if n >= 11:
        return "11-20"
    if n >= 7:
        return "7-10"
    return str(int(n))


def _cstr(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def location_id(lw: dict) -> str:
    return _cstr(safe_get(gobj(gobj(lw, "location"), "property"), "locationId"))


def detail_values(detail) -> tuple:
    """(building, contents, bi) from the per-location detail response."""
    bldg = cont = bi = 0.0
    if isinstance(detail, dict):
        bldg = to_num(detail.get("maxCvgBldgValuation")) or 0.0
        for cov in detail.get("locationCoverages") or []:
            lt = gj(cov, "lossType").lower()
            if lt == "contents":
                cont = to_num(gv(cov, "value")) or cont
            elif lt in ("bi", "business interruption"):
                bi = to_num(gv(cov, "value")) or bi
    return bldg, cont, bi


def build_location_row(lw: dict, detail, account_name: str, windtier_lookup: dict | None = None) -> dict:
    loc = gobj(lw, "location")
    prop, addr = gobj(loc, "property"), gobj(loc, "address")
    o = dict.fromkeys(ALL_LOC_COLUMNS)
    o["LocID"] = safe_get(prop, "locationNumber")
    o["Location Name"] = safe_get(prop, "locationName")
    o["Account Name"] = account_name
    o["Account Number"] = safe_get(prop, "accountId")
    tiv = safe_get(lw, "tiv")
    if tiv is None or tiv == "" or tiv == 0:
        t2 = safe_get(loc, "tiv")
        tiv = t2 if t2 is not None else tiv
    o["Location TIV"] = to_num(tiv) or 0.0
    o["Buildings TIV"], o["Contents TIV"], o["BI TIV"] = detail_values(detail)
    o["Construction Name"] = strip_prefix(_cstr(safe_get(prop, "buildingClass", "name")))
    o["Construction Scheme"] = safe_get(prop, "buildingClassScheme", "name")
    o["Construction Code"] = safe_get(prop, "buildingClass", "code")
    o["Occupancy Name"] = strip_prefix(_cstr(safe_get(prop, "occupancyType", "name")))
    o["Occupancy Scheme"] = safe_get(prop, "occupancyTypeScheme", "name")
    o["Occupancy Code"] = safe_get(prop, "occupancyType", "id")
    o["# of Stories"] = safe_get(prop, "stories")
    o["Year Built"] = safe_get(prop, "yearBuilt")
    o["User Text 1"] = safe_get(prop, "userText1")
    o["User Text 2"] = safe_get(prop, "userText2")
    o["User Defined 1"] = safe_get(prop, "userId1")
    o["User Defined 2"] = safe_get(prop, "userId2")
    o["Country"] = safe_get(addr, "country", "name")
    o["CRESTA"] = safe_get(addr, "area", "name")
    o["Admin1"] = safe_get(addr, "admin1Name")
    o["Admin2"] = safe_get(addr, "admin2Name")
    o["Admin3"] = safe_get(addr, "admin3Name")
    o["City"] = safe_get(addr, "cityName")
    o["Admin4"] = safe_get(addr, "admin4Name")
    o["Postal Code"] = safe_get(addr, "postalCode")
    o["Street Address"] = safe_get(addr, "streetAddress")
    o["Latitude"] = safe_get(addr, "latitude")
    o["Longitude"] = safe_get(addr, "longitude")
    o["Geocoding Resolution"] = safe_get(addr, "geoResolutionCode", "name")
    perils = prop.get("perils")
    o["Perils"] = ", ".join(str(p) for p in perils) if isinstance(perils, list) else ""
    o["Year Built Band"] = year_band(o["Year Built"])
    o["Stories Band"] = stories_band(o["# of Stories"])
    o["Lat,Long"] = f"{_cstr(o['Latitude'])},{_cstr(o['Longitude'])}"
    o[WIND_COLUMN] = wind_tier(_cstr(o["Admin1"]), _cstr(o["Admin2"]), windtier_lookup)
    return o


def rows_to_frame(rows: list) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=ALL_LOC_COLUMNS)
    for c in ("Location TIV", "Buildings TIV", "Contents TIV", "BI TIV"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    return df


def map_aggregate(df: pd.DataFrame) -> pd.DataFrame:
    """Map tab: per distinct Lat,Long: TIV sum and distinct LocID count (a LocID repeated at the same point counts once)."""
    out = {}
    for _, r in df.iterrows():
        mk = f"{_cstr(r['Latitude'])},{_cstr(r['Longitude'])}"
        if len(mk.strip()) <= 1:
            continue
        e = out.setdefault(mk, {"Latitude": r["Latitude"], "Longitude": r["Longitude"], "Lat,Long": mk,
                                "Location TIV Sum": 0.0, "LocID Count": 0, "_seen": set()})
        lk = _cstr(r["LocID"])
        if lk and lk not in e["_seen"]:
            e["_seen"].add(lk)
            e["LocID Count"] += 1
            e["Location TIV Sum"] += float(r["Location TIV"] or 0)
    rows = [{k: v for k, v in e.items() if k != "_seen"} for e in out.values()]
    return pd.DataFrame(rows, columns=["Latitude", "Longitude", "Lat,Long", "Location TIV Sum", "LocID Count"])


# ---------------------------------------------------------------- summary tables (the Exposure Summary pivots)
def _summ(df: pd.DataFrame, col: str, label: str | None = None, order: list | None = None) -> pd.DataFrame:
    g = df.groupby(col, dropna=False).agg(**{"Loc Count": ("LocID", "count"), "Sum of Location TIV": ("Location TIV", "sum")})
    g = g.reset_index().rename(columns={col: label or col})
    g[label or col] = g[label or col].map(lambda v: "(blank)" if v is None or (isinstance(v, float) and pd.isna(v)) or v == "" else v)
    if order:
        key = {v: i for i, v in enumerate(order)}
        g = g.sort_values(by=label or col, key=lambda s: s.map(lambda v: key.get(v, len(key))), kind="stable")
    else:
        g = g.sort_values(by=label or col, kind="stable")
    return g.reset_index(drop=True)


YEAR_ORDER = ["Pre-1990", "1990-2000", "2000-2010", "2010-2020", "2020-2030", "Unknown"]
STORIES_ORDER = ["1", "2", "3", "4", "5", "6", "7-10", "11-20", "21-50", "50+", "Unknown"]
WIND_ORDER = ["T1", "T2", "Rest of State", "N/A"]


def exposure_summaries(df: pd.DataFrame) -> dict:
    """The eight Exposure Summary pivots, in the order they sit on the sheet."""
    state = (df.groupby(["Admin1", "Admin2"], dropna=False)
               .agg(**{"Loc Count": ("LocID", "count"), "Sum of Location TIV": ("Location TIV", "sum")}).reset_index())
    state["Admin1"] = state["Admin1"].fillna("(blank)")
    state["Admin2"] = state["Admin2"].fillna("(blank)")
    return {
        "Account": _summ(df, "Account Name", "Account Name"),
        "State / County": state,
        "Construction": _summ(df, "Construction Name"),
        "Occupancy": _summ(df, "Occupancy Name"),
        "Geocoding Resolution": _summ(df, "Geocoding Resolution"),
        "Year Built Band": _summ(df, "Year Built Band", order=YEAR_ORDER),
        "Stories Band": _summ(df, "Stories Band", order=STORIES_ORDER),
        "Wind Tier": _summ(df, WIND_COLUMN, order=WIND_ORDER),
    }


# ---------------------------------------------------------------- account selection (two macros in the workbook)
def account_summary(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("Account Number", dropna=False).agg(Locations=("LocID", "count"), TIV=("Location TIV", "sum")).reset_index()
    return g.sort_values("Account Number", key=lambda s: pd.to_numeric(s, errors="coerce")).reset_index(drop=True)


def filter_accounts(df: pd.DataFrame, mode: str = "all", accounts: list | None = None) -> pd.DataFrame:
    """'all' = Run_ALL_Exposure_Hazards_And_Refresh; 'latest' = highest account number only; 'pick' = listed accounts."""
    if df is None or not len(df) or mode == "all":
        return df
    nums = pd.to_numeric(df["Account Number"], errors="coerce")
    if mode == "latest":
        return df[nums == nums.max()].reset_index(drop=True)
    want = {str(a) for a in (accounts or [])}
    return df[df["Account Number"].astype(str).isin(want)].reset_index(drop=True)


def repeated_locids(df: pd.DataFrame) -> dict:
    """LocIDs that appear under more than one account at the same point - the usual cause of TIV Modelled > Reported."""
    if df is None or not len(df):
        return {"locids": 0, "tiv": 0.0}
    key = df["LocID"].astype(str) + "|" + df["Lat,Long"].astype(str)
    accts = df.groupby(key)["Account Number"].nunique()
    dup = accts[accts > 1].index
    return {"locids": int(len(dup)), "tiv": float(df[key.isin(dup)]["Location TIV"].sum())}


def share_point_stats(df: pd.DataFrame) -> dict:
    """Locations that share a Lat,Long with at least one other location (campus / multi-building sites)."""
    if df is None or not len(df):
        return {"locations": 0, "tiv": 0.0, "groups": 0}
    ll = df["Lat,Long"].astype(str)
    cnt = ll.map(ll.value_counts())
    sub = df[cnt > 1]
    return {"locations": int(len(sub)), "tiv": float(sub["Location TIV"].sum()), "groups": int(ll[cnt > 1].nunique())}


# ---------------------------------------------------------------- reconciliation
def reconcile(df: pd.DataFrame, tiv_reported=None, expected_count: int | None = None) -> dict:
    """Checks the workbook only implied. Returns tiv_modelled / tiv_reported / delta / flags [(level, text)]."""
    flags = []
    n = len(df)
    tiv = float(df["Location TIV"].sum()) if n else 0.0
    if expected_count is not None and expected_count != n:
        flags.append(("error", f"Downloaded {n:,} locations but RMS reported {expected_count:,}."))
    if n:
        comp = (df["Buildings TIV"] + df["Contents TIV"] + df["BI TIV"]) - df["Location TIV"]
        bad = int((comp.abs() > 1.0).sum())
        if bad:
            flags.append(("warn", f"{bad:,} location(s) where Building + Contents + BI differs from Location TIV by more than 1."))
        zero = int((df["Location TIV"] <= 0).sum())
        if zero:
            flags.append(("warn", f"{zero:,} location(s) with zero TIV."))
        nolatlon = int((df["Latitude"].map(lambda v: to_num(v) in (None, 0.0))
                        | df["Longitude"].map(lambda v: to_num(v) in (None, 0.0))).sum())
        if nolatlon:
            flags.append(("warn", f"{nolatlon:,} location(s) with no usable latitude / longitude."))
        nostate = int(df["Admin1"].map(lambda v: v in (None, "", 0, "0")).sum())
        if nostate:
            flags.append(("warn", f"{nostate:,} location(s) with no state (Admin1) - hazards and wind tier cannot be set."))
        noyear = int((df["Year Built Band"] == "Unknown").sum())
        if noyear:
            flags.append(("info", f"{noyear:,} location(s) with unknown year built (blank, 0 or 9999)."))
        na_wind = int((df[WIND_COLUMN] == "N/A").sum())
        if na_wind:
            flags.append(("info", f"{na_wind:,} location(s) with wind tier N/A (state / county not in the lookup)."))
        nonus = int((~df["Country"].astype(str).str.upper().isin(["UNITED STATES", "US"])).sum())
        if nonus:
            flags.append(("info", f"{nonus:,} location(s) outside the US (no hazard enrichment)."))
        haz = df["mmi100yr"].map(lambda v: v not in (None, "", "Error")) if "mmi100yr" in df else None
        if haz is not None and haz.any():
            sub = df[haz]
            no_mmi = sub["mmi100yr"].map(lambda v: to_num(v) in (None, 0.0)).all()
            if no_mmi:
                flags.append(("info", "The 100-year MMI layer returned no value for any enriched location, so earthquake shaking is not shown."))
    n_acc = df["Account Number"].nunique() if n else 0
    rep_dup = repeated_locids(df)
    if n_acc > 1:
        flags.append(("info", f"{n_acc} accounts are included in this total."))
    if rep_dup["locids"]:
        flags.append(("warn", f"{rep_dup['locids']:,} location(s) appear under more than one account at the same point "
                              f"(TIV {rep_dup['tiv']:,.0f} counted more than once). Check the account selection."))
    delta = None
    rep = to_num(tiv_reported)
    if rep:
        delta = tiv / rep - 1
    return {"tiv_modelled": tiv, "tiv_reported": rep, "delta": delta, "locations": n, "flags": flags}
