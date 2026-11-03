"""Freeze and promote reviewed release records without rebuilding artifacts."""

from __future__ import annotations

import base64
import copy
import json
import os
import re
import urllib.error
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from scripts import release_baseline as baseline
from scripts.module_catalog import ROOT, _load_yaml, validate_catalog
from scripts.release_state import GitHubAPI, STATE_BRANCH
from scripts.release_versions import active_iteration, target_version

NAME = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
IMPACTS = {"patch", "minor", "major"}
PROMOTIONS = {"iteration", "release_candidate", "stable"}
RELEASE_CHOICES = {"module", "solution"}


def validate_inputs(
    release_choice: str | None,
    module: str | None,
    impact: str | None,
    promote: str | None,
) -> tuple[str, str, str, str]:
    release_choice = release_choice or "solution"
    module = module or ""
    impact = impact or "patch"
    promote = promote or "iteration"
    if release_choice not in RELEASE_CHOICES:
        raise ValueError("release choice must be module or solution")
    if release_choice == "solution" and module:
        raise ValueError("module name must be blank for a solution release")
    if release_choice == "module" and not NAME.fullmatch(module):
        raise ValueError("a valid module name is required for a module release")
    if impact not in IMPACTS:
        raise ValueError("release type must be major, minor, or patch")
    if promote not in PROMOTIONS:
        raise ValueError("promotion must be iteration, release_candidate, or stable")
    return release_choice, module, impact, promote


def _read_record(api: GitHubAPI, path: str) -> dict | None:
    return baseline.read_record(api, path)


def list_records(api: GitHubAPI, directory: str) -> list[tuple[str, dict]]:
    """Read JSON records from a state-branch directory, including nested folders."""
    pending = [directory]
    found: list[tuple[str, dict]] = []
    while pending:
        path = pending.pop()
        try:
            response = api.request(
                "GET", f"/contents/{path}?ref={urllib.parse.quote(STATE_BRANCH, safe='')}"
            )
        except urllib.error.HTTPError as error:
            if error.code == 404:
                error.close()
                continue
            raise
        items = response if isinstance(response, list) else [response]
        for item in items:
            if item.get("type") == "dir":
                pending.append(item["path"])
            elif item.get("type") == "file" and item["name"].endswith(".json"):
                document = _read_record(api, item["path"])
                if document is not None:
                    found.append((item["path"], document))
    return found


def require_stable_environment(api: GitHubAPI) -> None:
    environment = api.request("GET", "/environments/release-stable")
    if environment.get("name") != "release-stable":
        raise ValueError("the release-stable environment is missing or not identifiable")
    rules = environment.get("protection_rules")
    if not isinstance(rules, list):
        raise ValueError("release-stable has no readable protection rules; refusing stable promotion")
    has_reviewers = any(
        rule.get("type") == "required_reviewers"
        and isinstance(rule.get("reviewers"), list)
        and any(
            reviewer.get("type") in {"User", "Team"}
            and isinstance(reviewer.get("reviewer"), dict)
            for reviewer in rule["reviewers"]
        )
        for rule in rules
        if isinstance(rule, dict)
    )
    if not has_reviewers:
        raise ValueError(
            "release-stable must have a required-reviewers protection rule with at least one reviewer"
        )


def _catalog_target(
    root: Path, release_choice: str, module: str, impact: str
) -> tuple[str, str, str]:
    catalog = _load_yaml(root / "solution.yaml")
    if release_choice == "solution":
        stable = catalog["solution"]["version"]
        return stable, target_version(stable, [impact]), ""

    if module not in catalog["modules"]:
        raise ValueError(f"{module}: module is not in the solution catalog")
    manifest_path = root / "modules" / module / "module.yaml"
    manifest = _load_yaml(manifest_path)
    if not (root / "modules" / module / "Dockerfile").is_file():
        raise ValueError(f"{module}: module has no packaged image to release")
    stable = manifest["version"]
    return stable, target_version(stable, [impact]), impact


