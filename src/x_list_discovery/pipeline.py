"""Fetch, import, aggregate and rank without hidden global list state."""
from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import math
from pathlib import Path
import shutil

from .models import identity, profile
from .profiles import BD, classify
from .storage import digest, file_hash, now, read_csv, read_json, write_csv, write_json


AGGREGATE_COLUMNS = ["id", "userName", "name", "followed_by_seeds", "followers_count", "following_count",
    "already_followed", "is_seed", "is_member", "followed_by_list", "description", "isAutomated"]
RANK_COLUMNS = ["rank", "id", "userName", "name", "common_follows", "followed_by_seeds", "followers_count",
    "following_count", "followed_by_list", "description", "account_type", "account_type_reason", "bd_profile_flag", "selected", "selection_reason"]


def _entry(seed, users, origin, captured=None):
    users = [profile(u) for u in users]
    status = "unavailable" if not users and seed.get("following_count") not in (None, 0) else "complete"
    if not users and seed.get("following_count") is None:
        status = "unavailable"
    return {"source_identity": identity(seed), "userName": seed["userName"], "status": status,
            "reason": "empty_with_nonzero_or_unknown_profile" if status == "unavailable" else "",
            "users": users, "fetched_at": captured or now(), "origin": origin,
            "time_basis": "import_file_mtime" if captured else "api_fetch_completed"}


def cache(workspace, seed):
    entry = read_json(workspace.cache_path(seed))
    if entry is not None and (entry.get("source_identity") != identity(seed)
                              or entry.get("status") not in {"complete", "unavailable"}
                              or not isinstance(entry.get("users"), list)):
        raise ValueError("Cache ownership/schema mismatch for " + seed["userName"])
    return entry


def fetch(workspace, client, workers=4, refresh=False, progress=None):
    if workers < 1:
        raise ValueError("workers must be positive")
    seeds = workspace.seeds()
    previous = read_json(workspace.file("state/fetch.json"), {})
    failed_before = set(previous.get("failed", {}))
    source_signature = digest(seeds)
    continuing = not previous.get("finished", True) and previous.get("source_signature") == source_signature
    refresh = refresh or continuing and previous.get("refresh", False)
    cutoff = previous["started_at"] if continuing else now()
    state = {"started_at": cutoff, "source_signature": source_signature, "refresh": bool(refresh),
             "requested": len(seeds), "completed": 0, "cached": 0, "failed": {}, "finished": False}
    write_json(workspace.file("state/fetch.json"), state)

    def worker(seed):
        old = cache(workspace, seed)
        if old and (not refresh or old["fetched_at"] >= cutoff) and identity(seed) not in failed_before:
            return "cached"
        users = client.followings(seed["userName"])
        effective = seed
        if not users and seed.get("following_count") != 0:
            users = client.followings(seed["userName"])
            if not users:
                effective = {**seed, **client.user(seed["userName"])}
        entry = _entry(seed, users, "twitterapi.io")
        if not users:
            entry["status"] = "complete" if effective.get("following_count") == 0 and not effective.get("protected") else "unavailable"
            entry["reason"] = "protected" if effective.get("protected") else "empty_unverified" if entry["status"] == "unavailable" else ""
        if old:
            history = workspace.file("state/history/" + state["started_at"].replace(":", "-") + "/" + workspace.cache_path(seed).name)
            history.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(workspace.cache_path(seed), history)
        write_json(workspace.cache_path(seed), entry)
        return entry["status"]

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(worker, seed): seed for seed in seeds}
        for future in as_completed(futures):
            seed = futures[future]
            try:
                outcome = future.result()
                state["cached"] += outcome == "cached"
            except Exception as error:
                state["failed"][identity(seed)] = {"userName": seed["userName"], "error": str(error)}
            state["completed"] += 1
            write_json(workspace.file("state/fetch.json"), state)
            if progress:
                progress(seed["userName"], state["completed"], len(seeds))
    state["finished"] = True
    state["finished_at"] = now()
    write_json(workspace.file("state/fetch.json"), state)
    return state


