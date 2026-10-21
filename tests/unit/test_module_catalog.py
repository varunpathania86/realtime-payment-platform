import subprocess
import tempfile
import unittest
from pathlib import Path
from shutil import copytree

from scripts.module_catalog import (
    ROOT,
    _dependency_cycle_errors,
    _schema_errors,
    affected_modules,
    is_stable_version_only_change,
    run_module,
    validate_catalog,
)


def module_map(*entries: tuple[str, list[str]]) -> dict[str, tuple[Path, dict]]:
    return {
        name: (Path("modules") / name / "module.yaml", {"name": name, "dependencies": dependencies})
        for name, dependencies in entries
    }


class ModuleCatalogTests(unittest.TestCase):
    def test_current_catalog_validates(self) -> None:
        self.assertEqual(validate_catalog(), [])

    def test_module_schema_rejects_invalid_name_and_prerelease_catalog_version(self) -> None:
        errors = _schema_errors(
            {
                "schemaVersion": 1,
                "name": "Payment_API",
                "type": "api",
                "version": "0.2.2-rc1",
                "owners": ["team"],
                "dependencies": [],
            },
            "module.schema.json",
            "module",
        )
        self.assertTrue(any("name" in error for error in errors))
        self.assertTrue(any("version" in error for error in errors))

    def test_folder_and_catalog_mismatch_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            copytree(ROOT / "schemas", root / "schemas")
            (root / "modules" / "folder-name").mkdir(parents=True)
            (root / "solution.yaml").write_text(
                "schemaVersion: 1\n"
                "solution:\n  name: sample-solution\n  version: 0.0.0\n  owners: [team]\n"
                "pi:\n  id: PI-2026.4\n  startDate: '2026-10-26'\n  iterations: 6\n  iterationLengthDays: 14\n"
                "modules: [manifest-name]\n"
                "registries:\n  images: ghcr.io/<owner>\n  charts: oci://ghcr.io/<owner>/charts\n",
                encoding="utf-8",
            )
            (root / "modules" / "folder-name" / "module.yaml").write_text(
                "schemaVersion: 1\nname: manifest-name\ntype: library\nversion: 0.0.0\n"
                "owners: [team]\ndependencies: []\n",
                encoding="utf-8",
            )
            errors = validate_catalog(root)
            self.assertTrue(any("does not match folder" in error for error in errors))
            self.assertTrue(any("listed module" in error for error in errors))
            self.assertTrue(any("not listed in solution.yaml" in error for error in errors))

    def test_lifecycle_schemas_accept_valid_release_records(self) -> None:
        module_release = {
            "schemaVersion": 1,
            "kind": "module-release",
            "module": "payment-api",
            "version": "0.2.2-it1-rv.1",
            "source": {
                "repository": "https://github.com/example/repo",
                "commit": "a" * 40,
                "gitTag": "modules/payment-api/v0.2.2-it1-rv.1",
            },
            "workflow": {
                "runId": "123",
                "url": "https://github.com/example/repo/actions/runs/123",
            },
            "artifacts": {
                "images": [
                    {
                        "repository": "ghcr.io/example/payment-api",
                        "tag": "0.2.2-it1-rv.1",
                        "digest": "sha256:" + "b" * 64,
                    }
                ],
                "charts": [],
            },
            "evidence": {
                "tests": ["unit"],
                "sbom": "sbom.json",
                "provenance": "provenance.json",
                "approvedImpact": "patch",
            },
        }
        solution_release = {
            "schemaVersion": 1,
            "kind": "solution-release",
            "pi": {"id": "PI-2026.4"},
            "solution": {
                "version": "1.8.0-rc1",
                "sourceCommit": "a" * 40,
                "gitTag": "solution/v1.8.0-rc1",
            },
            "modules": [
                {
                    "name": "payment-api",
                    "version": "0.2.2-rc1",
                    "sourceCommit": "b" * 40,
                    "artifacts": {"images": [], "charts": []},
                    "workflowRun": "https://github.com/example/repo/actions/runs/123",
                    "approvedImpact": "patch",
                }
            ],
            "qa": {"evidence": ["smoke"], "runForRecord": "qa-record.json"},
        }
        release_state = {
            "schemaVersion": 1,
            "reservations": [
                {
                    "module": "payment-api",
                    "targetVersion": "0.2.2",
                    "iteration": 1,
                    "releaseLine": "main",
                    "sourceCommit": "a" * 40,
                    "reservedVersion": "0.2.2-it1-rv.1",
                    "counter": 1,
                    "branch": "main",
                    "integrated": True,
                    "registry": "ghcr.io/example",
                    "approvedImpact": "patch",
                }
            ],
        }
        self.assertEqual(_schema_errors(module_release, "module-release.schema.json", "module"), [])
        self.assertEqual(_schema_errors(solution_release, "solution-release.schema.json", "solution"), [])
        self.assertEqual(_schema_errors(release_state, "release-state.schema.json", "state"), [])
        release_state["reservations"][0]["releaseLine"] = "rc1"
        release_state["reservations"][0]["reservedVersion"] = "0.2.2-rc1-rv.1"
        self.assertEqual(_schema_errors(release_state, "release-state.schema.json", "state"), [])

    def test_release_schema_rejects_unlocked_version_forms(self) -> None:
        errors = _schema_errors(
            {
                "schemaVersion": 1,
                "kind": "module-release",
                "module": "payment-api",
                "version": "0.2.2-it1-br.deadbeef",
                "source": {
                    "repository": "https://github.com/example/repo",
                    "commit": "a" * 40,
                    "gitTag": "tag",
                },
                "workflow": {"runId": "1", "url": "https://example.invalid/run"},
                "artifacts": {"images": [], "charts": []},
                "evidence": {
                    "tests": [],
                    "sbom": "sbom",
                    "provenance": "attestation",
                    "approvedImpact": "patch",
                },
            },
            "module-release.schema.json",
            "module",
        )
        self.assertTrue(any("version" in error for error in errors))

    def test_changed_module_expands_to_transitive_dependents(self) -> None:
        modules = module_map(
            ("event-contracts", []),
            ("payment-api", ["event-contracts"]),
            ("web-portal", ["payment-api"]),
            ("unrelated", []),
        )
        self.assertEqual(
            affected_modules(["modules/event-contracts/module.yaml"], modules),
            ["event-contracts", "payment-api", "web-portal"],
        )

    def test_catalog_and_shared_validation_changes_select_all_modules(self) -> None:
        modules = module_map(("one", []), ("two", ["one"]))
        self.assertEqual(affected_modules(["solution.yaml"], modules), ["one", "two"])
        self.assertEqual(affected_modules(["schemas/module.schema.json"], modules), ["one", "two"])

    def test_unknown_changed_module_fails_explicitly(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown catalog module"):
            affected_modules(["modules/not-listed/source.go"], module_map(("known", [])))

    def test_dependency_cycles_are_reported(self) -> None:
        modules = module_map(("alpha", ["beta"]), ("beta", ["alpha"]))
        self.assertTrue(_dependency_cycle_errors(modules))

    def test_catalog_only_module_is_a_successful_noop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module = root / "modules" / "empty-module"
            module.mkdir(parents=True)
            (module / "module.yaml").write_text("name: empty-module\n", encoding="utf-8")
            self.assertEqual(run_module("empty-module", root), 0)

    def test_implementation_without_makefile_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module = root / "modules" / "unfinished"
            module.mkdir(parents=True)
            (module / "module.yaml").write_text("name: unfinished\n", encoding="utf-8")
            (module / "main.go").write_text("package main\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "no Makefile"):
                run_module("unfinished", root)

    def test_stable_version_only_change_is_detected_by_yaml_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.invalid"],
                cwd=root,
                check=True,
            )
            subprocess.run(["git", "config", "user.name", "Catalog Test"], cwd=root, check=True)
            module = root / "modules" / "example" / "module.yaml"
            module.parent.mkdir(parents=True)
            module.write_text(
                "schemaVersion: 1\nname: example\ntype: library\nversion: 0.0.0\n"
                "owners: [team]\ndependencies: []\n",
                encoding="utf-8",
            )
            solution = root / "solution.yaml"
            solution.write_text("solution:\n  version: 0.0.0\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "initial"], cwd=root, check=True)
            base = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
            ).stdout.strip()

            module.write_text(module.read_text(encoding="utf-8").replace("0.0.0", "0.0.1"), encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "stable version record"], cwd=root, check=True)
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
            ).stdout.strip()
            self.assertTrue(
                is_stable_version_only_change(["modules/example/module.yaml"], base, head, root)
            )

            (root / "modules" / "example" / "source.go").write_text("package example\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "code change"], cwd=root, check=True)
            code_head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
            ).stdout.strip()
            self.assertFalse(
                is_stable_version_only_change(
                    ["modules/example/module.yaml", "modules/example/source.go"],
                    base,
                    code_head,
                    root,
                )
            )


if __name__ == "__main__":
    unittest.main()