def select_module_publication(
    root: Path, state: dict, source: str, module_name: str
) -> dict:
    """Select one module's latest unchanged main publication, independent of peers."""
    from scripts.module_catalog import _load_yaml, plan_matrix
    from scripts.release_state import validate_state

    validate_state(state)
    catalog = _load_yaml(root / "solution.yaml")
    if module_name not in catalog["modules"]:
        raise ValueError(f"{module_name}: module is not in the solution catalog")
    module = root / "modules" / module_name
    if not (module / "Dockerfile").is_file():
        raise ValueError(f"{module_name}: module has no packaged image")
    candidates = [
        entry for entry in state["reservations"]
        if entry["module"] == module_name and entry["releaseLine"] == "main"
        and entry["branch"] == "main" and "publication" in entry
        and baseline.ancestor(root, entry["sourceCommit"], source)
    ]
    if not candidates:
        raise ValueError(f"{module_name}: no published main revision at or before selected source")
    chosen = candidates[0]
    for candidate in candidates[1:]:
        if baseline.ancestor(root, chosen["sourceCommit"], candidate["sourceCommit"]):
            chosen = candidate
    changed_pins = baseline.git(root, "diff", "--name-only", chosen["sourceCommit"], source, "--", ".tool-versions")
    changed_module = any(
        item["module"] == module_name
        for item in plan_matrix(chosen["sourceCommit"], source, root)["include"]
    )
    if changed_pins or changed_module:
        scope = "toolchain pins" if changed_pins else "module or dependency source"
        raise ValueError(
            f"publication pending for {source}: {module_name} has {scope} changes since "
            f"published source {chosen['sourceCommit']}; publish the selected source first"
        )
    publication = chosen["publication"]
    if (module / "chart" / "Chart.yaml").is_file() and "chart" not in publication:
        raise ValueError(f"{module_name}: chart publication is incomplete")
    return chosen


def build_module_manifest(
    entry: dict, version: str, repository: str, run_url: str, impact: str | None = None,
) -> dict:
    publication = entry["publication"]
    approved_impact = impact or entry["approvedImpact"]
    artifacts = {
        "images": [{
            "repository": publication["repository"], "tag": version,
            "digest": publication["digest"],
        }],
        "charts": [publication["chart"]] if "chart" in publication else [],
    }
    manifest = {
        "schemaVersion": 1,
        "kind": "module-release",
        "module": entry["module"],
        "version": version,
        "source": {
            "repository": f"https://github.com/{repository}",
            "commit": entry["sourceCommit"],
            "gitTag": f"modules/{entry['module']}/v{version}",
        },
        "workflow": {
            "runId": run_url.rsplit("/", 1)[-1],
            "url": run_url,
        },
        "artifacts": artifacts,
        "evidence": {
            "tests": [publication["testRun"], publication["workflowRun"]],
            "sbom": publication["sbom"],
            "provenance": publication["provenance"],
            "approvedImpact": approved_impact,
            "qa": [
                f"Promoted integrated revision {publication['version']} without rebuilding",
                (
                    f"Standalone protected dispatch selected {approved_impact} impact from stable "
                    f"{entry.get('stableVersion', 'catalog version')}; source publication retained "
                    f"target {entry['targetVersion']} and impact {entry['approvedImpact']}"
                ),
            ] if impact is not None else [
                f"Promoted integrated revision {publication['version']} without rebuilding"
            ],
        },
    }
    baseline.schema(manifest, "module-release.schema.json")
    return manifest


def _pi_key(pi: str) -> tuple[int, int]:
    match = re.fullmatch(r"PI-([0-9]{4})\.([1-9][0-9]*)", pi)
    if not match:
        raise ValueError(f"invalid PI identity in frozen baseline: {pi!r}")
    return int(match[1]), int(match[2])


def _baseline_candidates(
    records: list[tuple[str, dict]], release_choice: str, target: str, module: str
) -> list[dict]:
    candidates = []
    for path, record in records:
        if path.startswith("release-baselines/") is False:
            continue
        if record.get("kind") == "module-baseline":
            if release_choice != "module" or record.get("module") != module:
                continue
            manifest = record.get("release")
            pi = record.get("pi", {}).get("id")
            version = manifest.get("version", "") if isinstance(manifest, dict) else ""
            if baseline.baseline_version(version)[0] != target:
                continue
            candidates.append({
                "path": path, "pi": pi, "iteration": baseline.baseline_version(version)[1],
                "manifest": manifest, "sourceCommit": manifest["source"]["commit"],
                "targetVersion": target, "version": version, "choice": "module",
            })
            continue

        solution = record.get("solution")
        if not isinstance(solution, dict):
            continue
        solution_info = solution.get("solution", {})
        pi = solution.get("pi", {}).get("id")
        if release_choice == "solution":
            version = solution_info.get("version", "")
            if baseline.baseline_version(version)[0] != target:
                continue
            candidates.append({
                "path": path, "pi": pi, "iteration": baseline.baseline_version(version)[1],
                "manifest": solution, "bundle": record,
                "sourceCommit": solution_info["sourceCommit"],
                "targetVersion": target, "version": version, "choice": "solution",
            })
        else:
            for module_manifest in record.get("modules", []):
                if module_manifest.get("module") != module:
                    continue
                version = module_manifest.get("version", "")
                if baseline.baseline_version(version)[0] != target:
                    continue
                candidates.append({
                    "path": path, "pi": pi, "iteration": baseline.baseline_version(version)[1],
                    "manifest": module_manifest, "bundle": record,
                    "sourceCommit": module_manifest["source"]["commit"],
                    "targetVersion": target, "version": version, "choice": "module",
                })
    return candidates


