"""CLI for reproducible list discovery, with explicit paid-network commands."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile

from . import __version__, pipeline
from .api import APIError, TwitterAPI, list_id
from .storage import now, read_json, write_json
from .workspace import Workspace, initialize


def merge_reviews(paths):
    result = {"individuals": {}, "organizations": {}, "bd_accounts": {}, "uncertain": {}}
    for path in paths or []:
        raw = read_json(path)
        if not isinstance(raw, dict) or not set(raw) & set(result):
            raise ValueError("Not a profile-review JSON: " + str(path))
        for kind in result:
            values = raw.get(kind, {})
            if isinstance(values, list):
                values = {h: "manual_profile_review" for h in values}
            if not isinstance(values, dict):
                raise ValueError("Each profile-review group must be a mapping")
            for h, reason in values.items():
                from .models import handle
                h = handle(h).lower()
                for group in result.values():
                    group.pop(h, None)
                result[kind][h] = reason.get("reason", "manual_profile_review") if isinstance(reason, dict) else str(reason)
    return result


def status(workspace):
    seeds = workspace.seeds()
    counts = {"missing": 0, "complete": 0, "unavailable": 0}
    for seed in seeds:
        entry = pipeline.cache(workspace, seed)
        counts[entry["status"] if entry else "missing"] += 1
    try:
        workspace.require_aggregate()
        aggregate = "ready"
    except ValueError:
        aggregate = "missing_or_stale"
    ranks = {}
    for mode in ("cofollow", "gems"):
        try:
            ranks[mode] = pipeline.require_rank(workspace, mode)["selected"]
        except ValueError:
            ranks[mode] = "missing_or_stale"
    return {"workspace": str(workspace.path), "members": len(workspace.members()), "voting_seeds": len(seeds),
            "exclusions": len(workspace.excluded()), "source_cache": counts, "aggregate": aggregate, "rankings": ranks,
            "fetch": read_json(workspace.file("state/fetch.json"), {})}


def network_options(command):
    command.add_argument("--env-file", help="Dotenv file (default: .env in the current directory)")
    command.add_argument("--key-env", default="TWITTERAPI_IO_KEY", help="API-key environment variable name")


def parser():
    p = argparse.ArgumentParser(prog="x-list-discovery", description="Find shared follows outside an X list, plus underfollowed gems")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--root", default="runs", help="Workspace parent directory (default: runs)")
    commands = p.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Create a workspace from CSV/JSON or an X List")
    init.add_argument("name")
    source = init.add_mutually_exclusive_group(required=True)
    source.add_argument("--members", help="CSV or JSON account list; works offline")
    source.add_argument("--list", "--list-url", dest="list_source", help="X List URL or numeric ID; calls the API")
    init.add_argument("--seeds", help="Explicit subset of members to use as voting sources")
    init.add_argument("--personal-seeds", action="store_true", help="Only use members whose profiles identify a person")
    init.add_argument("--people-only", action="store_true", help="Keep only personal accounts in the selected results")
    init.add_argument("--exclude-bd", action="store_true", help="Exclude BD/marketing profile signals from selected results")
    init.add_argument("--exclude", help="Optional CSV/JSON of already-followed accounts; never becomes a voting source")
    init.add_argument("--reviews", action="append", help="Profile-review JSON; repeatable, later decisions win")
    init.add_argument("--owner", default="", help="Optional account to exclude from targets")
    init.add_argument("--min-common", type=int, default=2)
    init.add_argument("--max-followers", type=int, default=50000, help="Default gem follower ceiling (exclusive)")
    network_options(init)
    for name in ("fetch", "aggregate", "rank", "export", "run", "status", "import-graph", "exclude-followings"):
        command = commands.add_parser(name)
        command.add_argument("name")
        if name in {"fetch", "run", "exclude-followings"}:
            network_options(command)
            command.add_argument("--workers", type=int, default=4)
        if name in {"fetch", "run"}:
            command.add_argument("--refresh", action="store_true", help="Archive and replace old source caches")
        if name in {"rank", "export", "run"}:
            command.add_argument("--mode", choices=["both", "cofollow", "gems"], help="Default: workspace mode (both)")
        if name in {"rank", "run"}:
            command.add_argument("--min-common", type=int)
            command.add_argument("--max-followers", type=int, help="Follower ceiling for gems only")
            command.add_argument("--all-targets", action="store_true", help="Skip optional profile filters; still exclude all original members")
        if name in {"export", "run"}:
            command.add_argument("--top", type=int, default=100, help="Preview rows in Excel; full selected results are in CSV")
        if name == "run":
            command.add_argument("--fetch", action="store_true", help="Enable paid API fetching; omitted means offline")
        if name == "import-graph":
            command.add_argument("path", help="JSON mapping every seed username to followed profiles")
        if name == "exclude-followings":
            command.add_argument("--account", required=True)
    commands.add_parser("lists")
    return p


def execute(args):
    if args.command == "lists":
        root = Path(args.root)
        return {"workspaces": [p.name for p in sorted(root.iterdir()) if (p / "config.json").is_file()] if root.exists() else []}
    ws = Workspace(args.root, args.name)
    def client():
        return TwitterAPI(env_file=args.env_file, key_env=args.key_env)
    def progress(name, done, total):
        print("[{}/{}] @{}".format(done, total, name), file=sys.stderr, flush=True)
    if args.command == "init":
        if ws.path.exists():
            raise ValueError("Workspace already exists; choose another name")
        settings = dict(owner=args.owner, min_common=args.min_common, gem_max_followers=args.max_followers,
                        individuals_only=args.people_only, exclude_bd=args.exclude_bd, personal_seeds_only=args.personal_seeds)
        reviews = merge_reviews(args.reviews)
        if args.members:
            return initialize(ws, args.members, args.seeds, args.exclude, reviews, **settings)
        uid = list_id(args.list_source)
        members = client().list_members(uid)
        if not members:
            raise ValueError("List returned no members; empty and inaccessible lists cannot be distinguished")
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "members.json"
            write_json(snapshot, members)
            result = initialize(ws, snapshot, args.seeds, args.exclude, reviews, **settings)
        origin = read_json(ws.file("state/origin.json"))
        write_json(ws.file("state/origin.json"), {**origin, "source": "https://x.com/i/lists/" + uid, "list_id": uid})
        return result
    config = ws.config
    if args.command == "status":
        return status(ws)
    with ws.lock():
        if args.command == "fetch":
            return pipeline.fetch(ws, client(), args.workers, args.refresh, progress)
        if args.command == "import-graph":
            return pipeline.import_graph(ws, args.path)
        if args.command == "aggregate":
            return {k: v for k, v in pipeline.aggregate(ws).items() if k != "coverage"}
        if args.command == "exclude-followings":
            api = client()
            user = api.user(args.account)
            users = api.followings(args.account)
            if not users and (user.get("following_count") != 0 or user.get("protected")):
                raise ValueError("Exclusion list is unavailable; existing snapshot preserved")
            write_json(ws.file("state/history/excluded-" + now().replace(":", "-") + ".json"), ws.excluded())
            write_json(ws.file("input/excluded.json"), users)
            write_json(ws.file("state/exclusions-origin.json"), {"role": "target_exclusions_only", "account": args.account,
                       "fetched_at": now(), "count": len(users)})
            return {"excluded_accounts": len(users), "voting_seeds_unchanged": len(ws.seeds())}
        mode = args.mode or config.mode
        modes = ("cofollow", "gems") if mode == "both" else (mode,)
        if args.command == "run":
            if args.refresh and not args.fetch:
                raise ValueError("--refresh requires --fetch; offline run never calls the API")
            if args.fetch:
                fetched = pipeline.fetch(ws, client(), args.workers, args.refresh, progress)
                if fetched["failed"]:
                    return fetched
            try:
                ws.require_aggregate()
            except ValueError:
                pipeline.aggregate(ws)
        if args.command in {"rank", "run"}:
            if mode == "cofollow" and args.max_followers is not None:
                raise ValueError("--max-followers applies only to gems, never cofollow")
            ranked = {m: pipeline.rank(ws, m, args.min_common, args.max_followers if m == "gems" else None, args.all_targets) for m in modes}
            if args.command == "rank":
                return {"rankings": ranked}
        if args.command in {"export", "run"}:
            from .export import report
            reports = {m: report(ws, m, args.top) for m in modes}
            result = {"workspace": str(ws.path), "reports": reports}
            write_json(ws.file("output/discovery.json"), result)
            return result
    raise ValueError("Unknown command")


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parser().parse_args(argv)
    try:
        result = execute(args)
    except (ValueError, OSError, APIError, TypeError) as error:
        print("Error: " + str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if isinstance(result, dict) and result.get("failed") else 0
