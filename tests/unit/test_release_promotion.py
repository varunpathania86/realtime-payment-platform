import base64
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from scripts import release_baseline as baseline
from scripts import release_promotion as promotion


SOURCE = "a" * 40
DIGEST = "sha256:" + "b" * 64


def module_manifest(name="api", version="0.1.0-it1", source=SOURCE):
    return {
        "schemaVersion": 1,
        "kind": "module-release",
        "module": name,
        "version": version,
        "source": {
            "repository": "https://github.com/example/platform",
            "commit": source,
            "gitTag": f"modules/{name}/v{version}",
        },
        "workflow": {"runId": "123", "url": "https://github.com/example/platform/actions/runs/123"},
        "artifacts": {
            "images": [{
                "repository": f"ghcr.io/example/{name}", "tag": version, "digest": DIGEST,
            }],
            "charts": [],
        },
        "evidence": {
            "tests": ["build", "publication"], "sbom": "image@digest",
            "provenance": "image@digest", "approvedImpact": "patch",
        },
    }


def solution_bundle(
    version="0.1.0-it1", pi="PI-2026.4", source=SOURCE, module_version="0.1.0-it1"
):
    module = module_manifest(version=module_version)
    return {
        "solution": {
            "schemaVersion": 1, "kind": "solution-release", "pi": {"id": pi},
            "solution": {
                "version": version, "sourceCommit": source,
                "gitTag": f"solution/v{version}",
            },
            "modules": [{
                "name": "api", "version": module["version"], "sourceCommit": source,
                "artifacts": module["artifacts"],
                "workflowRun": module["workflow"]["url"], "approvedImpact": "patch",
            }],
            "qa": {"evidence": ["tested"], "runForRecord": "https://github.com/example/platform/actions/runs/123"},
        },
        "modules": [module],
    }


def reservation(source, name="api"):
    version = "0.0.1-it1-rv.1"
    return {
        "module": name, "targetVersion": "0.0.1", "iteration": 1, "releaseLine": "main",
        "sourceCommit": source, "reservedVersion": version, "counter": 1, "branch": "main",
        "integrated": True, "registry": "ghcr.io/example", "approvedImpact": "patch",
        "publication": {
            "module": name, "version": version, "sourceCommit": source,
            "repository": f"ghcr.io/example/{name}", "digest": DIGEST,
            "workflowRun": "https://github.com/example/platform/actions/runs/123",
            "testRun": "https://github.com/example/platform/actions/runs/124",
            "sbom": "image@digest", "provenance": "image@digest",
        },
    }