def select_latest_baseline(
    records: list[tuple[str, dict]], release_choice: str, target: str, module: str = ""
) -> dict:
    candidates = _baseline_candidates(records, release_choice, target, module)
    if not candidates:
        raise ValueError(f"no frozen iteration baseline is eligible for {target}")
    candidates.sort(key=lambda item: (_pi_key(item["pi"]), item["iteration"]))
    latest = candidates[-1]
    ties = [
        item for item in candidates
        if (_pi_key(item["pi"]), item["iteration"]) == (_pi_key(latest["pi"]), latest["iteration"])
    ]
    if len(ties) != 1:
        raise ValueError(f"ambiguous frozen iteration baselines for {target}; review state records")
    return latest


def _verify_manifest_artifacts(manifest: dict) -> None:
    if manifest["kind"] == "solution-release":
        baseline.schema(manifest, "solution-release.schema.json")
        for module in manifest["modules"]:
            baseline.verify_artifact_set(module["name"], module["version"], module["artifacts"])
    else:
        baseline.verify_module_artifacts(manifest)


def _scope(release_choice: str, module: str) -> str:
    return f"module-{module}" if release_choice == "module" else "solution"


def _promoted_manifest(selection: dict, version: str, release_choice: str) -> dict:
    manifest = copy.deepcopy(selection["manifest"])
    if release_choice == "solution":
        manifest["solution"]["version"] = version
        manifest["solution"]["gitTag"] = f"solution/v{version}"
    else:
        manifest["version"] = version
        manifest["source"]["gitTag"] = f"modules/{manifest['module']}/v{version}"
        for image in manifest["artifacts"]["images"]:
            image["tag"] = version
    return manifest


def _existing_request(
    records: list[tuple[str, dict]], run_id: str, release_choice: str, module: str, promote: str
) -> tuple[str, dict] | None:
    matches = [
        (path, record) for path, record in records
        if record.get("requestRunId") == run_id
        and record.get("releaseChoice") == release_choice
        and record.get("module", "") == module
        and record.get("promote") == promote
    ]
    if len(matches) > 1:
        raise ValueError("this workflow run has conflicting frozen release records")
    return matches[0] if matches else None


def _next_rc(records: list[tuple[str, dict]], release_choice: str, module: str, target: str) -> int:
    pattern = re.compile(rf"^{re.escape(target)}-rc([1-9][0-9]*)$")
    counters = [
        int(match[1])
        for path, record in records
        if record.get("releaseChoice") == release_choice
        and record.get("module", "") == module
        and record.get("promote") == "release_candidate"
        and (match := pattern.fullmatch(record.get("version", "")))
    ]
    return max(counters, default=0) + 1


def _latest_rc(
    records: list[tuple[str, dict]], release_choice: str, module: str, target: str
) -> tuple[str, dict]:
    candidates = [
        (path, record) for path, record in records
        if record.get("promote") == "release_candidate"
        and record.get("releaseChoice") == release_choice
        and record.get("module", "") == module
        and record.get("targetVersion") == target
    ]
    if not candidates:
        raise ValueError(f"no eligible release candidate exists for {target}")
    counters = []
    for path, record in candidates:
        match = re.fullmatch(rf"{re.escape(target)}-rc([1-9][0-9]*)", record["version"])
        if not match:
            raise ValueError(f"{path}: invalid frozen release-candidate version")
        counters.append((int(match[1]), path, record))
    highest = max(item[0] for item in counters)
    latest = [(path, record) for counter, path, record in counters if counter == highest]
    if len(latest) != 1:
        raise ValueError(f"ambiguous release candidates for {target}")
    return latest[0]


def _freeze_promotion(api: GitHubAPI, record: dict) -> tuple[str, dict]:
    path = f"release-promotions/{record['recordName']}.json"
    return path, baseline.freeze(api, path, record)


