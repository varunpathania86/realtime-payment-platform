import base64
import copy
from datetime import datetime, timezone
import io
import json
import hashlib
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from scripts import release_baseline as baseline


SOURCE = "a" * 40
DIGEST = "sha256:" + "b" * 64
RUN = "https://github.com/example/platform/actions/runs/123"
NOW = datetime(2026, 11, 2, tzinfo=timezone.utc)


def entry(source=SOURCE, counter=1):
    return {
        "module": "api", "targetVersion": "0.1.0", "iteration": 1, "releaseLine": "main",
        "sourceCommit": source, "counter": counter, "reservedVersion": f"0.1.0-it1-rv.{counter}",
        "branch": "main", "integrated": True, "registry": "ghcr.io/example",
        "approvedImpact": "minor", "chartRegistry": "oci://ghcr.io/example/charts",
        "publication": {
            "module": "api", "version": f"0.1.0-it1-rv.{counter}", "sourceCommit": source,
            "repository": "ghcr.io/example/api", "digest": DIGEST,
            "workflowRun": RUN, "testRun": RUN, "sbom": "image@digest", "provenance": "image@digest",
            "chart": {
                "repository": "ghcr.io/example/charts/api", "version": f"0.1.0-it1-rv.{counter}",
                "digest": "sha256:" + "c" * 64,
            },
        },
    }


def missing():
    return urllib.error.HTTPError("https://example.test", 404, "missing", {}, io.BytesIO())


class BaselineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "solution.yaml").write_text(
            "schemaVersion: 1\nsolution:\n  name: platform\n  version: 0.0.0\n"
            "pi:\n  id: PI-2026.4\n  startDate: '2026-10-26'\n"
            "  iterations: 6\n  iterationLengthDays: 14\nmodules: [api, future]\n"
        )
        self.module = self.root / "modules" / "api"
        (self.module / "chart").mkdir(parents=True)
        (self.module / "Dockerfile").write_text("FROM scratch\n")
        (self.module / "module.yaml").write_text("name: api\ndependencies: []\n")
        (self.module / "chart" / "Chart.yaml").write_text("name: api\n")
        baseline.git(self.root, "init", "-b", "main")
        baseline.git(self.root, "config", "user.email", "synthetic@example.test")
        baseline.git(self.root, "config", "user.name", "Synthetic")
        self.commit()
        self.source = baseline.git(self.root, "rev-parse", "HEAD")

    def commit(self):
        baseline.git(self.root, "add", ".")
        baseline.git(self.root, "commit", "-m", "test fixture")

    def bundle(self):
        return baseline.build_bundle(
            self.root, [entry()], "0.1.0-it1", SOURCE, "example/platform", RUN, NOW,
        )

    def test_strict_iteration_version(self):
        self.assertEqual(baseline.baseline_version("1.2.3-it12"), ("1.2.3", 12))
        for value in ["1.2.3", "1.2.3-rc1", "1.2.3-it1-rv.2", "../it1", "01.2.3-it1"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                baseline.baseline_version(value)

    def test_manifest_shapes_and_exact_chart_bytes_preserved(self):
        record = self.bundle()
        self.assertEqual(record["solution"]["solution"]["gitTag"], "solution/v0.1.0-it1")
        module = record["modules"][0]
        self.assertEqual(module["source"]["gitTag"], "modules/api/v0.1.0-it1")
        self.assertEqual(module["artifacts"]["images"][0]["digest"], DIGEST)
        self.assertEqual(module["artifacts"]["images"][0]["tag"], "0.1.0-it1")
        self.assertEqual(module["artifacts"]["charts"][0], entry()["publication"]["chart"])
        baseline.schema(record["solution"], "solution-release.schema.json")
        baseline.schema(module, "module-release.schema.json")
        self.assertIn("0.0.0", (self.root / "solution.yaml").read_text())

    def test_new_baseline_requires_current_iteration_and_greater_target(self):
        for version in ["0.1.0-it2", "0.0.0-it1"]:
            with self.subTest(version=version), self.assertRaises(ValueError):
                baseline.build_bundle(self.root, [entry()], version, SOURCE, "example/platform", RUN, NOW)
        with self.assertRaises(ValueError):
            baseline.build_bundle(
                self.root, [entry()], "0.1.0-it1", SOURCE, "example/platform", RUN,
                datetime(2026, 10, 1, tzinfo=timezone.utc),
            )

    def test_discovery_skips_catalog_only_modules(self):
        state = {"schemaVersion": 1, "reservations": [entry(self.source)]}
        selected = baseline.select_publications(self.root, state, self.source)
        self.assertEqual([item["module"] for item in selected], ["api"])

    def test_latest_ancestor_not_counter_is_selected(self):
        older = entry(self.source, 5)
        (self.root / "note.txt").write_text("new main commit")
        self.commit()
        newer = baseline.git(self.root, "rev-parse", "HEAD")
        selected = baseline.select_publications(
            self.root, {"schemaVersion": 1, "reservations": [entry(newer), older]}, newer,
        )
        self.assertEqual(selected[0]["sourceCommit"], newer)

    def test_changed_module_or_global_pins_block_stale_publication(self):
        for filename in ["modules/api/Dockerfile", ".tool-versions", "solution.yaml"]:
            with self.subTest(filename=filename):
                path = self.root / filename
                original = path.read_text() if path.exists() else ""
                path.write_text(original + "\n# change\n")
                self.commit()
                newer = baseline.git(self.root, "rev-parse", "HEAD")
                with self.assertRaisesRegex(ValueError, "changed since publication"):
                    baseline.select_publications(
                        self.root, {"schemaVersion": 1, "reservations": [entry(self.source)]}, newer,
                    )

    def test_incomplete_or_unpublished_modules_block(self):
        for reservation in [None, entry(self.source)]:
            reservations = [] if reservation is None else [reservation]
            if reservation:
                del reservation["publication"]["chart"]
            with self.subTest(reservation=reservation), self.assertRaises(ValueError):
                baseline.select_publications(
                    self.root, {"schemaVersion": 1, "reservations": reservations}, self.source,
                )

    def test_dependency_changes_require_a_new_dependent_publication(self):
        dependency = self.root / "modules" / "contract"
        dependency.mkdir()
        (dependency / "module.yaml").write_text("name: contract\ndependencies: []\n")
        (dependency / "schema.json").write_text("{}")
        (self.module / "module.yaml").write_text("name: api\ndependencies: [contract]\n")
        self.commit()
        published = baseline.git(self.root, "rev-parse", "HEAD")
        (dependency / "schema.json").write_text('{"changed": true}')
        self.commit()
        newer = baseline.git(self.root, "rev-parse", "HEAD")
        with self.assertRaisesRegex(ValueError, "changed since publication"):
            baseline.select_publications(
                self.root, {"schemaVersion": 1, "reservations": [entry(published)]}, newer,
            )

    def test_freeze_is_create_only_and_retry_safe(self):
        api = MagicMock()
        api.request.side_effect = [missing(), {}]
        bundle = self.bundle()
        self.assertEqual(baseline.freeze(api, "record.json", bundle), bundle)
        payload = api.request.call_args.args[2]
        self.assertNotIn("sha", payload)
        self.assertEqual(json.loads(base64.b64decode(payload["content"])), bundle)
        api.reset_mock()
        api.request.side_effect = None
        api.request.return_value = {"content": base64.b64encode(json.dumps(bundle).encode()).decode()}
        self.assertEqual(baseline.freeze(api, "record.json", bundle), bundle)
        api.request.assert_called_once()
        changed = copy.deepcopy(bundle)
        changed["solution"]["solution"]["sourceCommit"] = "d" * 40
        with self.assertRaisesRegex(ValueError, "conflicts"):
            baseline.freeze(api, "record.json", changed)

    def test_freeze_race_cannot_replace_another_selection(self):
        api = MagicMock()
        conflict = urllib.error.HTTPError("https://example.test", 409, "conflict", {}, io.BytesIO())
        with patch.object(baseline, "read_record", side_effect=[None, {"different": "record"}]):
            api.request.side_effect = conflict
            with self.assertRaisesRegex(ValueError, "concurrent"):
                baseline.freeze(api, "record.json", self.bundle())

    def test_build_gate_rejects_forks_failed_or_branch_runs(self):
        api = MagicMock()
        good = {"conclusion": "success", "head_sha": SOURCE, "head_branch": "main",
                "head_repository": {"full_name": "example/platform"}}
        api.request.return_value = {"workflow_runs": [good]}
        baseline.require_build(api, "example/platform", SOURCE)
        for change in [
            {"conclusion": "failure"}, {"head_branch": "feature/test"},
            {"head_repository": {"full_name": "fork/platform"}}, {"head_sha": "d" * 40},
        ]:
            api.request.return_value = {"workflow_runs": [{**good, **change}]}
            with self.subTest(change=change), self.assertRaises(ValueError):
                baseline.require_build(api, "example/platform", SOURCE)

    def test_registry_and_chart_integrity_are_required(self):
        bundle = self.bundle()
        with patch.object(baseline, "registry_manifest", return_value=(DIGEST, {})):
            with self.assertRaisesRegex(ValueError, "chart digest"):
                baseline.verify_artifacts(bundle)
        image = bundle["modules"][0]["artifacts"]["images"][0]
        with patch.object(baseline, "registry_manifest", return_value=(DIGEST, {})) as lookup:
            baseline.promote_image(image)
            lookup.assert_called_once()
        with patch.object(baseline, "registry_manifest", return_value=("sha256:" + "d" * 64, {})):
            with self.assertRaisesRegex(ValueError, "overwrite"):
                baseline.promote_image(image)

    def test_publication_requires_completed_trusted_workflow(self):
        api = MagicMock()
        good = {"conclusion": "success", "event": "workflow_run",
                "path": ".github/workflows/publish.yml",
                "head_repository": {"full_name": "example/platform"}}
        api.request.return_value = good
        baseline.require_publication(api, "example/platform", RUN)
        for change in [
            {"conclusion": "failure"}, {"event": "pull_request"}, {"path": ".github/workflows/build.yml"},
        ]:
            api.request.return_value = {**good, **change}
            with self.subTest(change=change), self.assertRaises(ValueError):
                baseline.require_publication(api, "example/platform", RUN)
        with self.assertRaisesRegex(ValueError, "this repository"):
            baseline.require_publication(api, "example/platform", "https://github.com/fork/repo/actions/runs/123")

    def test_verify_chart_pins_selected_image(self):
        bundle = self.bundle()
        module = bundle["modules"][0]
        image = module["artifacts"]["images"][0]
        chart = module["artifacts"]["charts"][0]
        with patch.object(baseline, "registry_manifest", side_effect=[(DIGEST, {}), (chart["digest"], {})]), \
                patch.object(baseline, "chart_metadata", return_value=(
                    {"name": "api", "version": chart["version"]},
                    {"image": {"repository": image["repository"], "digest": "sha256:" + "e" * 64}},
                )):
            with self.assertRaisesRegex(ValueError, "pin"):
                baseline.verify_artifacts(bundle)

    def test_existing_tag_must_match_commit_and_manifest(self):
        api = MagicMock()
        manifest = self.bundle()["solution"]
        api.request.side_effect = [
            {"object": {"type": "tag", "sha": "tag-object"}},
            {"object": {"type": "commit", "sha": SOURCE}, "message": json.dumps(manifest, sort_keys=True)},
        ]
        baseline.ensure_tag(api, "solution/v0.1.0-it1", SOURCE, manifest)
        api.request.side_effect = [{"object": {"type": "commit", "sha": SOURCE}}]
        with self.assertRaisesRegex(ValueError, "annotated"):
            baseline.ensure_tag(api, "solution/v0.1.0-it1", SOURCE, manifest)

    def test_release_rerun_checks_frozen_body(self):
        api = MagicMock()
        api.request.side_effect = [missing(), {}]
        manifest = self.bundle()["solution"]
        baseline.release(api, manifest, "solution/v0.1.0-it1")
        created = api.request.call_args.args[2]
        self.assertTrue(created["prerelease"])
        self.assertEqual(created["make_latest"], "false")
        api.request.side_effect = None
        api.request.return_value = {"body": created["body"], "prerelease": True, "draft": False}
        baseline.release(api, manifest, "solution/v0.1.0-it1")
        api.request.return_value["body"] = "changed"
        with self.assertRaisesRegex(ValueError, "conflicts"):
            baseline.release(api, manifest, "solution/v0.1.0-it1")

    def test_promotion_copies_exact_manifest_bytes_without_building(self):
        raw = b'{"schemaVersion":2,"mediaType":"application/vnd.oci.image.index.v1+json"}'
        digest = "sha256:" + hashlib.sha256(raw).hexdigest()
        image = {"repository": "ghcr.io/example/api", "tag": "0.1.0-it1", "digest": digest}
        token = MagicMock()
        token.__enter__.return_value = io.BytesIO(b'{"token":"synthetic-token"}')
        source = MagicMock()
        source.__enter__.return_value = MagicMock(
            read=lambda: raw, headers={"Content-Type": "application/vnd.oci.image.index.v1+json"},
        )
        put = MagicMock()
        with patch.dict("os.environ", {"GITHUB_ACTOR": "synthetic", "GH_TOKEN": "synthetic-only"}), \
                patch.object(baseline, "registry_manifest", side_effect=[None, (digest, {})]), \
                patch.object(baseline.urllib.request, "urlopen", side_effect=[token, source, put]) as request:
            baseline.promote_image(image)
        written = request.call_args_list[-1].args[0]
        self.assertEqual(written.method, "PUT")
        self.assertEqual(written.data, raw)
        self.assertEqual(written.full_url, "https://ghcr.io/v2/example/api/manifests/0.1.0-it1")

    def test_replay_uses_frozen_selection_without_recalculating_iteration(self):
        bundle = baseline.build_bundle(
            self.root, [entry(self.source)], "0.1.0-it1", self.source, "example/platform", RUN, NOW,
        )
        env = {
            "GITHUB_REPOSITORY": "example/platform", "GH_TOKEN": "synthetic-only",
            "GITHUB_REF": "refs/heads/main", "BASELINE_SOURCE": self.source,
            "BASELINE_VERSION": "0.1.0-it1", "SOURCE_ROOT": str(self.root),
            "BASELINE_OUTPUT": str(self.root / "output"),
        }
        baseline.git(self.root, "update-ref", "refs/remotes/origin/main", self.source)
        with patch.dict("os.environ", env), \
                patch.object(baseline, "GitHubAPI"), \
                patch.object(baseline, "require_build"), \
                patch.object(baseline, "validate_catalog", return_value=[]), \
                patch.object(baseline, "read_record", return_value=bundle), \
                patch.object(baseline, "build_bundle", side_effect=AssertionError("must not recalculate")), \
                patch.object(baseline, "verify_artifacts"), \
                patch.object(baseline, "promote_image") as promote, \
                patch.object(baseline, "ensure_tag") as tag, \
                patch.object(baseline, "release") as release:
            baseline.main()
        promote.assert_called_once()
        self.assertEqual(tag.call_count, 2)
        self.assertEqual(release.call_count, 2)
        saved = json.loads((self.root / "output" / "solution-release.json").read_text())
        self.assertEqual(saved, bundle["solution"])

    def test_non_main_execution_fails_before_release_side_effects(self):
        with patch.dict("os.environ", {
            "GITHUB_REPOSITORY": "example/platform", "GH_TOKEN": "synthetic-only",
            "GITHUB_REF": "refs/heads/feature/test",
        }), patch.object(baseline, "GitHubAPI") as api:
            with self.assertRaisesRegex(SystemExit, "trusted main"):
                baseline.main()
            api.return_value.request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