class PromotionInputTests(unittest.TestCase):
    def test_defaults_and_valid_choices(self):
        self.assertEqual(
            promotion.validate_inputs(None, None, None, None),
            ("solution", "", "patch", "iteration"),
        )
        self.assertEqual(
            promotion.validate_inputs("module", "payment-api", "minor", "release_candidate"),
            ("module", "payment-api", "minor", "release_candidate"),
        )

    def test_workflow_exposes_the_required_defaults_without_sha_or_version_inputs(self):
        import yaml

        workflow = yaml.load(
            (baseline.ROOT / ".github" / "workflows" / "release-baseline.yml").read_text(),
            Loader=yaml.BaseLoader,
        )
        inputs = workflow["on"]["workflow_dispatch"]["inputs"]
        self.assertEqual(inputs["release"]["default"], "solution")
        self.assertEqual(inputs["release"]["options"], ["module", "solution"])
        self.assertEqual(inputs["module"]["default"], "")
        self.assertEqual(inputs["type"]["default"], "patch")
        self.assertEqual(inputs["type"]["options"], ["major", "minor", "patch"])
        self.assertEqual(inputs["promote"]["default"], "iteration")
        self.assertEqual(
            inputs["promote"]["options"], ["iteration", "release_candidate", "stable"]
        )
        self.assertEqual(set(inputs), {"release", "module", "type", "promote"})
        self.assertIn("release-stable", workflow["jobs"]["release"]["environment"])
        self.assertEqual(workflow["jobs"]["stable-catalog"]["environment"], "release-stable")
        self.assertEqual(workflow["jobs"]["stable-catalog"]["permissions"]["actions"], "read")
        self.assertEqual(workflow["jobs"]["stable-catalog"]["needs"], "release")
        self.assertIn("inputs.promote == 'stable'", workflow["jobs"]["stable-catalog"]["if"])
        self.assertIn("inputs.release", workflow["concurrency"]["group"])

    def test_rejects_invalid_or_inconsistent_inputs(self):
        cases = [
            ("solution", "api", "patch", "iteration"),
            ("module", "", "patch", "iteration"),
            ("module", "../api", "patch", "iteration"),
            ("solution", "", "none", "iteration"),
            ("solution", "", "patch", "rc"),
        ]
        for values in cases:
            with self.subTest(values=values), self.assertRaises(ValueError):
                promotion.validate_inputs(*values)

    def test_stable_environment_requires_a_configured_required_reviewer(self):
        api = MagicMock()
        api.request.return_value = {
            "name": "release-stable",
            "protection_rules": [{
                "type": "required_reviewers",
                "reviewers": [{"type": "User", "reviewer": {"login": "release-owner"}}],
            }],
        }
        promotion.require_stable_environment(api)
        api.request.assert_called_once_with("GET", "/environments/release-stable")

    def test_stable_environment_missing_or_without_reviewers_fails_closed(self):
        api = MagicMock()
        for environment in [
            {"name": "release-stable", "protection_rules": []},
            {
                "name": "release-stable",
                "protection_rules": [{
                    "type": "required_reviewers",
                    "reviewers": [],
                }],
            },
            {
                "name": "release-stable",
                "protection_rules": [{
                    "type": "required_reviewers",
                    "reviewers": [{"type": "User", "reviewer": None}],
                }],
            },
            {"name": "another-environment", "protection_rules": []},
        ]:
            with self.subTest(environment=environment):
                api.request.return_value = environment
                with self.assertRaises(ValueError):
                    promotion.require_stable_environment(api)

    def test_stable_environment_api_errors_propagate(self):
        for status in (403, 404, 500):
            with self.subTest(status=status):
                api = MagicMock()
                error = urllib.error.HTTPError(
                    "https://api.github.com", status, "gate error", {}, io.BytesIO()
                )
                api.request.side_effect = error
                with self.assertRaises(urllib.error.HTTPError):
                    promotion.require_stable_environment(api)

    def test_catalog_target_uses_solution_and_standalone_module_impact_independently(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "solution.yaml").write_text(
                "solution:\n  version: 1.2.3\nmodules: [api]\n"
            )
            (root / "modules" / "api").mkdir(parents=True)
            (root / "modules" / "api" / "Dockerfile").write_text("FROM scratch\n")
            (root / "modules" / "api" / "module.yaml").write_text("version: 0.0.0\n")
            self.assertEqual(
                promotion._catalog_target(root, "solution", "", "major"),
                ("1.2.3", "2.0.0", ""),
            )
            self.assertEqual(
                promotion._catalog_target(root, "module", "api", "major"),
                ("0.0.0", "1.0.0", "major"),
            )

    def test_standalone_impact_changes_module_target_but_preserves_publication_evidence(self):
        publication = {
            "version": "0.0.1-it1-rv.1",
            "repository": "ghcr.io/example/api",
            "digest": DIGEST,
            "workflowRun": "https://github.com/example/platform/actions/runs/123",
            "testRun": "https://github.com/example/platform/actions/runs/124",
            "sbom": "image@digest",
            "provenance": "image@digest",
            "chart": {
                "repository": "ghcr.io/example/charts/api",
                "version": "0.0.1-it1-rv.1",
                "digest": "sha256:" + "c" * 64,
            },
        }
        entry = {
            "module": "api", "sourceCommit": SOURCE, "targetVersion": "0.0.1",
            "approvedImpact": "patch", "stableVersion": "0.0.0", "publication": publication,
        }
        stable, target, explicit_impact = "0.0.0", "1.0.0", "major"
        manifest = promotion.build_module_manifest(
            entry, f"{target}-it1", "example/platform",
            "https://github.com/example/platform/actions/runs/789", impact=explicit_impact,
        )
        self.assertEqual(stable, "0.0.0")
        self.assertEqual(manifest["version"], "1.0.0-it1")
        self.assertEqual(manifest["evidence"]["approvedImpact"], "major")
        self.assertEqual(manifest["artifacts"]["images"][0]["digest"], DIGEST)
        self.assertEqual(manifest["artifacts"]["images"][0]["tag"], "1.0.0-it1")
        self.assertEqual(
            manifest["artifacts"]["charts"][0],
            publication["chart"],
        )
        self.assertIn("target 0.0.1 and impact patch", manifest["evidence"]["qa"][1])
        self.assertEqual(manifest["evidence"]["provenance"], publication["provenance"])
        self.assertEqual(manifest["evidence"]["tests"], [publication["testRun"], publication["workflowRun"]])