def _release_record(
    api: GitHubAPI, release_choice: str, module: str, impact: str, promote: str,
    root: Path, run_id: str, repository: str,
) -> tuple[str, dict]:
    records = list_records(api, "release-promotions")
    prior = _existing_request(records, run_id, release_choice, module, promote)
    if prior:
        return prior

    stable, target, _ = _catalog_target(root, release_choice, module, impact)

    if promote == "release_candidate":
        baseline_records = list_records(api, "release-baselines")
        selection = select_latest_baseline(baseline_records, release_choice, target, module)
        # A retry dispatched as a new workflow run must not consume another RC number
        # when the latest RC already represents the same immutable baseline.
        same_source = [
            (path, record) for path, record in records
            if record.get("promote") == "release_candidate"
            and record.get("releaseChoice") == release_choice
            and record.get("module", "") == module
            and record.get("targetVersion") == target
            and record.get("sourceBaseline") == selection["path"]
        ]
        if same_source:
            if len(same_source) > 1:
                raise ValueError("multiple release candidates reference the same frozen baseline")
            return same_source[0]
        version = f"{target}-rc{_next_rc(records, release_choice, module, target)}"
        manifest = _promoted_manifest(selection, version, release_choice)
        baseline.schema(
            manifest,
            "solution-release.schema.json" if release_choice == "solution" else "module-release.schema.json",
        )
        if "bundle" in selection:
            baseline.verify_artifacts(selection["bundle"])
        else:
            _verify_manifest_artifacts(selection["manifest"])
        record_name = f"{_scope(release_choice, module)}-{version}"
        record = {
            "schemaVersion": 1, "promote": promote, "releaseChoice": release_choice,
            "module": module, "impact": impact, "baseVersion": stable,
            "targetVersion": target, "version": version, "recordName": record_name,
            "sourceBaseline": selection["path"], "sourceBaselineVersion": selection["version"],
            "sourceCommit": selection["sourceCommit"], "pi": selection["pi"],
            "requestRunId": run_id, "manifest": manifest,
            "repository": repository,
        }
        return _freeze_promotion(api, record)

    if promote == "stable":
        source_path, rc_record = _latest_rc(records, release_choice, module, target)
        if rc_record.get("baseVersion") != stable or rc_record.get("impact") != impact:
            raise ValueError("latest release candidate conflicts with the selected stable version or impact")
        stable_path = f"release-promotions/{_scope(release_choice, module)}-{target}-stable.json"
        frozen_stable = next(
            ((path, record) for path, record in records if path == stable_path),
            None,
        )
        if frozen_stable is not None:
            if (
                frozen_stable[1].get("sourceBaseline") != source_path
                or frozen_stable[1].get("baseVersion") != stable
                or frozen_stable[1].get("impact") != impact
            ):
                raise ValueError("stable version is already frozen from a conflicting release candidate")
            return frozen_stable
        version = target
        manifest = _promoted_manifest({
            "manifest": rc_record["manifest"],
        }, version, release_choice)
        baseline.schema(
            manifest,
            "solution-release.schema.json" if release_choice == "solution" else "module-release.schema.json",
        )
        _verify_manifest_artifacts(rc_record["manifest"])
        record_name = f"{_scope(release_choice, module)}-{version}-stable"
        record = {
            "schemaVersion": 1, "promote": promote, "releaseChoice": release_choice,
            "module": module, "impact": impact, "baseVersion": stable,
            "targetVersion": target, "version": version, "recordName": record_name,
            "sourceBaseline": source_path, "sourceBaselineVersion": rc_record["sourceBaselineVersion"],
            "sourceCommit": rc_record["sourceCommit"], "pi": rc_record["pi"],
            "requestRunId": run_id, "manifest": manifest,
            "repository": repository,
        }
        return _freeze_promotion(api, record)

    # Iteration baselines are frozen under their legacy paths and data shape so
    # existing records and tags remain eligible for later RC promotion.
    source = os.environ["GITHUB_SHA"]
    if not re.fullmatch(r"[a-f0-9]{40}", source):
        raise ValueError("workflow source must be a full lowercase commit SHA")
    if baseline.git(root, "rev-parse", "HEAD") != source or not baseline.ancestor(root, source, "origin/main"):
        raise ValueError("iteration source must be the exact dispatch commit on main")
    errors = validate_catalog(root)
    if errors:
        raise ValueError("\n".join(errors))
    baseline.require_build(api, repository, source)
    run_url = f"https://github.com/{repository}/actions/runs/{run_id}"
    frozen_requests = []
    for existing_path, existing in list_records(api, "release-baselines"):
        if (
            existing.get("requestRunId") == run_id
            and existing.get("releaseChoice") == release_choice
            and existing.get("module", "") == module
            and existing.get("targetVersion") == target
            and existing.get("dispatchSource") == source
        ):
            frozen_requests.append((existing_path, existing))
        elif release_choice == "solution" and "solution" in existing:
            solution = existing["solution"]
            if (
                solution.get("solution", {}).get("sourceCommit") == source
                and solution.get("solution", {}).get("version", "").startswith(f"{target}-it")
                and solution.get("qa", {}).get("runForRecord", "").endswith(f"/{run_id}")
            ):
                frozen_requests.append((existing_path, {
                    "kind": "legacy-solution-baseline", "solution": solution,
                    "bundle": existing, "version": solution["solution"]["version"],
                    "pi": solution["pi"]["id"], "targetVersion": target,
                    "sourceCommit": source,
                }))
    if len(frozen_requests) > 1:
        raise ValueError("this workflow run has multiple conflicting frozen iteration baselines")
    if frozen_requests:
        path, frozen = frozen_requests[0]
        if release_choice == "solution":
            bundle = frozen["bundle"]
            baseline.schema(frozen["solution"], "solution-release.schema.json")
            baseline.verify_artifacts(bundle)
            version = frozen["version"]
            return path, {
                "schemaVersion": 1, "promote": "iteration", "releaseChoice": release_choice,
                "module": "", "impact": impact, "baseVersion": stable, "targetVersion": target,
                "version": version, "recordName": f"solution-{version}",
                "manifest": frozen["solution"], "bundle": bundle, "sourceCommit": source,
                "pi": frozen["pi"], "requestRunId": run_id, "repository": repository,
            }
        manifest = frozen["release"]
        baseline.schema(manifest, "module-release.schema.json")
        _verify_manifest_artifacts(manifest)
        return path, {
            "schemaVersion": 1, "promote": "iteration", "releaseChoice": release_choice,
            "module": module, "impact": impact, "baseVersion": stable, "targetVersion": target,
            "version": manifest["version"], "recordName": f"module-{module}-{manifest['version']}",
            "manifest": manifest, "sourceCommit": frozen["dispatchSource"],
            "pi": frozen["pi"]["id"], "requestRunId": run_id, "repository": repository,
        }
    if release_choice == "solution":
        iteration = active_iteration(
            _load_yaml(root / "solution.yaml")["pi"], datetime.now(timezone.utc)
        )
        version = f"{target}-it{iteration}"
        path = f"release-baselines/solution-{version}.json"
        frozen = _read_record(api, path)
        if frozen is None:
            state, _ = baseline.GitHubState(api).read()
            selected = baseline.select_publications(root, state, source)
            for entry in selected:
                baseline.require_build(api, repository, entry["sourceCommit"])
                baseline.require_publication(
                    api, repository, entry["publication"]["workflowRun"], entry["sourceCommit"]
                )
            bundle = baseline.build_bundle(
                root, selected, version, source, repository, run_url, datetime.now(timezone.utc)
            )
            if baseline.baseline_version(version)[0] != target:
                raise ValueError("solution baseline target differs from the requested reviewed impact")
            baseline.verify_artifacts(bundle)
            frozen = baseline.freeze(api, path, bundle)
        else:
            if frozen["solution"]["solution"]["sourceCommit"] != source:
                raise ValueError("baseline version is already frozen for a different source")
            baseline.schema(frozen["solution"], "solution-release.schema.json")
            baseline.verify_artifacts(frozen)
        record_name = f"solution-{version}"
        return path, {
            "schemaVersion": 1, "promote": "iteration", "releaseChoice": release_choice,
            "module": "", "impact": impact, "baseVersion": stable, "targetVersion": target,
            "version": version, "recordName": record_name, "manifest": frozen["solution"],
            "bundle": frozen, "sourceCommit": source, "pi": frozen["solution"]["pi"]["id"],
            "requestRunId": run_id, "repository": repository,
        }

    iteration = active_iteration(
        _load_yaml(root / "solution.yaml")["pi"],
        datetime.now(timezone.utc),
    )
    version = f"{target}-it{iteration}"
    path = f"release-baselines/module-{module}-{version}.json"
    frozen = _read_record(api, path)
    if frozen is None:
        state, _ = baseline.GitHubState(api).read()
        selected = select_module_publication(root, state, source, module)
        baseline.require_build(api, repository, selected["sourceCommit"])
        baseline.require_publication(
            api, repository, selected["publication"]["workflowRun"], selected["sourceCommit"]
        )
        manifest = build_module_manifest(
            {**selected, "stableVersion": stable}, version, repository, run_url, impact=impact,
        )
        baseline.verify_module_artifacts(manifest)
        frozen = baseline.freeze(api, path, {
            "schemaVersion": 1, "kind": "module-baseline", "module": module,
            "targetVersion": target, "pi": {"id": _load_yaml(root / "solution.yaml")["pi"]["id"]},
            "release": manifest, "dispatchSource": source, "requestRunId": run_id,
        })
    else:
        if (
            frozen.get("kind") != "module-baseline"
            or frozen.get("module") != module
            or frozen.get("targetVersion") != target
            or frozen.get("dispatchSource") != source
            or frozen["release"].get("module") != module
            or baseline.baseline_version(frozen["release"]["version"])[0] != target
        ):
            raise ValueError("frozen module baseline record is malformed")
        baseline.schema(frozen["release"], "module-release.schema.json")
        _verify_manifest_artifacts(frozen["release"])
    return path, {
        "schemaVersion": 1, "promote": "iteration", "releaseChoice": release_choice,
        "module": module, "impact": impact, "baseVersion": stable, "targetVersion": target,
        "version": version, "recordName": f"module-{module}-{version}",
        "manifest": frozen["release"], "sourceCommit": frozen["release"]["source"]["commit"],
        "pi": frozen["pi"]["id"], "requestRunId": run_id, "repository": repository,
    }


