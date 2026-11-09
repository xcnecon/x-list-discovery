"""Normalize vendor/CSV aliases and retain stable identities."""
from __future__ import annotations

from pathlib import Path
import re

from .storage import read_csv, read_json


PROFILE_COLUMNS = ["id", "userName", "name", "description", "followers_count", "following_count",
                   "statuses_count", "protected", "isAutomated"]


def first(row, names, default=None):
    return next((row[k] for k in names if row.get(k) not in (None, "")), default)


def boolean(value, default=None):
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    if str(value).lower() in {"true", "1", "yes"}:
        return True
    if str(value).lower() in {"false", "0", "no"}:
        return False
    raise ValueError("Invalid boolean value: " + str(value))


def handle(value):
    value = str(value or "").strip().lstrip("@")
    # Vendor snapshots can contain legacy handles beyond X's usual 15 characters.
    # Keep those edges; path/control characters remain invalid.
    if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", value):
        raise ValueError("Invalid X username: " + value)
    return value


def profile(row):
    name = handle(first(row, ("userName", "username", "screen_name")))
    result = {"id": str(first(row, ("id", "user_id"), "")), "userName": name,
              "name": row.get("name") or "", "description": row.get("description") or ""}
    for key, aliases in {
        "followers_count": ("followers_count", "followers", "followersCount"),
        "following_count": ("following_count", "following", "friends_count"),
        "statuses_count": ("statuses_count", "statusesCount", "tweet_count"),
    }.items():
        value = first(row, aliases)
        result[key] = int(value) if value is not None else None
    for key in ("protected", "isAutomated"):
        result[key] = boolean(row.get(key))
    return result


def identity(row):
    return "id:" + str(row["id"]) if row.get("id") else "name:" + row["userName"].lower()


def profiles(path):
    path = Path(path)
    rows = read_json(path) if path.suffix.lower() == ".json" else read_csv(path)
    if not isinstance(rows, list):
        raise ValueError("Account input must be a CSV or JSON list")
    result, seen, names = [], set(), {}
    for raw in rows:
        row = profile(raw)
        key, name = identity(row), row["userName"].lower()
        if name in names and names[name] != key:
            raise ValueError("Username maps to conflicting IDs: " + name)
        names[name] = key
        if key not in seen:
            seen.add(key)
            result.append(row)
    return result
