import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import yaml

from scripts.module_catalog import ROOT
from scripts.publish_modules import chart_metadata, publish, publish_chart
from scripts.release_state import record_publication, reserve
from tests.unit.test_release_state import MemoryState, context

IMAGE = "sha256:" + "a" * 64
CHART = "sha256:" + "b" * 64
VERSION = "0.0.1-it1-rv.1"
REPOSITORY = "ghcr.io/example/payment-api"
CHART_REPOSITORY = "ghcr.io/example/charts/payment-api"
CONTEXT = context(module="payment-api", chartRegistry="oci://ghcr.io/example/charts")
RESERVATION = {**CONTEXT, "counter": 1, "reservedVersion": VERSION}
VALUES = {"repository": REPOSITORY, "tag": VERSION, "digest": IMAGE, "pullPolicy": "IfNotPresent"}
METADATA = {
    "name": "payment-api", "version": VERSION,
    "annotations": {
        "org.opencontainers.image.revision": CONTEXT["sourceCommit"],
        "org.opencontainers.image.ref.name": CONTEXT["branch"],
    },
}


class ChartPublicationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.source = Path(self.directory.name)
        self.chart = self.source / "modules" / "payment-api" / "chart"
        shutil.copytree(ROOT / "modules" / "payment-api" / "chart", self.chart)
        self.image_manifest = (IMAGE, {"annotations": {
            "org.opencontainers.image.revision": CONTEXT["sourceCommit"],
        }})
        self.environment = patch.dict(os.environ, {
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_REPOSITORY": "example/repo",
            "GITHUB_RUN_ID": "123",
            "SOURCE_RUN_URL": "https://github.com/example/repo/actions/runs/122",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def command(self, args, **kwargs):
        if args[:2] == ["helm", "lint"]:
            staged = Path(args[2])
            metadata = yaml.safe_load((staged / "Chart.yaml").read_text())
            self.assertEqual(metadata["version"], VERSION)
            self.assertEqual(metadata["appVersion"], VERSION)
            self.assertEqual(metadata["annotations"], METADATA["annotations"])
            self.assertEqual(yaml.safe_load((staged / "values.yaml").read_text())["image"], VALUES)
        if args[:2] == ["helm", "template"]:
            return subprocess.CompletedProcess(args, 0, stdout=yaml.safe_dump({
                "kind": "Deployment", "spec": {"template": {"spec": {"containers": [
                    {"image": f"{REPOSITORY}@{IMAGE}"},
                ]}}},
            }), stderr="")
        if args[:2] == ["helm", "push"]:
            self.assertTrue(args[2].endswith(f"payment-api-{VERSION}.tgz"))
            self.assertEqual(args[3], "oci://ghcr.io/example/charts")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr=f"Digest: {CHART}\n")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    def test_package_uses_versioned_chart_and_digest_pinned_image_without_source_edits(self):
        originals = {path: path.read_bytes() for path in self.chart.rglob("*") if path.is_file()}
        with (
            patch("scripts.publish_modules.registry_manifest", side_effect=[None, (CHART, {})]),
            patch("scripts.publish_modules.subprocess.run", side_effect=self.command) as run,
        ):
            chart = publish_chart(self.source, CONTEXT, RESERVATION, VERSION, REPOSITORY, IMAGE)
        self.assertEqual(chart, {"repository": CHART_REPOSITORY, "version": VERSION, "digest": CHART})
        self.assertEqual(
            [call.args[0][:2] for call in run.call_args_list],
            [["helm", "lint"], ["helm", "template"], ["helm", "package"], ["helm", "push"]],
        )
        for path, content in originals.items():
            self.assertEqual(path.read_bytes(), content)

    def test_real_helm_packages_both_application_charts_and_metadata_is_read_by_digest(self):
        real_run = subprocess.run
        for name in ("payment-api", "web-portal"):
            with self.subTest(module=name):
                chart = self.source / "modules" / name / "chart"
                if not chart.exists():
                    shutil.copytree(ROOT / "modules" / name / "chart", chart)
                archive = []

                def command(args, **kwargs):
                    if args[:2] == ["helm", "push"]:
                        archive.append(Path(args[2]).read_bytes())
                        return subprocess.CompletedProcess(args, 0, stdout=f"Digest: {CHART}\n", stderr="")
                    if args[:2] == ["helm", "pull"]:
                        self.assertEqual(args[2], f"oci://ghcr.io/example/charts/{name}@{CHART}")
                        (Path(args[-1]) / f"{name}-{VERSION}.tgz").write_bytes(archive[0])
                        return subprocess.CompletedProcess(args, 0)
                    return real_run(args, **kwargs)

                repository = f"ghcr.io/example/{name}"
                with (
                    patch("scripts.publish_modules.registry_manifest", side_effect=[None, (CHART, {})]),
                    patch("scripts.publish_modules.subprocess.run", side_effect=command),
                ):
                    result = publish_chart(
                        self.source, {**CONTEXT, "module": name}, RESERVATION, VERSION, repository, IMAGE,
                    )
                    metadata, values = chart_metadata(result["repository"], result["digest"])
                self.assertEqual(metadata["name"], name)
                self.assertEqual(metadata["version"], VERSION)
                self.assertEqual(metadata["appVersion"], VERSION)
                self.assertEqual(values["image"], {**VALUES, "repository": repository})

    def test_missing_chart_is_optional_unless_already_recorded(self):
        context_without_chart = {**CONTEXT, "module": "event-contracts"}
        with patch("scripts.publish_modules.registry_manifest") as registry:
            self.assertIsNone(publish_chart(
                self.source, context_without_chart, RESERVATION, VERSION, REPOSITORY, IMAGE,
            ))
            with self.assertRaisesRegex(ValueError, "recorded chart is missing"):
                publish_chart(
                    self.source, context_without_chart, {**RESERVATION, "publication": {"chart": {"digest": CHART}}},
                    VERSION, REPOSITORY, IMAGE,
                )
            registry.assert_not_called()

    def test_chart_identity_and_reserved_registry_are_validated(self):
        with self.assertRaisesRegex(ValueError, "reserved explicit OCI GHCR"):
            publish_chart(self.source, CONTEXT, {**RESERVATION, "chartRegistry": None}, VERSION, REPOSITORY, IMAGE)
        (self.chart / "Chart.yaml").write_text("name: different\nversion: 0.1.0\n")
        with patch("scripts.publish_modules.registry_manifest", return_value=None):
            with self.assertRaisesRegex(ValueError, "chart name must match"):
                publish_chart(self.source, CONTEXT, RESERVATION, VERSION, REPOSITORY, IMAGE)

    def test_completed_legacy_image_only_reservation_remains_idempotent(self):
        legacy = {key: value for key, value in RESERVATION.items() if key != "chartRegistry"}
        legacy["publication"] = {"digest": IMAGE}
        with patch("scripts.publish_modules.registry_manifest") as registry:
            self.assertIsNone(publish_chart(self.source, CONTEXT, legacy, VERSION, REPOSITORY, IMAGE))
            registry.assert_not_called()

    def test_same_branch_new_source_can_replace_chart_but_not_another_branch(self):
        branch_context = {**CONTEXT, "integrated": False}
        metadata = copy.deepcopy(METADATA)
        metadata["annotations"]["org.opencontainers.image.revision"] = "c" * 40
        with (
            patch("scripts.publish_modules.registry_manifest", side_effect=[(CHART, {}), (CHART, {})]),
            patch("scripts.publish_modules.chart_metadata", return_value=(metadata, {"image": VALUES})),
            patch("scripts.publish_modules.subprocess.run", side_effect=self.command) as run,
        ):
            result = publish_chart(self.source, branch_context, branch_context, VERSION, REPOSITORY, IMAGE)
            self.assertEqual(result["digest"], CHART)
            self.assertIn(["helm", "push"], [call.args[0][:2] for call in run.call_args_list])
        metadata["annotations"]["org.opencontainers.image.ref.name"] = "feature/other"
        with (
            patch("scripts.publish_modules.registry_manifest", return_value=(CHART, {})),
            patch("scripts.publish_modules.chart_metadata", return_value=(metadata, {"image": VALUES})),
        ):
            with self.assertRaisesRegex(ValueError, "collision"):
                publish_chart(self.source, branch_context, branch_context, VERSION, REPOSITORY, IMAGE)

    def test_partial_image_success_then_chart_retry_reuses_image(self):
        with (
            patch("scripts.publish_modules.github_store"),
            patch("scripts.publish_modules.reserve", return_value=RESERVATION),
            patch("scripts.publish_modules.record_publication") as record,
            patch("scripts.publish_modules.registry_manifest", side_effect=[
                self.image_manifest, None, self.image_manifest, None, (CHART, {}),
            ]),
            patch("scripts.publish_modules.subprocess.run", side_effect=[
                subprocess.CalledProcessError(1, ["helm", "lint"]),
            ]) as run,
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                publish(self.source, CONTEXT, self.source / "output")
            record.assert_not_called()
            run.side_effect = self.command
            publication = publish(self.source, CONTEXT, self.source / "output")
            self.assertEqual(publication["digest"], IMAGE)
            self.assertEqual(publication["chart"]["digest"], CHART)
            self.assertTrue(all(call.args[0][0] == "helm" for call in run.call_args_list))
            record.assert_called_once()

    def test_existing_image_and_chart_are_reused_by_digest(self):
        with (
            patch("scripts.publish_modules.registry_manifest", return_value=(CHART, {})),
            patch("scripts.publish_modules.chart_metadata", return_value=(METADATA, {"image": VALUES})),
            patch("scripts.publish_modules.subprocess.run") as run,
        ):
            result = publish_chart(self.source, CONTEXT, RESERVATION, VERSION, REPOSITORY, IMAGE)
            self.assertEqual(result["digest"], CHART)
            run.assert_not_called()

    def test_existing_chart_rejects_wrong_image_source_or_branch(self):
        for changed in (
            {"image": {**VALUES, "digest": "sha256:" + "c" * 64}},
            {"source": "c" * 40},
            {"branch": "feature/other"},
        ):
            with self.subTest(changed=changed):
                metadata = copy.deepcopy(METADATA)
                metadata["annotations"].update({
                    "org.opencontainers.image.revision": changed.get("source", CONTEXT["sourceCommit"]),
                    "org.opencontainers.image.ref.name": changed.get("branch", CONTEXT["branch"]),
                })
                with (
                    patch("scripts.publish_modules.registry_manifest", return_value=(CHART, {})),
                    patch("scripts.publish_modules.chart_metadata", return_value=(metadata, {"image": changed.get("image", VALUES)})),
                ):
                    with self.assertRaises(ValueError):
                        publish_chart(self.source, CONTEXT, RESERVATION, VERSION, REPOSITORY, IMAGE)

    def test_saved_chart_cannot_disappear_or_change_digest(self):
        reservation = {**RESERVATION, "publication": {"chart": {
            "repository": CHART_REPOSITORY, "version": VERSION, "digest": CHART,
        }}}
        for existing in (None, ("sha256:" + "c" * 64, {})):
            with patch("scripts.publish_modules.registry_manifest", return_value=existing):
                with self.assertRaisesRegex(ValueError, "immutable chart"):
                    publish_chart(self.source, CONTEXT, reservation, VERSION, REPOSITORY, IMAGE)

    def test_mismatching_registry_digest_after_push_fails(self):
        with (
            patch("scripts.publish_modules.registry_manifest", side_effect=[None, ("sha256:" + "c" * 64, {})]),
            patch("scripts.publish_modules.subprocess.run", side_effect=self.command),
        ):
            with self.assertRaisesRegex(ValueError, "chart digest verification"):
                publish_chart(self.source, CONTEXT, RESERVATION, VERSION, REPOSITORY, IMAGE)

    def test_chart_registry_is_frozen_in_reservation_and_chart_evidence_immutable(self):
        store = MemoryState()
        reservation = reserve(store, CONTEXT)
        retry = reserve(store, {**CONTEXT, "chartRegistry": "oci://ghcr.io/other/charts"})
        self.assertEqual(retry["chartRegistry"], CONTEXT["chartRegistry"])
        publication = {
            "module": "payment-api", "version": VERSION, "sourceCommit": CONTEXT["sourceCommit"],
            "repository": REPOSITORY, "digest": IMAGE, "workflowRun": "run", "testRun": "tests",
            "sbom": "sbom", "provenance": "provenance",
            "chart": {"repository": CHART_REPOSITORY, "version": VERSION, "digest": CHART},
        }
        record_publication(store, reservation, publication)
        record_publication(store, reservation, publication)
        with self.assertRaisesRegex(ValueError, "immutable chart"):
            record_publication(store, reservation, {**publication, "chart": {
                **publication["chart"], "digest": "sha256:" + "c" * 64,
            }})