def _publish_record(api: GitHubAPI, record: dict, prerelease: bool) -> None:
    manifest = record["manifest"]
    choice = record["releaseChoice"]
    if choice == "module":
        for image in manifest["artifacts"]["images"]:
            baseline.promote_image(image)
        tag = manifest["source"]["gitTag"]
        commit = manifest["source"]["commit"]
    else:
        tag = manifest["solution"]["gitTag"]
        commit = manifest["solution"]["sourceCommit"]
    baseline.ensure_tag(api, tag, commit, manifest)
    baseline.release(api, manifest, tag, prerelease=prerelease)


def _validate_promoted_record(record: dict) -> None:
    choice = record["releaseChoice"]
    module = record.get("module", "")
    version = record["version"]
    target = record["targetVersion"]
    if record["promote"] == "release_candidate":
        if not re.fullmatch(rf"{re.escape(target)}-rc[1-9][0-9]*", version):
            raise ValueError("frozen release candidate has an invalid target version")
    elif record["promote"] == "stable" and version != target:
        raise ValueError("frozen stable release version does not match its target")
    manifest = record["manifest"]
    baseline.schema(
        manifest,
        "solution-release.schema.json" if choice == "solution" else "module-release.schema.json",
    )
    if choice == "solution":
        solution = manifest["solution"]
        if (
            solution["version"] != version
            or solution["gitTag"] != f"solution/v{version}"
            or solution["sourceCommit"] != record["sourceCommit"]
        ):
            raise ValueError("frozen solution promotion has conflicting version, tag, or source")
    else:
        if (
            manifest["module"] != module
            or manifest["version"] != version
            or manifest["source"]["gitTag"] != f"modules/{module}/v{version}"
            or manifest["source"]["commit"] != record["sourceCommit"]
            or any(image["tag"] != version for image in manifest["artifacts"]["images"])
        ):
            raise ValueError("frozen module promotion has conflicting version, tag, or source")


