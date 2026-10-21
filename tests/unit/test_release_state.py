import copy
import threading
import unittest
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

from scripts.release_state import GitHubState, record_publication, reserve


class MemoryState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.state = {"schemaVersion": 1, "reservations": []}
        self.sha = 0
        self.conflicts = 0

    def read(self) -> tuple[dict, int]:
        with self.lock:
            return copy.deepcopy(self.state), self.sha

    def write(self, state: dict, sha: int) -> bool:
        with self.lock:
            if sha != self.sha:
                self.conflicts += 1
                return False
            self.state = copy.deepcopy(state)
            self.sha += 1
            return True


def context(commit: str = "a" * 40, **overrides: object) -> dict:
    return {
        "module": "event-contracts", "targetVersion": "0.0.1",
        "iteration": 1, "releaseLine": "main", "sourceCommit": commit,
        "branch": "main", "integrated": True, "approvedImpact": "patch",
        "registry": "ghcr.io/example", **overrides,
    }


class ReservationTests(unittest.TestCase):
    def test_reruns_reuse_version_even_after_iteration_changes(self) -> None:
        store = MemoryState()
        first = reserve(store, context())
        retry = reserve(store, context(iteration=2, targetVersion="0.1.0"))
        self.assertEqual(first, retry)
        self.assertEqual(len(store.state["reservations"]), 1)

    def test_counter_scope_and_natural_resets(self) -> None:
        store = MemoryState()
        self.assertEqual(reserve(store, context())["counter"], 1)
        self.assertEqual(reserve(store, context("b" * 40))["counter"], 2)
        for overrides in [
            {"module": "payment-api"}, {"iteration": 2}, {"targetVersion": "0.1.0"},
            {"releaseLine": "rc1", "iteration": None},
        ]:
            with self.subTest(overrides=overrides):
                self.assertEqual(reserve(store, context("c" * 40, **overrides))["counter"], 1)

    def test_concurrent_reservations_and_reruns(self) -> None:
        store = MemoryState()
        barrier = threading.Barrier(8)
        original_read = store.read
        local = threading.local()

        def overlapping_read() -> tuple[dict, int]:
            state, sha = original_read()
            if not getattr(local, "started", False):
                local.started = True
                barrier.wait(timeout=10)
            return state, sha

        with patch.object(store, "read", side_effect=overlapping_read):
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(lambda n: reserve(store, context(f"{n:040x}")), range(8)))
        self.assertEqual(sorted(result["counter"] for result in results), list(range(1, 9)))
        self.assertGreater(store.conflicts, 0)
        with ThreadPoolExecutor(max_workers=8) as pool:
            repeats = list(pool.map(lambda _: reserve(store, context()), range(8)))
        self.assertEqual(len({result["reservedVersion"] for result in repeats}), 1)

    def test_publication_recovery_and_digest_immutability(self) -> None:
        store = MemoryState()
        entry = reserve(store, context())
        publication = {
            "module": "event-contracts", "version": entry["reservedVersion"],
            "sourceCommit": "a" * 40, "digest": "sha256:" + "b" * 64,
            "repository": "ghcr.io/example/event-contracts",
            "workflowRun": "run", "testRun": "tests", "sbom": "digest", "provenance": "digest",
        }
        record_publication(store, entry, publication)
        self.assertEqual(reserve(store, context())["publication"], publication)
        record_publication(store, entry, publication)
        with self.assertRaisesRegex(ValueError, "cannot be changed"):
            record_publication(store, entry, {**publication, "digest": "sha256:" + "c" * 64})

    def test_invalid_state_fails_explicitly(self) -> None:
        store = MemoryState()
        store.state["schemaVersion"] = 2
        with self.assertRaises(ValueError):
            reserve(store, context())

    def test_corrupted_counter_and_duplicate_source_fail_explicitly(self) -> None:
        store = MemoryState()
        reserve(store, context())
        store.state["reservations"][0]["counter"] = 2
        with self.assertRaisesRegex(ValueError, "does not match"):
            reserve(store, context())
        store.state["reservations"][0]["counter"] = 1
        store.state["reservations"].append(copy.deepcopy(store.state["reservations"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            reserve(store, context())

    def test_contents_conflict_retries_only_after_actual_state_change(self) -> None:
        api = MagicMock()
        api.request.side_effect = urllib.error.HTTPError("https://api.github.com", 409, "conflict", {}, None)
        self.addCleanup(api.request.side_effect.close)
        store = GitHubState(api)
        with patch.object(store, "read", return_value=({}, "new-sha")):
            self.assertFalse(store.write({"schemaVersion": 1, "reservations": []}, "old-sha"))
        with patch.object(store, "read", return_value=({}, "old-sha")):
            with self.assertRaises(urllib.error.HTTPError):
                store.write({"schemaVersion": 1, "reservations": []}, "old-sha")
        api.request.side_effect = urllib.error.HTTPError("https://api.github.com", 403, "denied", {}, None)
        self.addCleanup(api.request.side_effect.close)
        with self.assertRaises(urllib.error.HTTPError):
            store.write({"schemaVersion": 1, "reservations": []}, "old-sha")

    def test_missing_state_branch_is_initialized_from_main_without_main_write(self) -> None:
        api = MagicMock()
        missing = urllib.error.HTTPError("https://api.github.com", 404, "missing", {}, None)
        self.addCleanup(missing.close)
        api.request.side_effect = [missing, {"object": {"sha": "a" * 40}}, {}, missing]
        self.assertEqual(GitHubState(api).read(), ({"schemaVersion": 1, "reservations": []}, None))
        self.assertEqual(api.request.call_args_list[2].args, (
            "POST", "/git/refs",
            {"ref": "refs/heads/automation/release-state", "sha": "a" * 40},
        ))


if __name__ == "__main__":
    unittest.main()
