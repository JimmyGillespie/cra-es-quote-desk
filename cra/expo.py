"""Exposure analysis: everything the Exposure dashboard shows, as plain pandas. No Streamlit and no RMS here.

Takes the location table the app builds (one row per location: Location TIV, Buildings / Contents / BI TIV, construction,
occupancy, year built band, stories band, geocoding resolution, wind tier, account, address, latitude / longitude and the
optional hazard and floor-area columns). Every function tolerates missing columns, zeros and blanks, so a thin or messy schedule
never breaks the page.

The Overview is a data-completeness dashboard: how much of the value (TIV) has a known construction, occupancy, year built, number
of stories and a street-level geocode, and how the schedule breaks down by each.
"""
from __future__ import annotations

import math
import re

import numpy as np
import pandas as pd

UNKNOWN = "Unknown"
YEAR_ORDER = ["Pre-1990", "1990-2000", "2000-2010", "2010-2020", "2020-2030", UNKNOWN]
STORIES_ORDER = ["1", "2", "3", "4", "5", "6", "7-10", "11-20", "21-50", "50+", UNKNOWN]
WIND_ORDER = ["T1", "T2", "Rest of State", "N/A"]
DTC_ORDER = ["Under 1 mile", "1 to 5 miles", "5 to 10 miles", "Over 10 miles", "N/A"]
FLOOD_ORDER = ["High risk (A / V zones)", "Moderate (shaded X)", "Minimal (X)", "Undetermined (D)", "Not enriched", "Error"]
FLOOR_ORDER = ["< 1,500", "1,500-2,500", "2,500-5,000", "5,000-10,000", "> 10,000", UNKNOWN]
_BLANKS = {"", "0", "0.0", "nan", "none", "n/a", "unknown", "null"}