def _save_output(record: dict) -> None:
    output = Path(os.environ.get("BASELINE_OUTPUT", str(ROOT / ".local" / "baseline")))
    output.mkdir(parents=True, exist_ok=True)
    manifest = record["manifest"]
    name = "solution-release.json" if manifest["kind"] == "solution-release" else f"{manifest['module']}.json"
    (output / name).write_text(json.dumps(manifest, indent=2) + "\n")


def _publish_iteration_solution(api: GitHubAPI, bundle: dict) -> None:
    for module in bundle["modules"]:
        for image in module["artifacts"]["images"]:
            baseline.promote_image(image)
        baseline.ensure_tag(api, module["source"]["gitTag"], module["source"]["commit"], module)
        baseline.release(api, module, module["source"]["gitTag"])
    manifest = bundle["solution"]
    baseline.ensure_tag(
        api, manifest["solution"]["gitTag"], manifest["solution"]["sourceCommit"], manifest,
    )
    baseline.release(api, manifest, manifest["solution"]["gitTag"])


def _save_bundle_output(bundle: dict) -> None:
    output = Path(os.environ.get("BASELINE_OUTPUT", str(ROOT / ".local" / "baseline")))
    output.mkdir(parents=True, exist_ok=True)
    (output / "solution-release.json").write_text(json.dumps(bundle["solution"], indent=2) + "\n")
    for module in bundle["modules"]:
        (output / f"{module['module']}.json").write_text(json.dumps(module, indent=2) + "\n")


