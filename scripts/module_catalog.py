#!/usr/bin/env python3
"""Validate solution/module catalogs and plan safe module CI work."""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError
from yaml.constructor import ConstructorError

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "schemas"


class UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""


def _construct_unique_mapping(
    loader: UniqueKeyLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _load_yaml(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as stream:
            return yaml.load(stream, Loader=UniqueKeyLoader)
    except (OSError, yaml.YAMLError) as error:
        raise ValueError(f"{path}: {error}") from error


def _schema_errors(document: Any, schema_name: str, label: str) -> list[str]:
    schema_path = SCHEMAS / schema_name
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
    except (OSError, json.JSONDecodeError, SchemaError, ValueError) as error:
        raise ValueError(f"{schema_path}: invalid JSON Schema: {error}") from error
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = []
    for error in sorted(validator.iter_errors(document), key=lambda item: tuple(map(str, item.absolute_path))):
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        errors.append(f"{label}: {location}: {error.message}")
    return errors


def _module_manifests(root: Path) -> dict[str, tuple[Path, Any]]:
    manifests: dict[str, tuple[Path, Any]] = {}
    for path in sorted((root / "modules").glob("*/module.yaml")):
        manifests[path.parent.name] = (path, _load_yaml(path))
    return manifests


def _dependency_cycle_errors(modules: dict[str, tuple[Path, Any]]) -> list[str]:
    errors: list[str] = []
    visited: set[str] = set()
    active: list[str] = []

    def visit(name: str) -> None:
        if name in active:
            cycle = active[active.index(name) :] + [name]
            errors.append(f"module dependency cycle: {' -> '.join(cycle)}")
            return
        if name in visited:
            return
        active.append(name)
        _, manifest = modules[name]
        for dependency in manifest.get("dependencies", []):
            if dependency in modules:
                visit(dependency)
        active.pop()
        visited.add(name)

    for module_name in sorted(modules):
        visit(module_name)
    return errors


def validate_catalog(root: Path = ROOT) -> list[str]:
    """Validate schemas, catalog/manifests, dependency references, and release records."""
    errors: list[str] = []
    solution_path = root / "solution.yaml"
    try:
        for schema_path in sorted((root / "schemas").glob("*.schema.json")):
            try:
                schema = json.loads(schema_path.read_text(encoding="utf-8"))
                Draft202012Validator.check_schema(schema)
            except (OSError, json.JSONDecodeError, SchemaError, ValueError) as error:
                raise ValueError(f"{schema_path}: invalid JSON Schema: {error}") from error
        solution = _load_yaml(solution_path)
        errors.extend(_schema_errors(solution, "solution.schema.json", str(solution_path.relative_to(root))))
        modules = _module_manifests(root)
        for folder_name, (path, manifest) in modules.items():
            errors.extend(_schema_errors(manifest, "module.schema.json", str(path.relative_to(root))))
            if isinstance(manifest, dict) and manifest.get("name") != folder_name:
                errors.append(
                    f"{path.relative_to(root)}: manifest name {manifest.get('name')!r} "
                    f"does not match folder {folder_name!r}"
                )

        if (
            isinstance(solution, dict)
            and isinstance(solution.get("modules"), list)
            and all(isinstance(name, str) for name in solution["modules"])
        ):
            listed = set(solution["modules"])
            found = set(modules)
            for missing in sorted(listed - found):
                errors.append(f"solution.yaml: listed module {missing!r} has no modules/{missing}/module.yaml")
            for unlisted in sorted(found - listed):
                errors.append(f"modules/{unlisted}/module.yaml: module is not listed in solution.yaml")
            for module_name, (path, manifest) in modules.items():
                if not isinstance(manifest, dict):
                    continue
                dependencies = manifest.get("dependencies", [])
                if not isinstance(dependencies, list):
                    continue
                for dependency in dependencies:
                    if not isinstance(dependency, str):
                        continue
                    if dependency not in listed:
                        errors.append(
                            f"{path.relative_to(root)}: dependency {dependency!r} is not listed in solution.yaml"
                        )
                    if dependency == module_name:
                        errors.append(f"{path.relative_to(root)}: module cannot depend on itself")
            cycle_candidates = {
                name: entry
                for name, entry in modules.items()
                if isinstance(entry[1], dict)
                and isinstance(entry[1].get("dependencies"), list)
                and all(isinstance(dependency, str) for dependency in entry[1]["dependencies"])
            }
            errors.extend(_dependency_cycle_errors(cycle_candidates))

        for path in sorted((root / "releases" / "modules").glob("*/*.yaml")):
            manifest = _load_yaml(path)
            errors.extend(_schema_errors(manifest, "module-release.schema.json", str(path.relative_to(root))))
            if isinstance(manifest, dict) and manifest.get("module") != path.parent.name:
                errors.append(f"{path.relative_to(root)}: manifest module must match parent directory")
            if isinstance(manifest, dict) and manifest.get("version") != path.stem:
                errors.append(f"{path.relative_to(root)}: manifest version must match filename")
            if isinstance(manifest, dict) and manifest.get("module") not in modules:
                errors.append(f"{path.relative_to(root)}: release references an unknown catalog module")
            if isinstance(manifest, dict):
                module_name = manifest.get("module")
                version = manifest.get("version")
                source = manifest.get("source")
                if isinstance(module_name, str) and isinstance(version, str) and isinstance(source, dict):
                    expected_tag = f"modules/{module_name}/v{version}"
                    if source.get("gitTag") != expected_tag:
                        errors.append(f"{path.relative_to(root)}: source.gitTag must be {expected_tag!r}")
                artifacts = manifest.get("artifacts")
                if isinstance(version, str) and isinstance(artifacts, dict):
                    images = artifacts.get("images", [])
                    charts = artifacts.get("charts", [])
                    for image in images if isinstance(images, list) else []:
                        if isinstance(image, dict) and image.get("tag") != version:
                            errors.append(f"{path.relative_to(root)}: image tag must match release version")
                    for chart in charts if isinstance(charts, list) else []:
                        if isinstance(chart, dict) and chart.get("version") != version:
                            errors.append(f"{path.relative_to(root)}: chart version must match release version")

        for path in sorted((root / "releases" / "solutions").glob("*.yaml")):
            manifest = _load_yaml(path)
            errors.extend(_schema_errors(manifest, "solution-release.schema.json", str(path.relative_to(root))))
            solution_release = manifest.get("solution") if isinstance(manifest, dict) else None
            if isinstance(solution_release, dict) and solution_release.get("version") != path.stem:
                errors.append(f"{path.relative_to(root)}: solution version must match filename")
            if isinstance(solution_release, dict):
                version = solution_release.get("version")
                expected_tag = f"solution/v{version}"
                if solution_release.get("gitTag") != expected_tag:
                    errors.append(f"{path.relative_to(root)}: solution.gitTag must be {expected_tag!r}")
            if isinstance(manifest, dict) and isinstance(manifest.get("modules"), list):
                module_names = [
                    item.get("name")
                    for item in manifest["modules"]
                    if isinstance(item, dict) and isinstance(item.get("name"), str)
                ]
                if len(module_names) != len(set(module_names)):
                    errors.append(f"{path.relative_to(root)}: module names must be unique")

        state_path = root / "releases" / "release-state.yaml"
        if state_path.exists():
            errors.extend(
                _schema_errors(_load_yaml(state_path), "release-state.schema.json", str(state_path.relative_to(root)))
            )
        impact_path = root / "releases" / "impacts.yaml"
        if impact_path.exists():
            policy = _load_yaml(impact_path)
            errors.extend(_schema_errors(policy, "impacts.schema.json", "releases/impacts.yaml"))
            if isinstance(policy, dict) and isinstance(policy.get("modules"), dict):
                for name in policy["modules"]:
                    if name not in modules:
                        errors.append(f"releases/impacts.yaml: unknown module {name!r}")
    except (OSError, json.JSONDecodeError, yaml.YAMLError, ConstructorError, ValueError) as error:
        errors.append(str(error))
    return errors


def affected_modules(changed_paths: list[str], modules: dict[str, tuple[Path, Any]]) -> list[str]:
    """Return changed modules plus all transitive dependents."""
    names = set(modules)
    if any(path == "solution.yaml" or path.startswith(("schemas/", "scripts/")) for path in changed_paths):
        return sorted(names)
    if any(path in {"Makefile", "scripts/requirements-catalog.txt"} for path in changed_paths):
        return sorted(names)

    affected: set[str] = set()
    for path in changed_paths:
        match = re.match(r"^modules/([^/]+)/", path)
        if match:
            module_name = match.group(1)
            if module_name not in names:
                raise ValueError(f"changed path references unknown catalog module: {module_name}")
            affected.add(module_name)

    changed = True
    while changed:
        changed = False
        for module_name, (_, manifest) in modules.items():
            dependencies = set(manifest.get("dependencies", []))
            if module_name not in affected and dependencies & affected:
                affected.add(module_name)
                changed = True
    return sorted(affected)


def _without_version(document: Any, section: str | None = None) -> Any:
    result = copy.deepcopy(document)
    target = result if section is None else result.get(section, {})
    if not isinstance(target, dict) or "version" not in target:
        raise ValueError("stable version field is missing")
    del target["version"]
    return result


def is_stable_version_only_change(
    changed_paths: list[str],
    base: str,
    head: str,
    root: Path = ROOT,
) -> bool:
    """Recognize catalog-only stable version PRs by comparing parsed YAML content."""
    if not changed_paths:
        return False
    allowed_paths = {
        path
        for path in changed_paths
        if path == "solution.yaml"
        or re.fullmatch(r"modules/[a-z][a-z0-9]*(?:-[a-z0-9]+)*/module\.yaml", path)
    }
    if set(changed_paths) != allowed_paths:
        return False
    changed_version = False
    try:
        for path in changed_paths:
            base_data = yaml.safe_load(
                subprocess.run(
                    ["git", "show", f"{base}:{path}"],
                    cwd=root,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout
            )
            head_data = yaml.safe_load(
                subprocess.run(
                    ["git", "show", f"{head}:{path}"],
                    cwd=root,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout
            )
            if path == "solution.yaml":
                changed_version |= base_data["solution"]["version"] != head_data["solution"]["version"]
                if _without_version(base_data, "solution") != _without_version(head_data, "solution"):
                    return False
            else:
                changed_version |= base_data["version"] != head_data["version"]
                if _without_version(base_data) != _without_version(head_data):
                    return False
    except (KeyError, OSError, subprocess.CalledProcessError, yaml.YAMLError, ValueError):
        return False
    return changed_version


def _changed_paths(base: str, head: str, root: Path) -> list[str]:
    if re.fullmatch(r"0+", base):
        return []
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=ACMRTD", f"{base}...{head}"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return [path for path in result.stdout.splitlines() if path]


def plan_matrix(base: str, head: str, root: Path = ROOT) -> dict[str, Any]:
    modules = _module_manifests(root)
    paths = _changed_paths(base, head, root)
    if re.fullmatch(r"0+", base):
        selected = sorted(modules)
    elif is_stable_version_only_change(paths, base, head, root):
        selected = []
    else:
        selected = affected_modules(paths, modules)
    return {"include": [{"module": name} for name in selected]}


def run_module(module_name: str, root: Path = ROOT) -> int:
    module_path = root / "modules" / module_name
    manifest_path = module_path / "module.yaml"
    if not manifest_path.is_file():
        raise ValueError(f"module manifest does not exist: {manifest_path.relative_to(root)}")
    implementation_files = [
        path for path in module_path.rglob("*") if path.is_file() and path != manifest_path
    ]
    makefile = module_path / "Makefile"
    if not implementation_files:
        print(f"skip     {module_name}: catalog-only module; implementation has not started")
        return 0
    if not makefile.is_file():
        raise ValueError(
            f"modules/{module_name} has implementation files but no Makefile; "
            "add the required lint, test, and build targets"
        )
    return subprocess.run(["make", "-C", str(module_path), "lint", "test", "build"], check=False).returncode


def local_modules(root: Path = ROOT) -> list[str]:
    """Discover runnable local packages without interpreting descriptive module types."""
    catalog = _load_yaml(root / "solution.yaml")["modules"]
    return sorted(
        name
        for name, (path, _) in _module_manifests(root).items()
        if name in catalog
        if (path.parent / "Dockerfile").is_file()
        and (path.parent / "chart" / "Chart.yaml").is_file()
    )


def _write_github_output(matrix: dict[str, Any]) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as stream:
            stream.write(f"matrix={json.dumps(matrix, separators=(',', ':'))}\n")
            stream.write(f"has_modules={'true' if matrix['include'] else 'false'}\n")
    else:
        print(json.dumps(matrix, separators=(",", ":")))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate", help="validate solution, module, and release manifests")
    subparsers.add_parser("local-modules", help="list modules with a Dockerfile and Helm chart")
    plan_parser = subparsers.add_parser("plan", help="write the affected module matrix")
    plan_parser.add_argument("--base", required=True)
    plan_parser.add_argument("--head", required=True)
    module_parser = subparsers.add_parser("run-module", help="run a module's build/test/lint targets")
    module_parser.add_argument("--name", required=True)
    args = parser.parse_args()

    try:
        if args.command == "validate":
            errors = validate_catalog()
            if errors:
                print("\n".join(f"ERROR {error}" for error in errors), file=sys.stderr)
                return 1
            print(f"Catalog valid: {len(_module_manifests(ROOT))} modules.")
            return 0
        if args.command == "plan":
            _write_github_output(plan_matrix(args.base, args.head))
            return 0
        if args.command == "local-modules":
            print("\n".join(local_modules()))
            return 0
        return run_module(args.name)
    except (OSError, subprocess.CalledProcessError, ValueError, yaml.YAMLError) as error:
        print(f"ERROR {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
