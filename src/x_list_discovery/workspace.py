"""One explicit source population and one cache namespace per list."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import socket

from .models import PROFILE_COLUMNS, identity, profiles
from .profiles import classify
from .storage import digest, file_hash, now, read_json, write_csv, write_json


def process_alive(pid):
    if pid < 1:
        raise ValueError("Invalid workspace lock PID")
    if os.name == "nt":
        # os.kill(pid, 0) can terminate a Windows process. Query its state instead.
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() != 87  # Invalid PID; access denied remains locked.
        try:
            code = wintypes.DWORD()
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


@dataclass
class Config:
    name: str
    version: int = 1
    owner: str = ""
    mode: str = "both"
    min_common: int = 2
    gem_max_followers: int = 50000
    gem_smoothing: int = 1000
    individuals_only: bool = False
    exclude_bd: bool = False
    exclude_members: bool = True

    def validate(self):
        if self.version != 1 or self.mode not in {"cofollow", "gems", "both"}:
            raise ValueError("Unsupported workspace version or ranking mode")
        if self.min_common < 1 or self.gem_smoothing < 0 or self.gem_max_followers < 1:
            raise ValueError("Ranking thresholds must be positive (smoothing may be zero)")
        for key in ("individuals_only", "exclude_bd", "exclude_members"):
            if not isinstance(getattr(self, key), bool):
                raise ValueError(key + " must be a JSON boolean")
        if not self.exclude_members:
            raise ValueError("Original list members must always be excluded")


class Workspace:
    def __init__(self, root, name):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name):
            raise ValueError("List name must be a simple slug, not a path")
        self.path = Path(root).resolve() / name.lower()
        self.config_path = self.path / "config.json"

    @property
    def config(self):
        raw = read_json(self.config_path)
        if raw is None:
            raise ValueError("Workspace not found; run init first: " + str(self.path))
        config = Config(**raw)
        config.validate()
        return config

    def file(self, relative):
        return self.path / relative

    def seeds(self):
        return profiles(self.file("input/seeds.csv"))

    def members(self):
        return profiles(self.file("input/members.csv"))

    def excluded(self):
        return profiles(self.file("input/excluded.json"))

    def cache_path(self, seed):
        key = identity(seed).replace(":", "-")
        return self.file("state/followings/" + key + ".json")

    def aggregate_signature(self):
        return digest({"members": file_hash(self.file("input/members.csv")),
                       "seeds": file_hash(self.file("input/seeds.csv")),
                       "excluded": file_hash(self.file("input/excluded.json")),
                       "caches": [(identity(s), file_hash(self.cache_path(s))) for s in self.seeds()]})

    def rank_signature(self, mode, settings):
        return digest({"aggregate": file_hash(self.file("data/cofollow.csv")), "config": file_hash(self.config_path),
                       "aggregate_signature": self.aggregate_signature(), "settings": settings,
                       "reviews": file_hash(self.file("input/reviews.json")), "mode": mode})

    def require_aggregate(self):
        fetch = read_json(self.file("state/fetch.json"), {})
        if fetch and (not fetch.get("finished") or fetch.get("failed")):
            raise ValueError("Source fetch is interrupted or failed; resume fetch before using the aggregate")
        meta = read_json(self.file("data/cofollow.meta.json"), {})
        if (meta.get("signature") != self.aggregate_signature()
                or not meta.get("csv_hash") or meta["csv_hash"] != file_hash(self.file("data/cofollow.csv"))):
            raise ValueError("Aggregate is missing or stale; run aggregate for this list")
        return meta

    @contextmanager
    def lock(self):
        lock = self.file("state/workspace.lock")
        lock.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            previous = read_json(lock, {})
            if previous.get("host") != socket.gethostname():
                raise ValueError("Workspace is locked by another host")
            if not process_alive(int(previous.get("pid", 0))):
                lock.unlink()
                with self.lock():
                    yield
                return
            raise ValueError("Another command is using this workspace")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump({"pid": os.getpid(), "host": socket.gethostname()}, f)
            yield
        finally:
            lock.unlink(missing_ok=True)


def initialize(workspace, members, seeds=None, excluded=None, reviews=None, **settings):
    if workspace.path.exists():
        raise ValueError("Workspace already exists; choose a new name or edit its config")
    member_rows = profiles(members)
    review_data = reviews if isinstance(reviews, dict) else read_json(reviews) if reviews else {"individuals": {}, "organizations": {}, "bd_accounts": {}, "uncertain": {}}
    classifications = {r["userName"].lower(): classify(r, review_data) for r in member_rows}
    personal_seeds_only = settings.pop("personal_seeds_only", False)
    seed_rows = profiles(seeds) if seeds else [r for r in member_rows if not personal_seeds_only or classifications[r["userName"].lower()][0] == "individual"]
    if not member_rows or not seed_rows:
        raise ValueError("No voting sources found. For --personal-seeds, provide profile reviews or an explicit --seeds CSV")
    member_ids = {identity(r) for r in member_rows}
    member_names = {r["userName"].lower(): r for r in member_rows}
    for seed in seed_rows:
        if identity(seed) in member_ids:
            continue
        member = member_names.get(seed["userName"].lower())
        if member is None or seed.get("id") and member.get("id") and seed["id"] != member["id"]:
            raise ValueError("Voting seeds must come from the supplied member list")
    config = Config(name=workspace.path.name, **settings)
    config.validate()
    excluded_rows = profiles(excluded) if excluded else []
    workspace.file("input").mkdir(parents=True)
    shutil.copy2(members, workspace.file("input/source" + Path(members).suffix.lower()))
    write_csv(workspace.file("input/members.csv"), member_rows, PROFILE_COLUMNS)
    write_csv(workspace.file("input/seeds.csv"), seed_rows, PROFILE_COLUMNS)
    seed_ids = {identity(r) for r in seed_rows}
    write_csv(workspace.file("input/seed_review.csv"), [
        {**r, "account_type": classifications[r["userName"].lower()][0],
         "reason": classifications[r["userName"].lower()][1], "voting_source": identity(r) in seed_ids}
        for r in member_rows], PROFILE_COLUMNS + ["account_type", "reason", "voting_source"])
    write_json(workspace.file("input/excluded.json"), excluded_rows)
    write_json(workspace.file("input/reviews.json"), review_data)
    write_json(workspace.config_path, vars(config))
    write_json(workspace.file("state/origin.json"), {"created_at": now(), "source": str(Path(members).resolve()),
               "source_hash": file_hash(members), "member_count": len(member_rows), "seed_count": len(seed_rows)})
    return {"workspace": str(workspace.path), "members": len(member_rows), "seeds": len(seed_rows)}