def run_release() -> None:
    repository = os.environ["GITHUB_REPOSITORY"]
    api = GitHubAPI(repository, os.environ["GH_TOKEN"])
    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise ValueError("release automation must run from trusted main tooling")
    release_choice, module, impact, promote = validate_inputs(
        os.environ.get("RELEASE_CHOICE"), os.environ.get("RELEASE_MODULE"),
        os.environ.get("RELEASE_TYPE"), os.environ.get("RELEASE_PROMOTE"),
    )
    if promote == "stable":
        require_stable_environment(api)
    root = Path(os.environ["SOURCE_ROOT"]).resolve()
    errors = validate_catalog(root)
    if errors:
        raise ValueError("\n".join(errors))
    run_id = os.environ["GITHUB_RUN_ID"]
    path, record = _release_record(api, release_choice, module, impact, promote, root, run_id, repository)
    if promote == "iteration":
        # The legacy baseline bundle is the canonical frozen record; this lightweight
        # request record is not needed for iteration idempotency.
        frozen = _read_record(api, path)
        if frozen is None:
            raise ValueError("iteration baseline was not frozen")
        if release_choice == "solution":
            baseline.verify_artifacts(frozen)
            _publish_iteration_solution(api, frozen)
            _save_bundle_output(frozen)
            print(
                f"Released {frozen['solution']['solution']['gitTag']}; "
                "no artifacts rebuilt."
            )
            return
        else:
            record["manifest"] = frozen["release"]
    else:
        _validate_promoted_record(record)
        baseline.freeze(api, path, record)
    _verify_manifest_artifacts(record["manifest"])
    _publish_record(api, record, prerelease=promote != "stable")
    _save_output(record)
    print(f"Released {record['manifest'].get('solution', {}).get('gitTag') or record['manifest'].get('source', {}).get('gitTag')}; no artifacts rebuilt.")


def _contents(api: GitHubAPI, path: str, ref: str) -> tuple[dict, str]:
    result = api.request(
        "GET", f"/contents/{path}?ref={urllib.parse.quote(ref, safe='')}"
    )
    text = base64.b64decode(result["content"]).decode()
    return _load_yaml_text(text), text


def _load_yaml_text(text: str) -> dict:
    import yaml

    result = yaml.safe_load(text)
    if not isinstance(result, dict):
        raise ValueError("catalog file must contain a YAML mapping")
    return result


def _replace_stable_version(text: str, kind: str, old: str, new: str) -> str:
    if kind == "solution":
        pattern = re.compile(rf"(?m)^(  version:\s*)['\"]?{re.escape(old)}['\"]?(\s*(?:#.*)?)$")
        matches = list(pattern.finditer(text))
        if len(matches) != 1:
            raise ValueError("solution catalog must contain exactly one stable version field")
    else:
        pattern = re.compile(rf"(?m)^(version:\s*)['\"]?{re.escape(old)}['\"]?(\s*(?:#.*)?)$")
        matches = list(pattern.finditer(text))
        if len(matches) != 1:
            raise ValueError("module catalog must contain exactly one stable version field")
    return pattern.sub(rf"\g<1>{new}\g<2>", text, count=1)


def _branch_sha(api: GitHubAPI, branch: str) -> str | None:
    try:
        result = api.request("GET", f"/git/ref/heads/{urllib.parse.quote(branch, safe='/')}")
    except urllib.error.HTTPError as error:
        if error.code == 404:
            error.close()
            return None
        raise
    return result["object"]["sha"]


def _verify_catalog_pr(api: GitHubAPI, pr: dict, expected_path: str, title: str, body: str) -> None:
    if pr["title"] != title or pr["body"] != body or pr["base"]["ref"] != "main":
        raise ValueError("existing stable catalog PR conflicts with the frozen release")
    files = api.request("GET", f"/pulls/{pr['number']}/files?per_page=100")
    if (
        len(files) != 1 or files[0]["filename"] != expected_path
        or files[0]["additions"] != 1 or files[0]["deletions"] != 1
    ):
        raise ValueError("stable catalog PR contains changes beyond the exact version update")


def _verify_catalog_branch_diff(api: GitHubAPI, base_sha: str, branch_sha: str, expected_path: str) -> None:
    comparison = api.request("GET", f"/compare/{base_sha}...{branch_sha}")
    files = comparison.get("files", [])
    if (
        len(files) != 1 or files[0]["filename"] != expected_path
        or files[0]["additions"] != 1 or files[0]["deletions"] != 1
    ):
        raise ValueError("stable catalog branch contains changes beyond the exact version update")


def stable_catalog_pr_body(
    release_choice: str,
    module: str,
    target: str,
    source_commit: str,
    template_path: Path | None = None,
) -> str:
    template = (template_path or ROOT / ".github" / "pull_request_template.md").read_text(
        encoding="utf-8"
    )
    what = (
        f"Record stable version `{target}` after its verified production promotion. "
        "This catalog-only change does not rebuild or retag artifacts."
    )
    scope = "solution" if release_choice == "solution" else f"module `{module}`"
    stage = (
        f"Stage 5 release automation — stable catalog recording for the {scope}.\n\n"
        f"Frozen source: `{source_commit}`. The workflow changes only the stable "
        "`version` field. `make ci` was not run on the generated catalog commit; "
        "normal PR CI remains required."
    )
    body = template.replace("## What and why\n\n", f"## What and why\n\n{what}\n\n", 1)
    body = body.replace("## Stage / issue\n\n", f"## Stage / issue\n\n{stage}\n\n", 1)
    if body == template or body.count("## What and why") != 1 or body.count("## Stage / issue") != 1:
        raise ValueError("repository pull request template is missing expected stable catalog sections")
    return body