def import_graph(workspace, path):
    raw = read_json(path)
    if not isinstance(raw, dict):
        raise ValueError("Legacy graph must map source usernames to profile lists")
    graph = {k.lower(): v for k, v in raw.items()}
    if len(graph) != len(raw) or any(not isinstance(v, list) for v in graph.values()):
        raise ValueError("Graph source names must be unique and every value must be a profile list")
    seeds = workspace.seeds()
    wanted = {s["userName"].lower() for s in seeds}
    if set(graph) != wanted:
        raise ValueError("Source population mismatch: missing={}, extra={}".format(sorted(wanted-set(graph)), sorted(set(graph)-wanted)))
    captured = datetime.fromtimestamp(Path(path).stat().st_mtime, timezone.utc).isoformat()
    for seed in seeds:
        entry = _entry(seed, graph[seed["userName"].lower()], str(Path(path).resolve()), captured)
        write_json(workspace.cache_path(seed), entry)
    state = {"finished": True, "requested": len(seeds), "completed": len(seeds), "failed": {},
             "imported_at": now(), "origin": str(Path(path).resolve()), "source_hash": file_hash(path)}
    write_json(workspace.file("state/fetch.json"), state)
    return state


def load_graph(workspace):
    seeds = workspace.seeds()
    state = read_json(workspace.file("state/fetch.json"), {})
    if state and not state.get("finished"):
        raise ValueError("Fetch was interrupted; resume fetch before aggregating")
    failures = state.get("failed", {})
    graph, coverage = {}, []
    missing = []
    for seed in seeds:
        entry = cache(workspace, seed)
        if entry is None or identity(seed) in failures:
            missing.append(seed["userName"])
            continue
        graph[seed["userName"]] = entry["users"]
        coverage.append({"userName": seed["userName"], "status": entry["status"], "reason": entry.get("reason", ""),
                         "following_count": len({identity(u) for u in entry["users"]}), "fetched_at": entry["fetched_at"],
                         "time_basis": entry.get("time_basis", "import_file_mtime" if entry.get("origin") != "twitterapi.io" else "api_fetch_completed")})
    if missing:
        raise ValueError("Incomplete source coverage; fetch missing/failed sources: " + ", ".join(missing))
    return graph, coverage


def aggregate(workspace):
    graph, coverage = load_graph(workspace)
    seeds, members, excluded = workspace.seeds(), workspace.members(), workspace.excluded()
    def known(users):
        return {str(u["id"]) for u in users if u.get("id")}, {u["userName"].lower() for u in users}
    seed_ids, seed_names = known(seeds)
    member_ids, member_names = known(members)
    excluded_ids, excluded_names = known(excluded)
    by_target, profiles = defaultdict(set), {}
    handle_ids = defaultdict(set)
    for users in graph.values():
        for u in users:
            if u.get("id"):
                handle_ids[u["userName"].lower()].add(str(u["id"]))
    for source, users in graph.items():
        for u in users:
            key = identity(u)
            known_ids = handle_ids[u["userName"].lower()]
            if not u.get("id") and len(known_ids) == 1:
                key = "id:" + next(iter(known_ids))
            by_target[key].add(source)
            if key not in profiles or not profiles[key].get("id") and u.get("id"):
                profiles[key] = u
    rows = []
    for key, sources in by_target.items():
        u = profiles[key]
        uid, name = str(u.get("id") or ""), u["userName"].lower()
        rows.append({"id": uid, "userName": u["userName"], "name": u.get("name", ""),
            "followed_by_seeds": len(sources), "followers_count": u.get("followers_count"),
            "following_count": u.get("following_count"), "description": u.get("description", ""),
            "followed_by_list": "|".join(sorted(sources, key=str.lower)),
            "already_followed": name in excluded_names or bool(uid and uid in excluded_ids),
            "is_seed": name in seed_names or bool(uid and uid in seed_ids),
            "is_member": name in member_names or bool(uid and uid in member_ids), "isAutomated": u.get("isAutomated", False)})
    rows.sort(key=lambda r: (-r["followed_by_seeds"], r["userName"].lower()))
    write_csv(workspace.file("data/cofollow.csv"), rows, AGGREGATE_COLUMNS)
    meta = {"created_at": now(), "signature": workspace.aggregate_signature(), "rows": len(rows),
            "csv_hash": file_hash(workspace.file("data/cofollow.csv")),
            "seed_count": len(seeds), "coverage": coverage,
            "edges": sum(r["following_count"] for r in coverage),
            "available_sources": sum(r["status"] == "complete" for r in coverage)}
    write_json(workspace.file("data/cofollow.meta.json"), meta)
    return meta


