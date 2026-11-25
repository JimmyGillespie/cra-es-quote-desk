"""Low-level RMS helpers: credentials, HTTP and tolerant JSON accessors.

Ported from rms_results.py in the EQ-RMS app (streamlit_app_v18), which is confirmed working against the live API:
the bare-key Authorization header, retry on 429/5xx, and the case-insensitive getters that the VBA JsonConverter code
relied on.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import NamedTuple
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter


# ---------------------------------------------------------------- keys / credentials
def clean_key(k: str) -> str:
    """Tidy a pasted key: invisible characters, quotes, a leading 'Authorization:' / 'Bearer ', inner whitespace."""
    k = re.sub("[\u200b-\u200f\u2060\ufeff\u00a0]", "", k or "")
    k = k.strip().strip("\"'`").strip()
    k = re.sub(r"^authorization\s*:\s*", "", k, flags=re.I)
    k = re.sub(r"^bearer\s+", "", k, flags=re.I)
    return re.sub(r"\s+", "", k)


def key_fingerprint(k: str) -> str:
    """Describe a key without revealing it."""
    k = clean_key(k)
    return f"{len(k)} characters, starts '{k[:3]}', ends '{k[-3:]}'" if len(k) >= 8 else f"{len(k)} characters"


@dataclass
class Creds:
    host: str
    riskdata_key: str
    riskmodeler_key: str = ""
    grouping_key: str = ""
    hazard_key: str = ""
    grouping_resource_group_id: str = ""
    verify: bool = True
    grouping_auth: str = ""          # the Authorization value the Grouping API accepted (set on first success)

    def __post_init__(self):
        self.host = (self.host or "").strip()
        self.riskdata_key = clean_key(self.riskdata_key)
        self.riskmodeler_key = clean_key(self.riskmodeler_key) or self.riskdata_key
        self.grouping_key = clean_key(self.grouping_key)
        self.hazard_key = clean_key(self.hazard_key)
        self.grouping_resource_group_id = (self.grouping_resource_group_id or "").strip()


class Resp(NamedTuple):
    status: int
    text: str
    headers: dict


class Http:
    """Thin requests wrapper; one instance per run; safe for simple concurrent GET/POST."""

    def __init__(self, verify: bool = True):
        self.verify = verify
        self.s = requests.Session()
        ad = HTTPAdapter(pool_connections=16, pool_maxsize=16)
        self.s.mount("https://", ad)
        self.s.mount("http://", ad)
        if not verify:
            try:
                import urllib3
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            except Exception:                                   # noqa: BLE001
                pass

    def _do(self, method, url, key, body=None, headers=None, retries=3, timeout=(30, 120)) -> Resp:
        h = {"Accept": "application/json", "Authorization": key}
        if body is not None:
            h["Content-Type"] = "application/json"
        if headers:
            h.update(headers)
        last = ""
        for attempt in range(retries):
            try:
                r = self.s.request(method, url, headers=h, timeout=timeout, verify=self.verify,
                                   data=(body.encode("utf-8") if isinstance(body, str) else body))
                if r.status_code in (429, 502, 503, 504) and attempt < retries - 1:
                    time.sleep(min(2 ** (attempt + 1), 10))
                    continue
                return Resp(r.status_code, r.text, dict(r.headers))
            except requests.RequestException as exc:
                last = str(exc)
                if attempt < retries - 1:
                    time.sleep(min(2 ** (attempt + 1), 10))
        return Resp(0, f"{method} error: {last}", {})

    def get(self, url, key, headers=None) -> Resp:
        return self._do("GET", url, key, headers=headers)

    def post(self, url, key, body, headers=None) -> Resp:
        return self._do("POST", url, key, body=body, headers=headers)


# ---------------------------------------------------------------- tolerant JSON accessors
def _s(v) -> str:
    if v is None or isinstance(v, (dict, list)):
        return ""
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def gj(obj, key: str) -> str:
    """String value of key (case-insensitive); '' when absent."""
    if not isinstance(obj, dict):
        return ""
    if key in obj:
        return _s(obj[key])
    kl = key.lower()
    for k, v in obj.items():
        if str(k).lower() == kl:
            return _s(v)
    return ""


def gv(obj, key: str):
    if not isinstance(obj, dict):
        return None
    if key in obj:
        return obj[key]
    kl = key.lower()
    for k, v in obj.items():
        if str(k).lower() == kl:
            return v
    return None


def gobj(obj, key: str) -> dict:
    v = gv(obj, key)
    return v if isinstance(v, dict) else {}


def to_num(v):
    if v is None or v == "" or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip())
    except ValueError:
        return None


def json_or_none(text: str):
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def as_list(parsed) -> list:
    """Array, or {items|analyses|data|results|exposures|portfolios: [...]}."""
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        for k in ("items", "analyses", "data", "results", "exposures", "portfolios", "searchItems"):
            if isinstance(parsed.get(k), list):
                return parsed[k]
    return []


def url_encode(s: str) -> str:
    return quote(s, safe="-._~")


def safe_get(obj, key: str, child: str | None = None):
    """LH_SafeGet, bug-for-bug: '' -> 0, missing -> None (blank), missing parent -> 0."""
    if not isinstance(obj, dict):
        return 0

    def leaf(d, k):
        if k not in d:
            return None
        v = d[k]
        if v is None:
            return None
        if isinstance(v, str):
            return 0 if v == "" else v
        return v

    if child is None:
        return leaf(obj, key)
    tmp = obj.get(key)
    if not isinstance(tmp, dict):
        return 0
    return leaf(tmp, child)