class ModuleIsolationTests(unittest.TestCase):
    def test_unrelated_module_change_does_not_block_module_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "solution.yaml").write_text(
                "modules: [api, other]\nregistries:\n  images: ghcr.io/example\n"
            )
            for name in ("api", "other"):
                module = root / "modules" / name
                module.mkdir(parents=True)
                (module / "module.yaml").write_text(f"name: {name}\ndependencies: []\n")
                (module / "Dockerfile").write_text("FROM scratch\n")
            baseline.git(root, "init", "-b", "main")
            baseline.git(root, "config", "user.email", "test@example.invalid")
            baseline.git(root, "config", "user.name", "Test")
            baseline.git(root, "add", ".")
            baseline.git(root, "commit", "-m", "published api")
            published = baseline.git(root, "rev-parse", "HEAD")
            (root / "modules" / "other" / "Dockerfile").write_text("FROM busybox\n")
            baseline.git(root, "add", ".")
            baseline.git(root, "commit", "-m", "change other only")
            source = baseline.git(root, "rev-parse", "HEAD")
            state = {
                "schemaVersion": 1,
                "reservations": [reservation(published)],
            }
            chosen = promotion.select_module_publication(root, state, source, "api")
            self.assertEqual(chosen["module"], "api")
            self.assertEqual(chosen["sourceCommit"], published)

    def test_module_selection_rejects_its_own_changed_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "solution.yaml").write_text("modules: [api]\n")
            module = root / "modules" / "api"
            module.mkdir(parents=True)
            (module / "module.yaml").write_text("name: api\ndependencies: []\n")
            (module / "Dockerfile").write_text("FROM scratch\n")
            baseline.git(root, "init", "-b", "main")
            baseline.git(root, "config", "user.email", "test@example.invalid")
            baseline.git(root, "config", "user.name", "Test")
            baseline.git(root, "add", ".")
            baseline.git(root, "commit", "-m", "published")
            published = baseline.git(root, "rev-parse", "HEAD")
            (module / "Dockerfile").write_text("FROM busybox\n")
            baseline.git(root, "add", ".")
            baseline.git(root, "commit", "-m", "api changed")
            source = baseline.git(root, "rev-parse", "HEAD")
            state = {
                "schemaVersion": 1,
                "reservations": [reservation(published)],
            }
            with self.assertRaisesRegex(ValueError, "publication pending"):
                promotion.select_module_publication(root, state, source, "api")


