"""Wind tier lookup (Get_Loc_Hazard_Refresh_ALL.bas CleanWindKey / StripAdmin2Suffix / GetWindTier); data/wind_tier.csv."""
from __future__ import annotations

import csv
from functools import lru_cache

from ..constants import DATA_DIR

SUFFIXES = ("COUNTY", "PARISH", "BOROUGH", "CITY", "MUNICIPALITY", "CENSUSAREA")


def clean_key(s: str) -> str:
    return "".join(ch for ch in (s or "").strip().upper() if ("A" <= ch <= "Z") or ("0" <= ch <= "9"))


def strip_admin2_suffix(admin2: str) -> str:
    s = clean_key(admin2)
    for suf in SUFFIXES:
        if len(s) > len(suf) and s.endswith(suf):
            return s[:-len(suf)]
    return s


def build_lookup(rows) -> dict:
    d = {}
    for a1, a2rms, a2, look, tier in rows:
        tier = (tier or "").strip() or "N/A"
        for k in (clean_key(a1) + clean_key(a2rms), clean_key(a1) + clean_key(a2), clean_key(look)):
            if k and k not in d:
                d[k] = tier
    return d


@lru_cache(maxsize=1)
def default_lookup() -> dict:
    with open(DATA_DIR / "wind_tier.csv", newline="", encoding="utf-8") as f:
        rd = csv.reader(f)
        next(rd, None)
        return build_lookup((r + [""] * 5)[:5] for r in rd)


def wind_tier(admin1, admin2, lookup: dict | None = None) -> str:
    lookup = default_lookup() if lookup is None else lookup
    if not lookup:
        return "N/A"
    a1 = clean_key(str(admin1 or ""))
    k = a1 + clean_key(str(admin2 or ""))
    if k in lookup:
        return lookup[k]
    return lookup.get(a1 + strip_admin2_suffix(str(admin2 or "")), "N/A")
