import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.module_catalog import ROOT
from scripts.publish_modules import publication_matrix, publish

DIGEST = "sha256:" + "d" * 64
CONTEXT = {
    "module": "event-contracts", "targetVersion": "0.0.1", "iteration": 1,
    "releaseLine": "main", "sourceCommit": "a" * 40, "branch": "main",
    "integrated": True, "approvedImpact": "patch", "registry": "ghcr.io/example",
}
ENV = {
    "GITHUB_SERVER_URL": "https://github.com", "GITHUB_REPOSITORY": "example/repo",
    "GITHUB_RUN_ID": "123", "SOURCE_RUN_URL": "https://github.com/example/repo/actions/runs/122",
    "GH_TOKEN": "test", "GITHUB_ACTOR": "example",
}


class PublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = patch.dict(os.environ, ENV)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_existing_revision_recovers_without_rebuild(self) -> None:
        reservation = {**CONTEXT, "reservedVersion": "0.0.1-it1-rv.1", "counter": 1}
        manifest = {"annotations": {"org.opencontainers.image.revision": "a" * 40}}
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch("scripts.publish_modules.github_store"),
                patch("scripts.publish_modules.reserve", return_value=reservation),
                patch("scripts.publish_modules.record_publication") as record,
                patch("scripts.publish_modules.registry_manifest", return_value=(DIGEST, manifest)),
                patch("scripts.publish_modules.subprocess.run") as build,
            ):
                result = publish(ROOT, CONTEXT, Path(directory))
                build.assert_not_called()
                self.assertEqual(result["digest"], DIGEST)
                self.assertEqual(result["version"], reservation["reservedVersion"])
                record.assert_called_once()
                self.assertEqual(json.loads((Path(directory) / "event-contracts.json").read_text()), result)

    def test_changed_or_missing_immutable_tag_fails(self) -> None:
        reservation = {
            **CONTEXT, "reservedVersion": "0.0.1-it1-rv.1", "counter": 1,
            "publication": {"digest": DIGEST},
        }
        for existing in [
            None,
            ("sha256:" + "c" * 64, {"annotations": {"org.opencontainers.image.revision": "a" * 40}}),
            (DIGEST, {"annotations": {"org.opencontainers.image.revision": "b" * 40}}),
        ]:
            with self.subTest(existing=existing):
                with (
                    patch("scripts.publish_modules.github_store"),
                    patch("scripts.publish_modules.reserve", return_value=reservation),
                    patch("scripts.publish_modules.registry_manifest", return_value=existing),
                    patch("scripts.publish_modules.subprocess.run") as build,
                ):
                    with self.assertRaisesRegex(ValueError, "immutable"):
                        publish(ROOT, CONTEXT, ROOT / "unused")
                    build.assert_not_called()

    def test_new_publication_verifies_digest_and_records_evidence(self) -> None:
        reservation = {**CONTEXT, "reservedVersion": "0.0.1-it1-rv.1", "counter": 1}

        def build(args: list[str], **_kwargs: object) -> None:
            metadata = Path(args[args.index("--metadata-file") + 1])
            metadata.write_text(json.dumps({"containerimage.digest": DIGEST}))
            self.assertIn("--sbom=true", args)
            self.assertIn("--provenance=mode=max", args)

        with tempfile.TemporaryDirectory() as directory:
            with (
                patch("scripts.publish_modules.github_store"),
                patch("scripts.publish_modules.reserve", return_value=reservation),
                patch("scripts.publish_modules.record_publication") as record,
                patch("scripts.publish_modules.registry_manifest", side_effect=[None, (DIGEST, {})]),
                patch("scripts.publish_modules.subprocess.run", side_effect=build),
            ):
                result = publish(ROOT, CONTEXT, Path(directory))
                self.assertEqual(result["sbom"], f"ghcr.io/example/event-contracts@{DIGEST}")
                self.assertEqual(result["testRun"], ENV["SOURCE_RUN_URL"])
                record.assert_called_once()

    def test_branch_hash_collision_and_stale_build_fail(self) -> None:
        context = {**CONTEXT, "branch": "feature/contracts", "integrated": False}
        api = MagicMock()
        api.request.return_value = {"object": {"sha": context["sourceCommit"]}}
        with (
            patch("scripts.publish_modules.GitHubAPI", return_value=api),
            patch("scripts.publish_modules.registry_manifest", return_value=(DIGEST, {"annotations": {
                "org.opencontainers.image.ref.name": "feature/other",
            }})),
        ):
            with self.assertRaisesRegex(ValueError, "collision"):
                publish(ROOT, context, ROOT / "unused")
            api.request.return_value = {"object": {"sha": "b" * 40}}
            with self.assertRaisesRegex(ValueError, "stale"):
                publish(ROOT, context, ROOT / "unused")

    def test_matrix_packages_only_implemented_modules_and_obeys_pi(self) -> None:
        with (
            patch("scripts.publish_modules.plan_matrix", return_value={"include": [
                {"module": "event-contracts"}, {"module": "payment-api"},
            ]}),
            patch("scripts.publish_modules.classify_rc_branch", return_value=None),
        ):
            matrix = publication_matrix(
                ROOT, "base", "a" * 40, "feature/contracts", [],
                datetime(2026, 10, 26, tzinfo=timezone.utc),
            )
            self.assertEqual(len(matrix["include"]), 1)
            entry = matrix["include"][0]
            self.assertEqual(entry["targetVersion"], "0.0.1")
            self.assertFalse(entry["integrated"])
            with self.assertRaisesRegex(ValueError, "outside PI"):
                publication_matrix(
                    ROOT, "base", "a" * 40, "feature/contracts", [],
                    datetime(2026, 10, 21, tzinfo=timezone.utc),
                )

    def test_rc_context_freezes_target_and_rejects_cross_module_edits(self) -> None:
        git_result = MagicMock(stdout="modules/event-contracts/schemas/event.avsc\n")
        with (
            patch("scripts.publish_modules.plan_matrix", return_value={"include": [{"module": "event-contracts"}]}),
            patch("scripts.publish_modules.classify_rc_branch", return_value="release/event-contracts/0.1.0-rc2"),
            patch("scripts.publish_modules.subprocess.run", return_value=git_result),
        ):
            matrix = publication_matrix(
                ROOT, "base", "a" * 40, "hotfix/contracts", [],
                datetime(2026, 10, 21, tzinfo=timezone.utc),
            )
            self.assertEqual(matrix["include"][0]["targetVersion"], "0.1.0")
            self.assertEqual(matrix["include"][0]["releaseLine"], "rc2")
            self.assertIsNone(matrix["include"][0]["iteration"])
            git_result.stdout += "modules/payment-api/main.go\n"
            with self.assertRaisesRegex(ValueError, "only their own module"):
                publication_matrix(
                    ROOT, "base", "a" * 40, "hotfix/contracts", [],
                    datetime(2026, 10, 21, tzinfo=timezone.utc),
                )


if __name__ == "__main__":
    unittest.main()