def review_types(path):
    raw = read_json(path, {})
    return {"organizations": {k.lower(): v for k, v in raw.get("organizations", {}).items()},
            "individuals": {k.lower() for k in raw.get("individuals", [])},
            "bd_accounts": {k.lower(): v for k, v in raw.get("bd_accounts", {}).items()},
            "uncertain": {k.lower(): v for k, v in raw.get("uncertain", {}).items()}}


def rank(workspace, mode=None, minimum=None, max_followers=None, all_targets=False):
    workspace.require_aggregate()
    config = workspace.config
    mode = mode or ("cofollow" if config.mode == "both" else config.mode)
    minimum = config.min_common if minimum is None else minimum
    if mode not in {"cofollow", "gems"} or minimum < 1:
        raise ValueError("Invalid ranking mode or threshold")
    if mode == "cofollow" and max_followers is not None:
        raise ValueError("--max-followers applies only to gems, never cofollow")
    maximum = config.gem_max_followers if max_followers is None else max_followers
    if maximum < 1:
        raise ValueError("max-followers must be positive")
    settings = {"minimum": minimum, "max_followers": maximum if mode == "gems" else None,
                "all_targets": all_targets, "individuals_only": config.individuals_only, "exclude_bd": config.exclude_bd}
    reviews = review_types(workspace.file("input/reviews.json"))
    rows = []
    for r in read_csv(workspace.file("data/cofollow.csv")):
        h, n = r["userName"].lower(), int(r["followed_by_seeds"])
        if n < minimum or r["already_followed"].lower() == "true" or config.exclude_members and r["is_member"].lower() == "true" or h == config.owner.lower().lstrip("@"):
            continue
        count = len({s.lower() for s in r["followed_by_list"].split("|") if s})
        if count != n:
            raise ValueError("Distinct source count mismatch: " + h)
        if mode == "gems":
            followers = int(r["followers_count"] or 0)
            if followers < n or followers >= maximum:
                continue
            r["gem_index"] = round(100 * n / math.sqrt(followers + config.gem_smoothing), 2)
        kind, reason = classify(r, reviews)
        bd = bool(BD.search(r.get("description") or "")) or h in reviews["bd_accounts"]
        if h in reviews["uncertain"]:
            kind, reason = "uncertain", reviews["uncertain"][h]
        if h in reviews["bd_accounts"]:
            kind, reason = "individual", reviews["bd_accounts"][h]
        selected = all_targets or (not config.individuals_only or kind == "individual" and r.get("isAutomated", "").lower() != "true") and (not config.exclude_bd or not bd)
        r.update(common_follows=n, account_type=kind, account_type_reason=reason, bd_profile_flag=bd, selected=selected,
                 selection_reason="selected" if selected else "bd_role" if bd and config.exclude_bd else "automated" if r.get("isAutomated", "").lower() == "true" else kind)
        rows.append(r)
    rows.sort(key=lambda r: (-(r["gem_index"] if mode == "gems" else r["common_follows"]), r["userName"].lower()))
    for i, row in enumerate(rows, 1):
        row["rank"] = i
    columns = RANK_COLUMNS + (["gem_index"] if mode == "gems" else [])
    write_csv(workspace.file("rankings/" + mode + "/all.csv"), rows, columns)
    selected = [{**r, "rank": i} for i, r in enumerate((r for r in rows if r["selected"]), 1)]
    write_csv(workspace.file("rankings/" + mode + "/selected.csv"), selected, columns)
    meta = {"created_at": now(), "mode": mode, "settings": settings, "all": len(rows), "selected": len(selected),
            "all_hash": file_hash(workspace.file("rankings/" + mode + "/all.csv")),
            "selected_hash": file_hash(workspace.file("rankings/" + mode + "/selected.csv")),
            "signature": workspace.rank_signature(mode, settings), "selection_counts": dict(Counter(r["selection_reason"] for r in rows))}
    write_json(workspace.file("rankings/" + mode + "/meta.json"), meta)
    return meta


def require_rank(workspace, mode):
    workspace.require_aggregate()
    meta = read_json(workspace.file("rankings/" + mode + "/meta.json"), {})
    if (meta.get("signature") != workspace.rank_signature(mode, meta.get("settings", {}))
            or not meta.get("all_hash") or meta["all_hash"] != file_hash(workspace.file("rankings/" + mode + "/all.csv"))
            or not meta.get("selected_hash") or meta["selected_hash"] != file_hash(workspace.file("rankings/" + mode + "/selected.csv"))):
        raise ValueError("Ranking is missing or stale; run rank for this mode")
    return meta
