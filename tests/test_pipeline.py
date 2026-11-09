from pathlib import Path
import tempfile
import unittest
import os
import socket

from helpers import fixture, user
from x_list_discovery import pipeline
from x_list_discovery.models import profiles
from x_list_discovery.storage import file_hash, read_csv, read_json, write_json
from x_list_discovery.workspace import Workspace, initialize, process_alive


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_normalization_and_source_deduplication(self):
        path = self.root / "input.json"
        write_json(path, [{"username": "@Alice", "user_id": "9007199254740993", "followersCount": "5"},
                          {"screen_name": "AliceRenamed", "id": "9007199254740993"}])
        rows = profiles(path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "9007199254740993")
        write_json(path, [user("Alice", 1), user("ALICE", 2)])
        with self.assertRaisesRegex(ValueError, "conflicting IDs"):
            profiles(path)

    def test_default_seeds_remove_projects_keep_full_exclusion_population(self):
        ws, _, _ = fixture(self.root)
        self.assertEqual(len(ws.members()), 3)
        self.assertEqual([r["userName"] for r in ws.seeds()], ["Alice", "Bob"])
        review = read_csv(ws.file("input/seed_review.csv"))
        self.assertEqual(review[-1]["voting_source"], "False")
        pipeline.rank(ws, minimum=2, all_targets=True)
        names = {r["userName"] for r in read_csv(ws.file("rankings/cofollow/all.csv"))}
        self.assertFalse(names & {"Project", "RenamedOwned", "Me"})

    def test_empty_or_unreviewed_sources_fail_before_creating_workspace(self):
        path = self.root / "names.json"
        write_json(path, [{"username": "Unknown"}])
        ws = Workspace(self.root, "unknown")
        with self.assertRaisesRegex(ValueError, "No voting sources"):
            initialize(ws, path, personal_seeds_only=True)
        self.assertFalse(ws.path.exists())
        initialize(ws, path, seeds=path)
        self.assertEqual(len(ws.seeds()), 1)

    def test_source_subset_and_workspace_paths(self):
        members = self.root / "members.json"
        seeds = self.root / "seeds.json"
        write_json(members, [user("Alice", 1)])
        write_json(seeds, [user("Other", 9)])
        with self.assertRaisesRegex(ValueError, "must come from"):
            initialize(Workspace(self.root, "bad"), members, seeds)
        write_json(seeds, [user("Alice", 99)])
        with self.assertRaisesRegex(ValueError, "must come from"):
            initialize(Workspace(self.root, "bad-id"), members, seeds)
        with self.assertRaises(ValueError):
            Workspace(self.root, "../escape")

    def test_distinct_votes_and_missing_target_id(self):
        ws, graph, path = fixture(self.root)
        graph["Bob"][0]["id"] = ""
        write_json(path, graph)
        pipeline.import_graph(ws, path)
        pipeline.aggregate(ws)
        alpha = [r for r in read_csv(ws.file("data/cofollow.csv")) if r["userName"] == "Alpha"]
        self.assertEqual(len(alpha), 1)
        self.assertEqual(alpha[0]["followed_by_seeds"], "2")
        self.assertEqual(alpha[0]["followed_by_list"], "Alice|Bob")

    def test_cofollow_is_invariant_to_popularity_and_gems_are_separate(self):
        ws, _, _ = fixture(self.root)
        before = {str(p): file_hash(p) for p in ws.file("input").iterdir()}
        pipeline.rank(ws, minimum=2, all_targets=True)
        original = [(r["userName"], r["common_follows"]) for r in read_csv(ws.file("rankings/cofollow/all.csv"))]
        self.assertIn(("Alpha", "2"), original)  # impossible follower count stays eligible
        for seed in ws.seeds():
            path = ws.cache_path(seed)
            entry = read_json(path)
            for u in entry["users"]:
                u["followers_count"] = 999999999 if u["userName"] == "Alpha" else None
            write_json(path, entry)
        with self.assertRaisesRegex(ValueError, "stale"):
            pipeline.rank(ws, minimum=2)
        pipeline.aggregate(ws)
        pipeline.rank(ws, minimum=2, all_targets=True)
        changed = [(r["userName"], r["common_follows"]) for r in read_csv(ws.file("rankings/cofollow/all.csv"))]
        self.assertEqual(original, changed)
        with self.assertRaisesRegex(ValueError, "only to gems"):
            pipeline.rank(ws, max_followers=50000)
        aggregate_hash = file_hash(ws.file("data/cofollow.csv"))
        pipeline.rank(ws, mode="gems", minimum=2)
        self.assertEqual(file_hash(ws.file("data/cofollow.csv")), aggregate_hash)
        self.assertEqual(before, {str(p): file_hash(p) for p in ws.file("input").iterdir()})

    def test_person_policy_separates_organization_uncertain_bd_and_bot(self):
        ws, _, _ = fixture(self.root)
        meta = pipeline.rank(ws, minimum=2)
        rows = read_csv(ws.file("rankings/cofollow/selected.csv"))
        self.assertEqual([r["userName"] for r in rows], ["Alpha", "Gamma"])
        self.assertEqual(meta["selection_counts"], {"selected": 2, "bd_role": 1, "automated": 1, "uncertain": 1, "organization": 1})
        reviews = {"individuals": {"mystery": "Confirmed personal account"}, "organizations": {}}
        write_json(ws.file("input/reviews.json"), reviews)
        with self.assertRaisesRegex(ValueError, "stale"):
            pipeline.require_rank(ws, "cofollow")
        ws.require_aggregate()  # profile review does not invalidate the network
        pipeline.rank(ws, minimum=2)
        self.assertEqual(len(read_csv(ws.file("rankings/cofollow/selected.csv"))), 3)

    def test_gem_score_and_strict_follower_boundary(self):
        ws, _, _ = fixture(self.root)
        original = file_hash(ws.file("data/cofollow.csv"))
        pipeline.rank(ws, mode="gems", minimum=2, max_followers=50000)
        rows = read_csv(ws.file("rankings/gems/selected.csv"))
        self.assertEqual([r["userName"] for r in rows], ["Gamma"])
        self.assertEqual(float(rows[0]["gem_index"]), 1.91)
        pipeline.rank(ws, mode="gems", minimum=2, max_followers=10000)
        self.assertEqual(read_csv(ws.file("rankings/gems/selected.csv")), [])
        self.assertEqual(file_hash(ws.file("data/cofollow.csv")), original)

    def test_stale_and_tampered_results_are_rejected(self):
        ws, _, _ = fixture(self.root)
        pipeline.rank(ws, minimum=2)
        path = ws.file("rankings/cofollow/selected.csv")
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "stale"):
            pipeline.require_rank(ws, "cofollow")
        config = read_json(ws.config_path)
        config["min_common"] = 2
        write_json(ws.config_path, config)
        ws.require_aggregate()
        path = ws.file("data/cofollow.csv")
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "stale"):
            ws.require_aggregate()

    def test_lists_and_graph_import_are_isolated(self):
        one, _, _ = fixture(self.root, "one")
        two, _, _ = fixture(self.root, "two", ready=False)
        self.assertNotEqual(one.cache_path(one.seeds()[0]), two.cache_path(two.seeds()[0]))
        with self.assertRaisesRegex(ValueError, "Incomplete source"):
            pipeline.aggregate(two)
        path = self.root / "wrong.json"
        write_json(path, {"MyActualFollow": []})
        with self.assertRaisesRegex(ValueError, "Source population mismatch"):
            pipeline.import_graph(two, path)
        self.assertFalse(two.cache_path(two.seeds()[0]).exists())
        one.require_aggregate()

    def test_workspace_lock(self):
        ws, _, _ = fixture(self.root)
        with ws.lock():
            with self.assertRaisesRegex(ValueError, "Another command"):
                with ws.lock():
                    pass
        self.assertFalse(ws.file("state/workspace.lock").exists())

    def test_live_and_stale_lock_processes(self):
        self.assertTrue(process_alive(os.getpid()))
        self.assertFalse(process_alive(99999999))
        ws, _, _ = fixture(self.root)
        write_json(ws.file("state/workspace.lock"), {"pid": 99999999, "host": socket.gethostname()})
        with ws.lock():
            self.assertEqual(read_json(ws.file("state/workspace.lock"))["pid"], os.getpid())


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws, self.graph, _ = fixture(self.tmp.name, ready=False)

    def tearDown(self):
        self.tmp.cleanup()

    def test_failed_source_is_not_cached_and_retry_skips_success(self):
        graph, calls = self.graph, []

        class Fake:
            failing = True
            def followings(self, h):
                calls.append(h)
                if h == "Bob" and self.failing:
                    raise ValueError("temporary failure")
                return graph[h]

        client = Fake()
        self.assertTrue(pipeline.fetch(self.ws, client, workers=1)["failed"])
        with self.assertRaisesRegex(ValueError, "Incomplete source"):
            pipeline.aggregate(self.ws)
        client.failing = False
        result = pipeline.fetch(self.ws, client, workers=1)
        self.assertFalse(result["failed"])
        self.assertEqual(calls.count("Alice"), 1)
        self.assertEqual(calls.count("Bob"), 2)
        pipeline.aggregate(self.ws)

    def test_empty_protected_source_is_coverage_gap(self):
        class Fake:
            def followings(self, h):
                return []
            def user(self, h):
                return {"following_count": 100, "protected": True}
        result = pipeline.fetch(self.ws, Fake(), workers=1)
        self.assertFalse(result["failed"])
        meta = pipeline.aggregate(self.ws)
        self.assertEqual(meta["available_sources"], 0)
        self.assertTrue(all(r["status"] == "unavailable" for r in meta["coverage"]))

    def test_failed_refresh_blocks_an_unchanged_previous_aggregate(self):
        graph = self.graph
        class Good:
            def followings(self, h):
                return graph[h]
        pipeline.fetch(self.ws, Good(), workers=1)
        pipeline.aggregate(self.ws)
        class Bad:
            def followings(self, h):
                raise ValueError("failure")
        # Old files survive, but their existence cannot turn a failed refresh into success.
        old_signature = self.ws.aggregate_signature()
        pipeline.fetch(self.ws, Bad(), refresh=True, workers=1)
        self.assertEqual(old_signature, self.ws.aggregate_signature())
        with self.assertRaisesRegex(ValueError, "interrupted or failed"):
            pipeline.rank(self.ws)

    def test_interrupted_refresh_resumes_without_using_old_success_as_new(self):
        for seed in self.ws.seeds():
            entry = pipeline._entry(seed, self.graph[seed["userName"]], "fixture", "2000-01-01T00:00:00+00:00")
            write_json(self.ws.cache_path(seed), entry)
        previous = {"finished": False, "refresh": True, "source_signature": pipeline.digest(self.ws.seeds()),
                    "started_at": "2020-01-01T00:00:00+00:00", "failed": {}}
        write_json(self.ws.file("state/fetch.json"), previous)
        with self.assertRaisesRegex(ValueError, "interrupted"):
            pipeline.aggregate(self.ws)
        alice = self.ws.cache_path(self.ws.seeds()[0])
        entry = read_json(alice)
        entry["fetched_at"] = "2021-01-01T00:00:00+00:00"
        write_json(alice, entry)
        calls, graph = [], self.graph
        class Fake:
            def followings(self, h):
                calls.append(h)
                return graph[h]
        pipeline.fetch(self.ws, Fake(), workers=1)
        self.assertEqual(calls, ["Bob"])
        self.assertEqual(len(list(self.ws.file("state/history").rglob("id-2.json"))), 1)
