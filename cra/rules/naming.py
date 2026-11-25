"""File and folder naming (SaveFile.bas GetFolderNameFromExposure / CleanFileName, ReplyFoundEmail.bas)."""
from __future__ import annotations

import re
from datetime import datetime


def clean_file_name(s: str, maxlen: int = 120) -> str:
    s = re.sub(r'[\\/:*?"<>|]', "", s or "").strip()
    return s[:maxlen]


def folder_name_from_exposure(exposure_name: str) -> str:
    """EDM_Childress_Klein_Properties_Y26 -> 'Childress Klein Properties'."""
    t = (exposure_name or "").strip()
    if t.upper().startswith("CRA_E&S_"):
        t = t[8:]
    if t.upper().startswith("EDM_"):
        t = t[4:]
    parts = t.split("_")
    if parts and len(parts[-1]) >= 2 and parts[-1][0].upper() == "Y" and parts[-1][1:].isdigit():
        parts = parts[:-1]
    return clean_file_name(" ".join(p for p in parts if p))


def report_file_name(exposure_name: str, stamp: str | None = None, ext: str = "xlsx") -> str:
    base = clean_file_name(exposure_name) or "Account"
    if stamp:
        return f"CRA_E&S_{base}_{stamp}.{ext}"
    return f"CRA_E&S_{base}.{ext}"


def slug(s: str, maxlen: int = 60) -> str:
    return (re.sub(r"[^A-Za-z0-9]+", "_", s or "").strip("_") or "account")[:maxlen]


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")