def create_stable_catalog_pr() -> None:
    repository = os.environ["GITHUB_REPOSITORY"]
    api = GitHubAPI(repository, os.environ["GH_TOKEN"])
    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise ValueError("stable catalog PR automation must use trusted main tooling")
    require_stable_environment(api)
    run_id = os.environ["GITHUB_RUN_ID"]
    records = list_records(api, "release-promotions")
    matching = [
        record for _, record in records
        if record.get("promote") == "stable" and record.get("requestRunId") == run_id
    ]
    if not matching:
        release_choice, module, impact, _ = validate_inputs(
            os.environ.get("RELEASE_CHOICE"), os.environ.get("RELEASE_MODULE"),
            os.environ.get("RELEASE_TYPE"), "stable",
        )
        _, target, _ = _catalog_target(ROOT, release_choice, module, impact)
        matching = [
            record for _, record in records
            if record.get("promote") == "stable"
            and record.get("releaseChoice") == release_choice
            and record.get("module", "") == module
            and record.get("targetVersion") == target
            and record.get("impact") == impact
        ]
    if len(matching) != 1:
        raise ValueError("stable promotion record for this workflow run is missing or ambiguous")
    record = matching[0]
    release_choice = record["releaseChoice"]
    module = record.get("module", "")
    target = record["targetVersion"]
    base_version = record["baseVersion"]
    path = "solution.yaml" if release_choice == "solution" else f"modules/{module}/module.yaml"
    kind = release_choice
    branch = f"automation/stable-catalog-{_scope(release_choice, module)}-{target}"
    title = f"chore(releases): record {kind} {target} stable version"
    body = stable_catalog_pr_body(
        release_choice, module, target, record["sourceCommit"],
    )
    main_manifest, main_text = _contents(api, path, "main")
    current = main_manifest["solution"]["version"] if release_choice == "solution" else main_manifest["version"]
    if current == target:
        print(f"Stable catalog already records {target}; no PR is required.")
        return
    if current != base_version:
        raise ValueError(
            f"stable catalog conflict: expected {base_version}, found {current}; review before retrying"
        )
    updated = _replace_stable_version(main_text, release_choice, base_version, target)
    branch_ref = _branch_sha(api, branch)
    main_ref = api.request("GET", "/git/ref/heads/main")["object"]["sha"]
    if branch_ref is None:
        try:
            api.request("POST", "/git/refs", {"ref": f"refs/heads/{branch}", "sha": main_ref})
        except urllib.error.HTTPError as error:
            if error.code != 422:
                raise
            error.close()
        branch_ref = _branch_sha(api, branch)
    branch_manifest, branch_text = _contents(api, path, branch)
    if branch_text != updated:
        if branch_manifest == main_manifest:
            result = api.request("GET", f"/contents/{path}?ref={urllib.parse.quote(branch, safe='')}")
            try:
                api.request("PUT", f"/contents/{path}", {
                    "message": f"chore(releases): record {kind} {target} stable version",
                    "branch": branch, "sha": result["sha"],
                    "content": base64.b64encode(updated.encode()).decode(),
                })
            except urllib.error.HTTPError as error:
                if error.code not in (409, 422):
                    raise
                error.close()
            _, branch_text = _contents(api, path, branch)
        if branch_text != updated:
            raise ValueError("stable catalog branch contains a conflicting or additional version change")

    branch_ref = _branch_sha(api, branch)
    if branch_ref is None:
        raise ValueError("stable catalog branch disappeared during update")
    _verify_catalog_branch_diff(api, main_ref, branch_ref, path)

    owner = repository.split("/", 1)[0]
    query = urllib.parse.urlencode({
        "head": f"{owner}:{branch}", "base": "main", "state": "open",
    })
    prs = api.request("GET", f"/pulls?{query}")
    if len(prs) > 1:
        raise ValueError("multiple open stable catalog PRs exist for the same target")
    if prs:
        _verify_catalog_pr(api, prs[0], path, title, body)
        return
    try:
        pr = api.request("POST", "/pulls", {
            "title": title, "head": branch, "base": "main", "body": body, "draft": False,
        })
    except urllib.error.HTTPError as error:
        if error.code not in (409, 422):
            raise
        error.close()
        prs = api.request("GET", f"/pulls?{query}")
        if len(prs) != 1:
            raise ValueError("stable catalog PR creation conflicted and no unique PR was found")
        pr = prs[0]
    _verify_catalog_pr(api, pr, path, title, body)
    print(f"Opened stable catalog PR #{pr['number']} for {target}; main was not modified.")
