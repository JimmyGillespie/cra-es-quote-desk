"""Account store - the single source of truth for a quote. Nothing important lives only in st.session_state.

Layout (one folder per account, kept for the life of the quote; re-running updates it, it never forks a new account):

    <root>/accounts/<year>/<account_id>/
        manifest.json          parts{} with sha256, size and timestamp - written LAST
        submission.json  portfolios.json  locations.json.gz  hazards.json.gz  analyses.json  jobs.json
        rds.json  results_meta.json  email.json
        reports/               generated workbooks / CSVs
        runs/<stamp>/          immutable snapshot of each report run

Reliability rules (the reason results used to open "sometimes"):
  * every write is atomic (temp file -> fsync -> rename) and then read back and hash-checked;
  * every write goes to ALL writable roots (shared mount + local fallback);
  * a read picks the newest copy whose hash matches the manifest, and repairs the other root;
  * a part that fails its check is reported as 'damaged', never returned as if it were fine.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .rules.naming import slug


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    os.replace(tmp, path)


def _writable(root: Path) -> bool:
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / f".probe{os.getpid()}"
        probe.write_bytes(b"x")
        probe.unlink()
        return True
    except OSError:
        return False


@dataclass
class SaveReport:
    ok: bool
    saved: list = field(default_factory=list)        # roots written and verified
    failed: list = field(default_factory=list)       # (root, error)

    def text(self) -> str:
        if self.ok and not self.failed:
            return f"saved to {len(self.saved)} location(s)"
        if self.ok:
            return f"saved to {len(self.saved)} location(s); {len(self.failed)} unavailable"
        return "NOT SAVED: " + "; ".join(f"{r}: {e}" for r, e in self.failed)


class AccountStore:
    def __init__(self, roots: list):
        self.all_roots = [Path(r) for r in roots]
        self.roots = [r for r in self.all_roots if _writable(r)]
        self.unavailable = [r for r in self.all_roots if r not in self.roots]

    # ------------------------------------------------------------ paths
    @staticmethod
    def new_account_id(exposure_id: str, exposure_name: str, year: int | None = None) -> str:
        return f"{year or datetime.now().year}-{exposure_id}-{slug(exposure_name, 40)}"

    @staticmethod
    def _year_of(account_id: str) -> str:
        return account_id.split("-", 1)[0] if account_id[:4].isdigit() else str(datetime.now().year)

    def _dir(self, root: Path, account_id: str) -> Path:
        return root / "accounts" / self._year_of(account_id) / account_id

    @contextmanager
    def _lock(self, root: Path, account_id: str, timeout: float = 20.0):
        lock = self._dir(root, account_id) / ".lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        while True:
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                break
            except FileExistsError:
                try:
                    if time.time() - lock.stat().st_mtime > 30:       # stale
                        lock.unlink()
                        continue
                except OSError:
                    pass
                if time.time() - t0 > timeout:
                    raise TimeoutError(f"account {account_id} is locked by another save")
                time.sleep(0.1)
        try:
            yield
        finally:
            try:
                lock.unlink()
            except OSError:
                pass

    # ------------------------------------------------------------ manifest
    def _read_manifest(self, root: Path, account_id: str) -> dict | None:
        p = self._dir(root, account_id) / "manifest.json"
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def manifest(self, account_id: str) -> dict | None:
        best = None
        for r in self.roots:
            m = self._read_manifest(r, account_id)
            if m and (best is None or m.get("updated", "") > best.get("updated", "")):
                best = m
        return best

    # ------------------------------------------------------------ parts
    @staticmethod
    def _filename(part: str, compress: bool) -> str:
        return f"{part}.json.gz" if compress else f"{part}.json"

    def save_part(self, account_id: str, part: str, obj, *, compress: bool = False,
                  header: dict | None = None) -> SaveReport:
        """Atomically store a JSON-able part in every writable root and update the manifest."""
        raw = json.dumps(obj, ensure_ascii=False, default=_json_default, separators=(",", ":")).encode("utf-8")
        data = gzip.compress(raw, 5) if compress else raw
        fname = self._filename(part, compress)
        sha, stamp = _sha(data), now_iso()
        rep = SaveReport(ok=False)
        for root in self.roots:
            try:
                with self._lock(root, account_id):
                    d = self._dir(root, account_id)
                    _atomic_write(d / fname, data)
                    back = (d / fname).read_bytes()                     # verify by reading back
                    if _sha(back) != sha:
                        raise OSError("read-back hash mismatch")
                    m = self._read_manifest(root, account_id) or {"account_id": account_id, "created": stamp, "parts": {}}
                    m.setdefault("parts", {})[part] = {"file": fname, "sha256": sha, "bytes": len(data), "updated": stamp}
                    m["updated"] = stamp
                    if header:
                        m.update({k: v for k, v in header.items() if v is not None})
                    _atomic_write(d / "manifest.json", json.dumps(m, indent=1).encode("utf-8"))   # LAST
                rep.saved.append(str(root))
            except (OSError, TimeoutError) as exc:
                rep.failed.append((str(root), str(exc)))
        rep.ok = bool(rep.saved)
        return rep

    def load_part(self, account_id: str, part: str, default=None):
        """-> object. Picks the newest copy whose hash matches its manifest; repairs the other roots."""
        cands = []
        for root in self.roots:
            m = self._read_manifest(root, account_id)
            ent = (m or {}).get("parts", {}).get(part)
            if not ent:
                continue
            p = self._dir(root, account_id) / ent["file"]
            try:
                data = p.read_bytes()
            except OSError:
                continue
            if _sha(data) != ent["sha256"]:
                continue                                                # damaged: never trust it
            cands.append((ent["updated"], root, ent, data))
        if not cands:
            return default
        cands.sort(key=lambda c: c[0], reverse=True)
        _u, _root, ent, data = cands[0]
        for root in self.roots:                                         # repair stale / missing copies
            if not any(c[1] == root and c[0] == cands[0][0] for c in cands):
                try:
                    d = self._dir(root, account_id)
                    _atomic_write(d / ent["file"], data)
                    m = self._read_manifest(root, account_id) or {"account_id": account_id, "created": ent["updated"], "parts": {}}
                    m.setdefault("parts", {})[part] = ent
                    m["updated"] = max(m.get("updated", ""), ent["updated"])
                    _atomic_write(d / "manifest.json", json.dumps(m, indent=1).encode("utf-8"))
                except OSError:
                    pass
        raw = gzip.decompress(data) if ent["file"].endswith(".gz") else data
        return json.loads(raw.decode("utf-8"))

    def check_account(self, account_id: str) -> dict:
        """{'ok': [...], 'damaged': [...], 'missing': [...]} per part, across roots."""
        rep = {"ok": [], "damaged": [], "missing": []}
        parts = set()
        for r in self.roots:
            parts |= set(((self._read_manifest(r, account_id) or {}).get("parts") or {}).keys())
        for part in sorted(parts):
            good = False
            for r in self.roots:
                ent = ((self._read_manifest(r, account_id) or {}).get("parts") or {}).get(part)
                if ent:
                    try:
                        if _sha((self._dir(r, account_id) / ent["file"]).read_bytes()) == ent["sha256"]:
                            good = True
                    except OSError:
                        pass
            (rep["ok"] if good else rep["damaged"]).append(part)
        return rep

    # ------------------------------------------------------------ files (reports) and run snapshots
    def save_file(self, account_id: str, relpath: str, data: bytes) -> SaveReport:
        rep = SaveReport(ok=False)
        sha = _sha(data)
        for root in self.roots:
            try:
                p = self._dir(root, account_id) / relpath
                _atomic_write(p, data)
                if _sha(p.read_bytes()) != sha:
                    raise OSError("read-back hash mismatch")
                rep.saved.append(str(root))
            except OSError as exc:
                rep.failed.append((str(root), str(exc)))
        rep.ok = bool(rep.saved)
        return rep

    def read_file(self, account_id: str, relpath: str) -> bytes | None:
        for root in self.roots:
            p = self._dir(root, account_id) / relpath
            if p.exists():
                try:
                    return p.read_bytes()
                except OSError:
                    continue
        return None

    def list_files(self, account_id: str, sub: str = "reports") -> list:
        seen = {}
        for root in self.roots:
            d = self._dir(root, account_id) / sub
            if d.exists():
                for p in d.rglob("*"):
                    if p.is_file():
                        rel = str(p.relative_to(self._dir(root, account_id)))
                        seen[rel] = max(seen.get(rel, 0), p.stat().st_mtime)
        return sorted(seen.items(), key=lambda kv: kv[1], reverse=True)

    # ------------------------------------------------------------ library
    def list_accounts(self) -> list:
        found = {}
        for root in self.roots:
            base = root / "accounts"
            if not base.exists():
                continue
            for mf in base.glob("*/*/manifest.json"):
                try:
                    m = json.loads(mf.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                aid = m.get("account_id") or mf.parent.name
                if aid not in found or m.get("updated", "") > found[aid].get("updated", ""):
                    found[aid] = m
        return sorted(found.values(), key=lambda m: m.get("updated", ""), reverse=True)

    def delete_account(self, account_id: str) -> None:
        for root in self.roots:
            shutil.rmtree(self._dir(root, account_id), ignore_errors=True)

    def describe(self) -> dict:
        return {"writable": [str(r) for r in self.roots], "unavailable": [str(r) for r in self.unavailable]}


def _json_default(o):
    try:
        import numpy as np
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return None if np.isnan(o) else float(o)
        if isinstance(o, np.bool_):
            return bool(o)
    except ImportError:
        pass
    if hasattr(o, "isoformat"):
        return o.isoformat()
    if isinstance(o, (set, tuple)):
        return list(o)
    if isinstance(o, float):
        return None
    raise TypeError(f"not JSON serialisable: {type(o)}")
