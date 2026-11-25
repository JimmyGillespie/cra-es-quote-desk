"""Runtime settings, read from Streamlit secrets / environment variables (Posit Connect: Vars tab).

Variable names follow the EQ-RMS app so the same Posit settings work:
    RISKDATA_API_KEY   Risk Data + Risk Modeler API key   (fallback: RMS_TOKEN, the AmCat name)
    LI_API_KEY         Location Intelligence key           (fallback: LI_TOKEN)
    GROUPING_API_KEY   Grouping API key                    (fallback: RISKDATA_API_KEY, flagged in the UI)
    GROUPING_RESOURCE_GROUP_ID, RMS_HOST, CRA_STORE_DIR, CRA_SHARE_DIR, CRA_MAILBOX, CRA_QUOTES_ROOT
No key is ever written to disk, logs or exported files.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .constants import DEFAULT_MAILBOX, DEFAULT_QUOTES_ROOT, DEFAULT_RMS_HOST
from .rms.core import Creds, clean_key

Getter = Callable[[str], str]


def env_getter(secrets: Optional[dict] = None) -> Getter:
    """Secrets first, then the environment (name matched ignoring case)."""
    def get(name: str) -> str:
        if secrets:
            v = secrets.get(name)
            if v not in (None, ""):
                return str(v)
        v = os.environ.get(name)
        if v not in (None, ""):
            return v
        for k, val in os.environ.items():
            if k.upper() == name.upper() and val:
                return val
        return ""
    return get


@dataclass
class Settings:
    host: str = DEFAULT_RMS_HOST
    riskdata_key: str = ""
    li_key: str = ""
    grouping_key: str = ""
    grouping_key_source: str = ""          # "GROUPING_API_KEY" | "RISKDATA_API_KEY (fallback)" | ""
    grouping_resource_group_id: str = ""
    verify_ssl: bool = True
    store_dir: str = ""
    share_dir: str = ""
    mailbox: str = DEFAULT_MAILBOX
    quotes_root: str = DEFAULT_QUOTES_ROOT
    notes: list = field(default_factory=list)

    @classmethod
    def load(cls, get: Optional[Getter] = None) -> "Settings":
        get = get or env_getter()
        s = cls()
        s.host = (get("RMS_HOST") or DEFAULT_RMS_HOST).strip()
        s.riskdata_key = clean_key(get("RISKDATA_API_KEY") or get("RMS_TOKEN"))
        s.li_key = clean_key(get("LI_API_KEY") or get("LI_TOKEN"))
        gk = clean_key(get("GROUPING_API_KEY"))
        if gk:
            s.grouping_key, s.grouping_key_source = gk, "GROUPING_API_KEY"
        elif s.riskdata_key:
            s.grouping_key, s.grouping_key_source = s.riskdata_key, "RISKDATA_API_KEY (fallback)"
            s.notes.append("GROUPING_API_KEY is not set, so the Risk Data key will be offered to the Grouping API.")
        s.grouping_resource_group_id = (get("GROUPING_RESOURCE_GROUP_ID") or get("RMS_RESOURCE_GROUP_ID")).strip()
        s.verify_ssl = (get("CRA_VERIFY_SSL") or "1").strip().lower() not in ("0", "false", "no")
        s.store_dir = get("CRA_STORE_DIR").strip()
        s.share_dir = get("CRA_SHARE_DIR").strip()
        s.mailbox = (get("CRA_MAILBOX") or DEFAULT_MAILBOX).strip()
        s.quotes_root = (get("CRA_QUOTES_ROOT") or DEFAULT_QUOTES_ROOT).strip()
        return s

    def creds(self) -> Creds:
        return Creds(host=self.host, riskdata_key=self.riskdata_key, riskmodeler_key=self.riskdata_key,
                     grouping_key=self.grouping_key, hazard_key=self.li_key,
                     grouping_resource_group_id=self.grouping_resource_group_id, verify=self.verify_ssl)

    def missing(self) -> list:
        out = []
        if not self.riskdata_key:
            out.append("RISKDATA_API_KEY")
        if not self.li_key:
            out.append("LI_API_KEY (needed only for hazard enrichment)")
        return out


def default_store_roots(settings: Settings) -> list:
    """Primary (shared mount) first, then a local fallback. Both are written; reads take the newest good copy."""
    roots = []
    if settings.share_dir:
        roots.append(Path(settings.share_dir))
    else:
        roots.append(Path("/mnt/from-posit/content/anv-cra-es"))
    roots.append(Path(settings.store_dir) if settings.store_dir else Path.home() / ".cra_es_store")
    return roots