# ---------------------------------------------------------------- small helpers
def num(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(0.0, index=df.index)
    return pd.to_numeric(df[col], errors="coerce").fillna(0.0)


def text(df: pd.DataFrame, col: str, blank: str = UNKNOWN) -> pd.Series:
    """A text column where None / 0 / '' / 'N/A' all read as `blank`."""
    if col not in df.columns:
        return pd.Series(blank, index=df.index)
    return df[col].map(lambda v: blank if v is None or (isinstance(v, float) and v != v) or str(v).strip().lower() in _BLANKS else str(v).strip())


def title(s) -> str:
    return " ".join(w[:1].upper() + w[1:].lower() for w in str(s).split())


def money(v, dec: int = 1) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return "-"
    a = abs(v)
    if a >= 1e9:
        return f"${v / 1e9:,.{dec}f}B"
    if a >= 1e6:
        return f"${v / 1e6:,.{dec}f}M"
    if a >= 1e3:
        return f"${v / 1e3:,.0f}K"
    return f"${v:,.0f}"


def pct(v, dec: int = 1) -> str:
    return "-" if v is None or v != v else f"{v * 100:.{dec}f}%"


def coords(df: pd.DataFrame) -> pd.DataFrame:
    """Rows with a usable latitude / longitude, as floats (lat, lon)."""
    lat, lon = num(df, "Latitude"), num(df, "Longitude")
    ok = (lat.abs() > 0.0001) & (lon.abs() > 0.0001) & (lat.abs() <= 90) & (lon.abs() <= 180)
    return df.assign(lat=lat, lon=lon)[ok]


# ---------------------------------------------------------------- headline
def headline(df: pd.DataFrame, reported=None) -> dict:
    tiv, n = float(num(df, "Location TIV").sum()), len(df)
    vals = num(df, "Location TIV")
    big = df.loc[vals.idxmax()] if n and tiv > 0 else None
    top10 = float(vals.sort_values(ascending=False).head(10).sum() / tiv) if tiv > 0 else None
    acc = int(df["Account Number"].astype(str).nunique()) if "Account Number" in df and n else 0
    st_ = text(df, "Admin1")
    states = int(st_[st_ != UNKNOWN].nunique()) if n else 0
    return {"tiv": tiv, "locations": n, "accounts": acc, "avg_tiv": (tiv / n) if n else None, "states": states,
            "largest_tiv": float(vals.max()) if n else None, "largest_share": (float(vals.max()) / tiv) if tiv > 0 else None,
            "largest_name": (f"{big.get('Street Address', '')}, {big.get('City', '')}".strip(", ") if big is not None else ""),
            "top10_share": top10, "reported": reported,
            "delta": (tiv / reported - 1) if reported else None}


def group_tiv(df: pd.DataFrame, col: str, order: list | None = None, top: int | None = None, other: str = "Other") -> pd.DataFrame:
    """TIV by a category: columns [label, Locations, TIV, Share, Average]. `top` keeps the largest N and folds the rest into `other`."""
    key = text(df, col)
    d = pd.DataFrame({"label": key, "tiv": num(df, "Location TIV")})
    g = d.groupby("label").agg(Locations=("tiv", "size"), TIV=("tiv", "sum")).reset_index()
    tot = g["TIV"].sum()
    if order:
        rank = {v: i for i, v in enumerate(order)}
        g = g.sort_values("label", key=lambda s: s.map(lambda v: rank.get(v, len(rank))), kind="stable")
    else:
        g = g.sort_values("TIV", ascending=False, kind="stable")
        if top and len(g) > top:
            head, rest = g.head(top), g.iloc[top:]
            g = pd.concat([head, pd.DataFrame([{"label": other, "Locations": int(rest["Locations"].sum()), "TIV": float(rest["TIV"].sum())}])])
    g["Share"] = g["TIV"] / tot if tot else 0.0
    g["Average"] = g["TIV"] / g["Locations"].replace(0, np.nan)
    return g.reset_index(drop=True)


def state_county(df: pd.DataFrame) -> pd.DataFrame:
    d = pd.DataFrame({"State": text(df, "Admin1").map(title), "County": text(df, "Admin2").map(title), "tiv": num(df, "Location TIV")})
    g = d.groupby(["State", "County"]).agg(Locations=("tiv", "size"), TIV=("tiv", "sum")).reset_index().sort_values("TIV", ascending=False)
    g["Share"] = g["TIV"] / g["TIV"].sum() if g["TIV"].sum() else 0.0
    return g.reset_index(drop=True)


def top_locations(df: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    tiv = float(num(df, "Location TIV").sum()) or 1.0
    d = df.assign(_t=num(df, "Location TIV")).sort_values("_t", ascending=False).head(n)
    return pd.DataFrame({"Location": (d["LocID"].values if "LocID" in d else np.arange(1, len(d) + 1)), "Name": text(d, "Location Name", "").values,
                         "Address": (text(d, "Street Address", "") + ", " + text(d, "City", "")).str.strip(", ").values,
                         "Occupancy": text(d, "Occupancy Name").values, "Construction": text(d, "Construction Name").values,
                         "TIV": d["_t"].values, "Share": (d["_t"] / tiv).values})


def accumulations(df: pd.DataFrame, cell: float = 0.05, top: int = 10) -> pd.DataFrame:
    """The biggest piles of value in one small area (a grid of `cell` degrees; 0.05 is about 5 km). One bad event can hit all of it."""
    d = coords(df)
    if d.empty:
        return pd.DataFrame(columns=["Area", "Locations", "TIV", "Share", "lat", "lon"])
    d = d.assign(_t=num(d, "Location TIV"), _gy=np.floor(d["lat"] / cell), _gx=np.floor(d["lon"] / cell))
    total = float(num(df, "Location TIV").sum()) or 1.0
    rows = []
    for (_gy, _gx), g in d.groupby(["_gy", "_gx"]):
        c_, s_ = text(g, "City", ""), text(g, "Admin1", "")
        city, state = c_[c_ != ""].mode(), s_[s_ != ""].mode()
        where = ", ".join(x for x in (title(city.iloc[0]) if len(city) else "", title(state.iloc[0]) if len(state) else "") if x) or f"{g['lat'].mean():.2f}, {g['lon'].mean():.2f}"
        rows.append({"Area": where, "Locations": len(g), "TIV": float(g["_t"].sum()), "Share": float(g["_t"].sum()) / total,
                     "lat": float(g["lat"].mean()), "lon": float(g["lon"].mean())})
    out = pd.DataFrame(rows).sort_values("TIV", ascending=False).head(top).reset_index(drop=True)
    n = out.groupby("Area").cumcount()
    out["Area"] = [a if k == 0 else f"{a} ({k + 1})" for a, k in zip(out["Area"], n)]          # two piles in one town must not share a label
    return out


# ---------------------------------------------------------------- floor area (only when the schedule carries it)
def floor_area_col(df: pd.DataFrame):
    """The floor-area column, whatever it is called ('Floor Area', 'FLOORAREA', 'floorArea'), or None when the schedule has none."""
    for c in df.columns:
        if re.sub(r"[^a-z]", "", str(c).lower()) == "floorarea":
            return c
    return None


def floor_area_band(v) -> str:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return UNKNOWN
    if n != n or n <= 0:
        return UNKNOWN
    return "< 1,500" if n < 1500 else "1,500-2,500" if n < 2500 else "2,500-5,000" if n < 5000 else "5,000-10,000" if n < 10000 else "> 10,000"


def floor_area_groups(df: pd.DataFrame):
    """TIV by floor-area band, or None when the schedule has no floor-area column (or none of it is filled in)."""
    col = floor_area_col(df)
    if col is None:
        return None
    d = df.assign(_fa=df[col].map(floor_area_band))
    if (d["_fa"] == UNKNOWN).all():
        return None
    return group_tiv(d, "_fa", FLOOR_ORDER)


# ---------------------------------------------------------------- matrices
def matrix(df: pd.DataFrame, rows: str, cols: str, row_order=None, col_order=None, top_rows: int | None = None, top_cols: int | None = None) -> pd.DataFrame:
    """TIV by two categories, long form: [row, col, TIV, Locations] - ready for a heatmap."""
    r, c = text(df, rows), text(df, cols)
    d = pd.DataFrame({"row": r, "col": c, "TIV": num(df, "Location TIV")})
    for name, k in (("row", top_rows), ("col", top_cols)):
        if k:
            keep = d.groupby(name)["TIV"].sum().sort_values(ascending=False).head(k).index
            d[name] = d[name].where(d[name].isin(keep), "Other")
    g = d.groupby(["row", "col"]).agg(TIV=("TIV", "sum"), Locations=("TIV", "size")).reset_index()
    g.attrs["row_order"] = row_order or list(d.groupby("row")["TIV"].sum().sort_values(ascending=False).index)
    g.attrs["col_order"] = col_order or list(d.groupby("col")["TIV"].sum().sort_values(ascending=False).index)
    return g


# ---------------------------------------------------------------- hazards (only when they were added)
def flood_class(z) -> str:
    s = str(z or "").strip().upper()
    if not s or s in ("N/A", "NONE", "NAN"):
        return "Not enriched"
    if s == "ERROR":
        return "Error"
    if s in {"A", "AE", "AH", "AO", "AR", "A99", "V", "VE"}:
        return "High risk (A / V zones)"
    if s in ("SHX", "X500", "B") or s.startswith("0.2"):
        return "Moderate (shaded X)"
    if s in ("X", "C"):
        return "Minimal (X)"
    if s == "D":
        return "Undetermined (D)"
    return f"Other ({s})"


def dtc_band(v) -> str:
    try:
        n = float(v)
        return "Under 1 mile" if n < 1 else "1 to 5 miles" if n < 5 else "5 to 10 miles" if n < 10 else "Over 10 miles"
    except (TypeError, ValueError):
        s = str(v or "").strip()
        if s.startswith(">") or "10" in s:
            return "Over 10 miles"
        return "N/A"


def hazards_added(df: pd.DataFrame) -> bool:
    return "floodZone" in df.columns and bool(df["floodZone"].map(lambda v: v not in (None, "", "N/A", "Error") and v == v).any())


def hazard_groups(df: pd.DataFrame) -> dict:
    """{title: grouped TIV table} for the hazard layers that have data. Empty when hazards were not added."""
    if not hazards_added(df):
        return {}
    tmp = df.assign(**{"_flood": df["floodZone"].map(flood_class), "_dtc": df["DTC"].map(dtc_band) if "DTC" in df else "N/A",
                       "_soil": text(df, "soilDescription", "Not enriched"), "_mmi": text(df, "MMI_Description", "N/A")})
    out = {"Flood zone": group_tiv(tmp, "_flood", FLOOD_ORDER), "Distance to the coast": group_tiv(tmp, "_dtc", DTC_ORDER),
           "Soil (earthquake)": group_tiv(tmp, "_soil")}
    if (tmp["_mmi"] != "N/A").any():
        out["Shaking (100-year MMI)"] = group_tiv(tmp, "_mmi")
    return out


# ---------------------------------------------------------------- data quality
# 'Street name / census tract', zip, city and so on are coarser than a street address: they are NOT high resolution.
_COARSE = ("census", "street name", "zip", "postal", "city", "county", "admin", "country", "un_geocoded", "ungeocoded", "not geocoded", "unknown")
_FINE = ("parcel", "street", "building", "address", "rooftop", "point", "coordinate")


def _fine(v) -> bool:
    s = str(v).strip().lower()
    if not s or s in _BLANKS or any(w in s for w in _COARSE):
        return False
    return any(w in s for w in _FINE)


CHECKS = [("coords", "Has latitude and longitude"), ("geo", "Geocoded to street level or better"), ("year", "Year built is known"),
          ("stories", "Number of stories is known"), ("cons", "Construction is known"), ("occ", "Occupancy is known"),
          ("bldg", "Building value is above zero"), ("hazard", "Hazards have been added")]


def _good_mask(df: pd.DataFrame, key: str) -> pd.Series:
    if key == "coords":
        return df.index.isin(coords(df).index)
    if key == "geo":
        return text(df, "Geocoding Resolution").map(_fine)
    if key == "year":
        return text(df, "Year Built Band") != UNKNOWN
    if key == "stories":
        return text(df, "Stories Band") != UNKNOWN
    if key == "cons":
        return text(df, "Construction Name") != UNKNOWN
    if key == "occ":
        return text(df, "Occupancy Name") != UNKNOWN
    if key == "bldg":
        return num(df, "Buildings TIV") > 0
    if key == "hazard":
        return (df["floodZone"].map(lambda v: v not in (None, "", "N/A", "Error") and v == v) if "floodZone" in df.columns
                else pd.Series(False, index=df.index))
    raise KeyError(key)


def quality_scores(df: pd.DataFrame) -> list:
    """One row per check: how much of the schedule (by count and by value) passes. level is green (95%+ of value), amber (80%+) or red."""
    n, tot = len(df), float(num(df, "Location TIV").sum())
    out = []
    for key, label in CHECKS:
        if key == "hazard" and "floodZone" not in df.columns:
            continue
        good = pd.Series(_good_mask(df, key), index=df.index)
        v = num(df, "Location TIV")
        g_tiv = float(v[good].sum())
        pt = (g_tiv / tot) if tot > 0 else (good.mean() if n else 1.0)
        out.append({"key": key, "check": label, "good": int(good.sum()), "bad": int((~good).sum()), "pct_locs": float(good.mean()) if n else 1.0,
                    "pct_tiv": float(pt), "bad_tiv": float(tot - g_tiv), "level": "green" if pt >= 0.95 else "amber" if pt >= 0.80 else "red"})
    return out


def issue_rows(df: pd.DataFrame, key: str) -> pd.DataFrame:
    """The locations that fail a check, biggest value first."""
    bad = df[~pd.Series(_good_mask(df, key), index=df.index)].copy()
    for c in ("Construction Name", "Occupancy Name", "Year Built Band", "Stories Band", "Geocoding Resolution"):
        if c in bad.columns:
            bad[c] = text(bad, c)                                          # 0 / blank read as 'Unknown', as everywhere else
    cols = [c for c in ("LocID", "Location Name", "Street Address", "City", "Admin1", "Geocoding Resolution", "Year Built Band", "Stories Band",
                        "Construction Name", "Occupancy Name", "Location TIV", "Buildings TIV") if c in bad.columns]
    return bad.assign(_t=num(bad, "Location TIV")).sort_values("_t", ascending=False)[cols]


# ---------------------------------------------------------------- windstorm / coast
def wind_by_resolution(df: pd.DataFrame) -> pd.DataFrame:
    """Wind tier by geocoding resolution: [Wind tier, Geocoding, Locations, TIV]."""
    d = pd.DataFrame({"Wind tier": text(df, "Wind Tier", "N/A"), "Geocoding": text(df, "Geocoding Resolution"), "tiv": num(df, "Location TIV")})
    g = d.groupby(["Wind tier", "Geocoding"]).agg(Locations=("tiv", "size"), TIV=("tiv", "sum")).reset_index()
    rank = {v: i for i, v in enumerate(WIND_ORDER)}
    return g.sort_values(["Wind tier", "TIV"], key=lambda s: s.map(lambda v: rank.get(v, 99)) if s.name == "Wind tier" else -s, kind="stable").reset_index(drop=True)


# ---------------------------------------------------------------- the map
PALETTE = [(11, 37, 69), (47, 109, 181), (200, 67, 75), (42, 157, 143), (181, 101, 29), (109, 89, 122), (138, 155, 181), (224, 161, 0)]
MAP_MODES = ["Value (TIV)", "Wind tier", "Construction", "Occupancy", "Geocoding resolution", "Flood zone"]
_MODE_COL = {"Wind tier": "Wind Tier", "Construction": "Construction Name", "Occupancy": "Occupancy Name", "Geocoding resolution": "Geocoding Resolution"}


def _ramp(t: float) -> tuple:
    """Pale blue -> deep red for t in 0..1."""
    t = min(max(t, 0.0), 1.0)
    a, b, c = np.array([125, 180, 228]), np.array([237, 190, 70]), np.array([192, 57, 43])
    rgb = a + (b - a) * (t / 0.5) if t < 0.5 else b + (c - b) * ((t - 0.5) / 0.5)
    return tuple(int(x) for x in rgb)


def map_points(df: pd.DataFrame, mode: str = "Value (TIV)") -> tuple:
    """-> (points DataFrame, legend [(label, (r, g, b))]). One point per distinct coordinate; size is the value there; colour follows `mode`."""
    d = coords(df)
    if d.empty:
        return pd.DataFrame(columns=["lat", "lon", "tiv", "locs", "label", "category", "r", "g", "b", "radius", "tiv_txt"]), []
    cat_col = "_flood" if mode == "Flood zone" else _MODE_COL.get(mode)
    d = d.assign(_t=num(d, "Location TIV"), _k=d["lat"].round(6).astype(str) + "," + d["lon"].round(6).astype(str))
    if mode == "Flood zone":
        d["_flood"] = d["floodZone"].map(flood_class) if "floodZone" in d else "Not enriched"
    d["_cat"] = text(d, cat_col) if cat_col else ""
    d["_addr"] = (text(d, "Street Address", "") + ", " + text(d, "City", "")).str.strip(", ")
    g = d.groupby("_k").agg(lat=("lat", "mean"), lon=("lon", "mean"), tiv=("_t", "sum"), locs=("_t", "size"), label=("_addr", "first"),
                            category=("_cat", lambda s: s.mode().iloc[0] if len(s.mode()) else "")).reset_index(drop=True)
    legend = []
    if mode == "Value (TIV)":
        lo, hi = float(g["tiv"].min()), float(g["tiv"].max())
        t = ((np.log10(g["tiv"].clip(lower=1)) - math.log10(max(lo, 1))) / ((math.log10(max(hi, 2)) - math.log10(max(lo, 1))) or 1.0)).clip(0, 1)
        rgb = [_ramp(x) for x in t]
        legend = [("Smaller", _ramp(0.0)), ("Mid", _ramp(0.5)), ("Largest", _ramp(1.0))]
    else:
        order = list(g.groupby("category")["tiv"].sum().sort_values(ascending=False).index)
        col = {c: PALETTE[i % len(PALETTE)] for i, c in enumerate(order)}
        rgb = [col[c] for c in g["category"]]
        legend = [(c, col[c]) for c in order[:8]]
    g["r"], g["g"], g["b"] = [x[0] for x in rgb], [x[1] for x in rgb], [x[2] for x in rgb]
    g["radius"] = 600 + 5200 * np.sqrt(g["tiv"] / max(float(g["tiv"].max()), 1.0))
    g["tiv_txt"] = g["tiv"].map(money)
    return g, legend


# ---------------------------------------------------------------- one workbook with every table
def summary_workbook(df: pd.DataFrame, reported=None) -> bytes:
    """The whole dashboard as tables in one .xlsx (for the underwriter or the file)."""
    import io
    h = headline(df, reported)
    sheets = {
        "Headline": pd.DataFrame({"Measure": ["TIV modelled", "TIV reported", "Locations", "Accounts", "Average TIV per location"],
                                  "Value": [h["tiv"], h["reported"], h["locations"], h["accounts"], h["avg_tiv"]]}),
        "Data quality": pd.DataFrame(quality_scores(df)).drop(columns=["key"], errors="ignore"),
        "Geocoding": group_tiv(df, "Geocoding Resolution"), "Construction": group_tiv(df, "Construction Name"),
        "Occupancy": group_tiv(df, "Occupancy Name"), "Year built": group_tiv(df, "Year Built Band", YEAR_ORDER), "Stories": group_tiv(df, "Stories Band", STORIES_ORDER),
        "State and county": state_county(df), "Wind tier": wind_by_resolution(df), "Top locations": top_locations(df, 25),
        "Accumulations 5km": accumulations(df, 0.05, 25).drop(columns=["lat", "lon"], errors="ignore"),
    }
    fa = floor_area_groups(df)
    if fa is not None:
        sheets["Floor area"] = fa
    for name, t in hazard_groups(df).items():
        sheets[name[:31]] = t
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for name, t in sheets.items():
            t.to_excel(xw, sheet_name=name[:31], index=False)
            ws = xw.sheets[name[:31]]
            for i, c in enumerate(t.columns, 1):
                ws.column_dimensions[ws.cell(1, i).column_letter].width = min(max(12, len(str(c)) + 4, int(t[c].astype(str).str.len().max() if len(t) else 0) + 2), 48)
            ws.freeze_panes = "A2"
    return buf.getvalue()