class FrozenPromotionTests(unittest.TestCase):
    def test_existing_solution_baselines_are_eligible_and_latest_is_selected(self):
        earlier = solution_bundle("0.1.0-it2", "PI-2026.3")
        latest = solution_bundle("0.1.0-it1", "PI-2026.4", "c" * 40)
        selected = promotion.select_latest_baseline(
            [("release-baselines/old.json", earlier), ("release-baselines/live.json", latest)],
            "solution", "0.1.0",
        )
        self.assertEqual(selected["path"], "release-baselines/live.json")
        self.assertEqual(selected["sourceCommit"], "c" * 40)

    def test_first_live_baseline_versions_remain_eligible(self):
        existing = solution_bundle(
            "0.1.0-it1", "PI-2026.4", SOURCE, module_version="0.0.1-it1",
        )
        solution = promotion.select_latest_baseline(
            [("release-baselines/solution-0.1.0-it1.json", existing)],
            "solution", "0.1.0",
        )
        module = promotion.select_latest_baseline(
            [("release-baselines/solution-0.1.0-it1.json", existing)],
            "module", "0.0.1", "api",
        )
        self.assertEqual(solution["version"], "0.1.0-it1")
        self.assertEqual(module["version"], "0.0.1-it1")

    def test_iteration_retry_reuses_run_frozen_source_after_main_advances(self):
        source = "c" * 40
        bundle = solution_bundle(source=source)
        bundle["solution"]["qa"]["runForRecord"] = (
            "https://github.com/example/platform/actions/runs/789"
        )
        env = {
            "GITHUB_SHA": source,
            "GITHUB_RUN_ID": "789",
            "GITHUB_REPOSITORY": "example/platform",
        }
        with patch.dict("os.environ", env), \
                patch.object(promotion, "list_records", side_effect=[
                    [], [("release-baselines/solution-0.1.0-it1.json", bundle)],
                ]), \
                patch.object(promotion, "_catalog_target", return_value=("0.0.0", "0.1.0", "")), \
                patch.object(promotion.baseline, "git", return_value=source), \
                patch.object(promotion.baseline, "ancestor", return_value=True), \
                patch.object(promotion, "validate_catalog", return_value=[]), \
                patch.object(promotion.baseline, "require_build") as require_build, \
                patch.object(promotion.baseline, "verify_artifacts"), \
                patch.object(
                    promotion.baseline, "select_publications",
                    side_effect=AssertionError("retry must use frozen source"),
                ), \
                patch.object(
                    promotion.baseline, "build_bundle",
                    side_effect=AssertionError("retry must not recalculate"),
                ):
            api = MagicMock()
            _, release = promotion._release_record(
                api, "solution", "", "patch", "iteration",
                Path("."), "789", "example/platform",
            )
        require_build.assert_called_once_with(api, "example/platform", source)
        self.assertEqual(release["sourceCommit"], source)
        self.assertEqual(release["manifest"]["solution"]["sourceCommit"], source)

    def test_conflicting_baselines_at_same_iteration_are_ambiguous(self):
        first = solution_bundle()
        second = solution_bundle(source="c" * 40)
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            promotion.select_latest_baseline(
                [("release-baselines/one.json", first), ("release-baselines/two.json", second)],
                "solution", "0.1.0",
            )

    def test_rc_counter_increments_and_existing_workflow_run_is_idempotent(self):
        rc1 = {
            "releaseChoice": "solution", "module": "", "promote": "release_candidate",
            "targetVersion": "0.1.0", "version": "0.1.0-rc1",
        }
        records = [("release-promotions/solution-0.1.0-rc1.json", rc1)]
        self.assertEqual(promotion._next_rc(records, "solution", "", "0.1.0"), 2)
        prior = dict(rc1, requestRunId="456")
        self.assertEqual(
            promotion._existing_request(
                [("release-promotions/solution-0.1.0-rc1.json", prior)],
                "456", "solution", "", "release_candidate",
            ),
            ("release-promotions/solution-0.1.0-rc1.json", prior),
        )

    def test_stable_selection_uses_highest_eligible_rc_and_rejects_ties(self):
        rc1 = {
            "releaseChoice": "module", "module": "api", "promote": "release_candidate",
            "targetVersion": "0.0.1", "version": "0.0.1-rc1",
        }
        rc2 = dict(rc1, version="0.0.1-rc2")
        selected = promotion._latest_rc(
            [("rc1.json", rc1), ("rc2.json", rc2)], "module", "api", "0.0.1",
        )
        self.assertEqual(selected, ("rc2.json", rc2))
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            promotion._latest_rc(
                [("rc2.json", rc2), ("duplicate.json", rc2)], "module", "api", "0.0.1",
            )

    def test_rc_creation_freezes_incremented_counter_and_replay_reuses_it(self):
        rc1 = {
            "releaseChoice": "solution", "module": "", "promote": "release_candidate",
            "targetVersion": "0.1.0", "version": "0.1.0-rc1",
        }
        selection = {
            "path": "release-baselines/solution-0.1.0-it1.json",
            "manifest": solution_bundle()["solution"],
            "version": "0.1.0-it1", "pi": "PI-2026.4", "sourceCommit": SOURCE,
        }
        env = {
            "GITHUB_REPOSITORY": "example/platform", "GITHUB_RUN_ID": "789",
            "GITHUB_REF": "refs/heads/main",
        }
        with patch.dict("os.environ", env), \
                patch.object(promotion, "list_records", return_value=[
                    ("release-promotions/solution-0.1.0-rc1.json", rc1),
                ]), \
                patch.object(promotion, "_catalog_target", return_value=("0.0.0", "0.1.0", "")), \
                patch.object(promotion, "select_latest_baseline", return_value=selection), \
                patch.object(promotion, "_verify_manifest_artifacts"), \
                patch.object(promotion, "baseline") as baseline_mock:
            baseline_mock.schema = MagicMock()
            baseline_mock.freeze.side_effect = lambda api, path, record: record
            api = MagicMock()
            path, record = promotion._release_record(
                api, "solution", "", "minor", "release_candidate",
                Path("."), "789", "example/platform",
            )
        self.assertEqual(path, "release-promotions/solution-0.1.0-rc2.json")
        self.assertEqual(record["version"], "0.1.0-rc2")
        self.assertEqual(record["sourceBaseline"], selection["path"])

        with patch.object(promotion, "list_records", return_value=[(path, record)]), \
                patch.object(promotion, "_catalog_target", side_effect=AssertionError("must reuse record")), \
                patch.object(promotion, "select_latest_baseline", side_effect=AssertionError("must reuse record")):
            path2, replayed = promotion._release_record(
                api, "solution", "", "minor", "release_candidate",
                Path("."), "789", "example/platform",
            )
        self.assertEqual((path2, replayed), (path, record))

    def test_solution_iteration_dispatch_releases_every_frozen_module_and_solution(self):
        bundle = solution_bundle()
        record = {
            "releaseChoice": "solution", "manifest": bundle["solution"],
            "version": "0.1.0-it1",
        }
        with tempfile.TemporaryDirectory() as directory:
            env = {
                "GITHUB_REPOSITORY": "example/platform", "GITHUB_RUN_ID": "321",
                "GITHUB_REF": "refs/heads/main", "GH_TOKEN": "synthetic",
                "RELEASE_CHOICE": "solution", "RELEASE_MODULE": "",
                "RELEASE_TYPE": "minor", "RELEASE_PROMOTE": "iteration",
                "SOURCE_ROOT": directory,
            }
            with patch.dict("os.environ", env), \
                    patch.object(promotion, "GitHubAPI") as api_mock, \
                    patch.object(promotion, "validate_catalog", return_value=[]), \
                    patch.object(
                        promotion, "_release_record",
                        return_value=("release-baselines/solution-0.1.0-it1.json", record),
                    ), \
                    patch.object(promotion, "_read_record", return_value=bundle), \
                    patch.object(promotion.baseline, "verify_artifacts"), \
                    patch.object(promotion, "_publish_iteration_solution") as publish, \
                    patch.object(promotion, "_save_bundle_output") as save:
                promotion.run_release()
        publish.assert_called_once_with(api_mock.return_value, bundle)
        save.assert_called_once_with(bundle)

    def test_stable_release_gate_runs_before_release_record_or_writes(self):
        manifest = solution_bundle()["solution"]
        record = {
            "releaseChoice": "solution", "module": "", "promote": "stable",
            "version": "0.1.0", "targetVersion": "0.1.0",
            "sourceCommit": SOURCE, "manifest": manifest,
        }
        api = MagicMock()
        api.request.return_value = {
            "name": "release-stable",
            "protection_rules": [{
                "type": "required_reviewers",
                "reviewers": [{"type": "Team", "reviewer": {"slug": "release-reviewers"}}],
            }],
        }
        event_order = []

        def release_record(*args):
            event_order.append("release_record")
            return "release-promotions/solution-0.1.0-stable.json", record

        with tempfile.TemporaryDirectory() as directory:
            env = {
                "GITHUB_REPOSITORY": "example/platform", "GITHUB_RUN_ID": "901",
                "GITHUB_REF": "refs/heads/main", "GH_TOKEN": "synthetic",
                "RELEASE_CHOICE": "solution", "RELEASE_MODULE": "",
                "RELEASE_TYPE": "patch", "RELEASE_PROMOTE": "stable",
                "SOURCE_ROOT": directory,
            }
            with patch.dict("os.environ", env), \
                    patch.object(promotion, "GitHubAPI", return_value=api), \
                    patch.object(promotion, "validate_catalog", return_value=[]), \
                    patch.object(promotion, "_release_record", side_effect=release_record), \
                    patch.object(promotion, "_validate_promoted_record"), \
                    patch.object(promotion.baseline, "freeze") as freeze, \
                    patch.object(promotion, "_verify_manifest_artifacts"), \
                    patch.object(promotion, "_publish_record"), \
                    patch.object(promotion, "_save_output"):
                promotion.run_release()
        self.assertEqual(api.request.call_args_list[0].args, ("GET", "/environments/release-stable"))
        self.assertEqual(event_order, ["release_record"])
        freeze.assert_called_once()

    def test_stable_release_gate_failure_prevents_release_record_creation(self):
        api = MagicMock()
        api.request.return_value = {"name": "release-stable", "protection_rules": []}
        with tempfile.TemporaryDirectory() as directory:
            env = {
                "GITHUB_REPOSITORY": "example/platform", "GITHUB_RUN_ID": "901",
                "GITHUB_REF": "refs/heads/main", "GH_TOKEN": "synthetic",
                "RELEASE_CHOICE": "solution", "RELEASE_MODULE": "",
                "RELEASE_TYPE": "patch", "RELEASE_PROMOTE": "stable",
                "SOURCE_ROOT": directory,
            }
            with patch.dict("os.environ", env), \
                    patch.object(promotion, "GitHubAPI", return_value=api), \
                    patch.object(promotion, "_release_record") as release_record:
                with self.assertRaisesRegex(ValueError, "required-reviewers"):
                    promotion.run_release()
        release_record.assert_not_called()


class StableCatalogTests(unittest.TestCase):
    class FakeAPI:
        def __init__(self):
            self.main_text = (
                "schemaVersion: 1\nsolution:\n  name: platform\n  version: 0.0.0\n"
                "pi:\n  id: PI-2026.4\n"
            )
            self.branch_text = None
            self.branch_sha = None
            self.pr = None
            self.main_sha = "d" * 40
            self.extra_files = False
            self.calls = []
            self.environment = {
                "name": "release-stable",
                "protection_rules": [{
                    "type": "required_reviewers",
                    "reviewers": [{"type": "User", "reviewer": {"login": "release-owner"}}],
                }],
            }

        def request(self, method, path, data=None):
            self.calls.append((method, path))
            if method == "GET" and path == "/environments/release-stable":
                return self.environment
            if method == "GET" and path.startswith("/contents/solution.yaml?"):
                ref = path.split("ref=", 1)[1]
                text = self.main_text if ref == "main" else self.branch_text
                if text is None:
                    raise urllib.error.HTTPError("https://example.test", 404, "missing", {}, io.BytesIO())
                return {
                    "content": base64.b64encode(text.encode()).decode(),
                    "sha": "contents-sha",
                }
            if method == "GET" and path == "/git/ref/heads/main":
                return {"object": {"sha": self.main_sha}}
            if method == "GET" and path.startswith("/git/ref/heads/automation/"):
                if self.branch_sha is None:
                    raise urllib.error.HTTPError("https://example.test", 404, "missing", {}, io.BytesIO())
                return {"object": {"sha": self.branch_sha}}
            if method == "POST" and path == "/git/refs":
                self.branch_sha = self.main_sha
                self.branch_text = self.main_text
                return {}
            if method == "PUT" and path == "/contents/solution.yaml":
                self.branch_text = base64.b64decode(data["content"]).decode()
                self.branch_sha = "e" * 40
                return {}
            if method == "GET" and path.startswith("/compare/"):
                files = [{
                    "filename": "solution.yaml", "additions": 1, "deletions": 1,
                }]
                if self.extra_files:
                    files.append({"filename": "README.md", "additions": 1, "deletions": 1})
                return {"files": files}
            if method == "GET" and path.startswith("/pulls?"):
                return [self.pr] if self.pr else []
            if method == "POST" and path == "/pulls":
                self.pr = {
                    "number": 7, "title": data["title"], "body": data["body"],
                    "base": {"ref": "main", "sha": self.main_sha},
                }
                return self.pr
            if method == "GET" and path == "/pulls/7/files?per_page=100":
                return [{"filename": "solution.yaml", "additions": 1, "deletions": 1}]
            raise AssertionError(f"unexpected API call: {method} {path}")

    def stable_record(self):
        return {
            "promote": "stable", "requestRunId": "900", "releaseChoice": "solution",
            "module": "", "targetVersion": "0.0.1", "baseVersion": "0.0.0",
            "sourceCommit": SOURCE,
        }

    def test_stable_pr_changes_only_version_and_is_rerunnable(self):
        api = self.FakeAPI()
        env = {
            "GITHUB_REPOSITORY": "example/platform", "GITHUB_RUN_ID": "900",
            "GITHUB_REF": "refs/heads/main", "GH_TOKEN": "synthetic",
        }
        with patch.dict("os.environ", env), \
                patch.object(promotion, "list_records", return_value=[
                    ("release-promotions/solution-0.0.1-stable.json", self.stable_record()),
                ]), patch.object(promotion, "GitHubAPI", return_value=api):
            promotion.create_stable_catalog_pr()
            promotion.create_stable_catalog_pr()
        self.assertEqual(api.calls[0], ("GET", "/environments/release-stable"))
        first_write = next(index for index, call in enumerate(api.calls) if call[0] in {"POST", "PUT"})
        self.assertLess(api.calls.index(("GET", "/environments/release-stable")), first_write)
        self.assertEqual(
            api.branch_text,
            api.main_text.replace("  version: 0.0.0", "  version: 0.0.1"),
        )
        self.assertEqual(api.main_text, (
            "schemaVersion: 1\nsolution:\n  name: platform\n  version: 0.0.0\n"
            "pi:\n  id: PI-2026.4\n"
        ))
        self.assertEqual(api.pr["number"], 7)

    def test_stable_pr_rejects_catalog_conflict_without_branch_or_pr(self):
        api = self.FakeAPI()
        api.main_text = api.main_text.replace("version: 0.0.0", "version: 0.2.0")
        env = {
            "GITHUB_REPOSITORY": "example/platform", "GITHUB_RUN_ID": "900",
            "GITHUB_REF": "refs/heads/main", "GH_TOKEN": "synthetic",
        }
        with patch.dict("os.environ", env), \
                patch.object(promotion, "list_records", return_value=[
                    ("release-promotions/solution-0.0.1-stable.json", self.stable_record()),
                ]), patch.object(promotion, "GitHubAPI", return_value=api):
            with self.assertRaisesRegex(ValueError, "catalog conflict"):
                promotion.create_stable_catalog_pr()
        self.assertIsNone(api.branch_sha)
        self.assertIsNone(api.pr)

    def test_stable_pr_gate_rejects_missing_reviewers_before_any_writes(self):
        api = self.FakeAPI()
        api.environment = {"name": "release-stable", "protection_rules": []}
        env = {
            "GITHUB_REPOSITORY": "example/platform", "GITHUB_RUN_ID": "900",
            "GITHUB_REF": "refs/heads/main", "GH_TOKEN": "synthetic",
        }
        with patch.dict("os.environ", env), \
                patch.object(promotion, "list_records", side_effect=AssertionError("must gate first")), \
                patch.object(promotion, "GitHubAPI", return_value=api):
            with self.assertRaisesRegex(ValueError, "required-reviewers"):
                promotion.create_stable_catalog_pr()
        self.assertEqual(api.calls, [("GET", "/environments/release-stable")])
        self.assertIsNone(api.branch_sha)
        self.assertIsNone(api.pr)

    def test_stable_pr_rejects_branch_with_additional_files_before_opening_pr(self):
        api = self.FakeAPI()
        api.extra_files = True
        env = {
            "GITHUB_REPOSITORY": "example/platform", "GITHUB_RUN_ID": "900",
            "GITHUB_REF": "refs/heads/main", "GH_TOKEN": "synthetic",
        }
        with patch.dict("os.environ", env), \
                patch.object(promotion, "list_records", return_value=[
                    ("release-promotions/solution-0.0.1-stable.json", self.stable_record()),
                ]), patch.object(promotion, "GitHubAPI", return_value=api):
            with self.assertRaisesRegex(ValueError, "beyond the exact version update"):
                promotion.create_stable_catalog_pr()
        self.assertIsNone(api.pr)

    def test_stable_pr_body_preserves_template_and_truthfully_leaves_checklist_unchecked(self):
        template_path = baseline.ROOT / ".github" / "pull_request_template.md"
        template = template_path.read_text(encoding="utf-8")
        body = promotion.stable_catalog_pr_body(
            "solution", "", "1.2.0", "a" * 40, template_path,
        )
        self.assertIn("## What and why\n\nRecord stable version `1.2.0`", body)
        self.assertIn("## Stage / issue\n\nStage 5 release automation", body)
        self.assertIn("Frozen source: `" + "a" * 40 + "`", body)
        self.assertIn("`make ci` was not run on the generated catalog commit", body)
        self.assertLess(body.index("## What and why"), body.index("## Stage / issue"))
        self.assertLess(body.index("## Stage / issue"), body.index("## Checklist"))
        self.assertEqual(
            [line for line in body.splitlines() if line.startswith("- [ ]")],
            [line for line in template.splitlines() if line.startswith("- [ ]")],
        )
        self.assertNotIn("- [x] make ci passes", body)

    def test_rc_and_stable_manifests_keep_source_from_frozen_predecessor_after_main_moves(self):
        frozen_source = "a" * 40
        dispatched_main = "c" * 40
        baseline_manifest = solution_bundle(source=frozen_source)["solution"]
        selection = {
            "path": "release-baselines/solution-0.1.0-it1.json",
            "manifest": baseline_manifest,
            "bundle": solution_bundle(source=frozen_source),
            "version": "0.1.0-it1",
            "pi": "PI-2026.4",
            "sourceCommit": frozen_source,
        }
        environment = {
            "GITHUB_SHA": dispatched_main,
            "GITHUB_RUN_ID": "901",
            "GITHUB_REPOSITORY": "example/platform",
        }
        with patch.dict("os.environ", environment), \
                patch.object(promotion, "list_records", side_effect=[[], []]), \
                patch.object(promotion, "_catalog_target", return_value=("0.0.0", "0.1.0", "")), \
                patch.object(promotion, "select_latest_baseline", return_value=selection), \
                patch.object(promotion, "_verify_manifest_artifacts"), \
                patch.object(promotion.baseline, "verify_artifacts"), \
                patch.object(promotion.baseline, "schema"), \
                patch.object(promotion.baseline, "freeze", side_effect=lambda api, path, record: record):
            _, rc = promotion._release_record(
                MagicMock(), "solution", "", "patch", "release_candidate",
                Path("."), "901", "example/platform",
            )
        self.assertEqual(rc["sourceCommit"], frozen_source)
        self.assertEqual(rc["manifest"]["solution"]["sourceCommit"], frozen_source)
        self.assertEqual(rc["manifest"]["solution"]["version"], "0.1.0-rc1")

        with patch.dict("os.environ", environment), \
                patch.object(promotion, "list_records", return_value=[
                    ("release-promotions/solution-0.1.0-rc1.json", rc),
                ]), \
                patch.object(promotion, "_catalog_target", return_value=("0.0.0", "0.1.0", "")), \
                patch.object(promotion, "_verify_manifest_artifacts"), \
                patch.object(promotion.baseline, "verify_artifacts"), \
                patch.object(promotion.baseline, "schema"), \
                patch.object(promotion.baseline, "freeze", side_effect=lambda api, path, record: record):
            _, stable = promotion._release_record(
                MagicMock(), "solution", "", "patch", "stable",
                Path("."), "902", "example/platform",
            )
        self.assertEqual(stable["sourceCommit"], frozen_source)
        self.assertEqual(stable["manifest"]["solution"]["sourceCommit"], frozen_source)
        self.assertEqual(stable["manifest"]["solution"]["version"], "0.1.0")
        self.assertNotEqual(stable["manifest"]["solution"]["sourceCommit"], dispatched_main)

    def test_catalog_rewrite_preserves_everything_but_one_version_value(self):
        source = "schemaVersion: 1\nname: api\nversion: 0.0.0\nowners: [test]\n"
        self.assertEqual(
            promotion._replace_stable_version(source, "module", "0.0.0", "0.0.1"),
            "schemaVersion: 1\nname: api\nversion: 0.0.1\nowners: [test]\n",
        )
        with self.assertRaisesRegex(ValueError, "exactly one"):
            promotion._replace_stable_version(source + "version: 0.0.0\n", "module", "0.0.0", "0.0.1")


if __name__ == "__main__":
    unittest.main()
