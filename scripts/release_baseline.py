"""Freeze a tested main solution baseline without rebuilding or deploying artifacts."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from datetime import datetime, timezone
import urllib.error
import urllib.parse
import urllib.request

from scripts.module_catalog import ROOT, _load_yaml, _schema_errors, plan_matrix, validate_catalog
from scripts.publish_modules import chart_metadata, registry_manifest, validate_digest
from scripts.release_state import GitHubAPI, GitHubState, STATE_BRANCH, validate_state
from scripts.release_versions import STABLE, active_iteration


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True,
    ).stdout.strip()


def ancestor(root: Path, older: str, newer: str) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", older, newer], cwd=root, check=False,
        capture_output=True,
    )
    if result.returncode not in (0, 1):
        raise ValueError("cannot establish publication ancestry")
    return result.returncode == 0


def baseline_version(version: str) -> tuple[str, int]:
    match = re.fullmatch(rf"({STABLE})-it([1-9][0-9]*)", version)
    if not match:
        raise ValueError("use an iteration baseline such as 0.1.0-it1, not a branch, revision or RC")
    return match[1], int(match[5])


def schema(document: dict, name: str) -> None:
    errors = _schema_errors(document, name, "baseline release")
    if errors:
        raise ValueError("\n".join(errors))


def select_publications(root: Path, state: dict, source: str) -> list[dict]:
    validate_state(state)
    catalog = _load_yaml(root / "solution.yaml")
    selected = []
    for name in catalog["modules"]:
        module = root / "modules" / name
        if not (module / "Dockerfile").exists():
            continue
        candidates = [
            entry for entry in state["reservations"]
            if entry["module"] == name and entry["releaseLine"] == "main"
            and entry["branch"] == "main" and "publication" in entry
            and ancestor(root, entry["sourceCommit"], source)
        ]
        if not candidates:
            raise ValueError(f"{name}: no published main revision at or before selected source")
        # Main ancestry, not per-target counters or wall-clock timestamps, orders revisions.
        chosen = candidates[0]
        for candidate in candidates[1:]:
            if ancestor(root, chosen["sourceCommit"], candidate["sourceCommit"]):
                chosen = candidate
        changed = git(
            root, "diff", "--name-only", chosen["sourceCommit"], source, "--", ".tool-versions",
        )
        affected = plan_matrix(chosen["sourceCommit"], source, root)["include"]
        if changed or any(item["module"] == name for item in affected):
            scope = "toolchain pins" if changed else "module or dependency source"
            raise ValueError(
                f"publication pending for {source}: {name} has {scope} changes since "
                f"published source {chosen['sourceCommit']}; publish the selected source first"
            )
        publication = chosen["publication"]
        if (module / "chart" / "Chart.yaml").exists() and "chart" not in publication:
            raise ValueError(f"{name}: chart publication is incomplete")
        selected.append(chosen)
    if not selected:
        raise ValueError("no packaged catalog modules to release")
    return selected


def build_bundle(
    root: Path, selected: list[dict], version: str, source: str,
    repository: str, run_url: str, now: datetime,
) -> dict:
    target, iteration = baseline_version(version)
    catalog = _load_yaml(root / "solution.yaml")
    if iteration != active_iteration(catalog["pi"], now):
        raise ValueError("new baselines must use the current UTC iteration")
    if tuple(map(int, target.split("."))) <= tuple(map(int, catalog["solution"]["version"].split("."))):
        raise ValueError("solution target must exceed the stable production catalog version")
    modules = []
    solution_modules = []
    for entry in selected:
        publication = entry["publication"]
        module_version = f"{entry['targetVersion']}-it{iteration}"
        artifacts = {
            "images": [{
                "repository": publication["repository"], "tag": module_version,
                "digest": publication["digest"],
            }],
            # Preserve package bytes: never rewrite Chart.yaml or its pinned image.
            "charts": [publication["chart"]] if "chart" in publication else [],
        }
        manifest = {
            "schemaVersion": 1, "kind": "module-release", "module": entry["module"],
            "version": module_version,
            "source": {
                "repository": f"https://github.com/{repository}", "commit": entry["sourceCommit"],
                "gitTag": f"modules/{entry['module']}/v{module_version}",
            },
            "workflow": {"runId": run_url.rsplit("/", 1)[-1], "url": run_url},
            "artifacts": artifacts,
            "evidence": {
                "tests": [publication["testRun"], publication["workflowRun"]],
                "sbom": publication["sbom"], "provenance": publication["provenance"],
                "approvedImpact": entry["approvedImpact"],
                "qa": [f"Promoted integrated revision {publication['version']} without rebuilding"],
            },
        }
        schema(manifest, "module-release.schema.json")
        modules.append(manifest)
        solution_modules.append({
            "name": entry["module"], "version": module_version, "sourceCommit": entry["sourceCommit"],
            "artifacts": artifacts, "workflowRun": run_url, "approvedImpact": entry["approvedImpact"],
        })
    solution = {
        "schemaVersion": 1, "kind": "solution-release", "pi": {"id": catalog["pi"]["id"]},
        "solution": {"version": version, "sourceCommit": source, "gitTag": f"solution/v{version}"},
        "modules": solution_modules,
        "qa": {"evidence": [
            "Frozen tested main publications; no rebuild, deployment, schedule advance or stable-version update",
            "Catalog-only modules have no runtime artifact and are not part of this artifact baseline",
        ], "runForRecord": run_url},
    }
    schema(solution, "solution-release.schema.json")
    return {"solution": solution, "modules": modules}


def read_record(api: GitHubAPI, path: str) -> dict | None:
    try:
        result = api.request("GET", f"/contents/{path}?ref={urllib.parse.quote(STATE_BRANCH, safe='')}")
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        error.close()
        return None
    return json.loads(base64.b64decode(result["content"]))


def freeze(api: GitHubAPI, path: str, bundle: dict) -> dict:
    existing = read_record(api, path)
    if existing is not None:
        if existing != bundle:
            raise ValueError("immutable baseline selection conflicts with the existing record")
        return existing
    try:
        api.request("PUT", f"/contents/{path}", {
            "message": (
                "chore(releases): freeze iteration baseline\n\n"
                "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
            ),
            "branch": STATE_BRANCH,
            "content": base64.b64encode((json.dumps(bundle, indent=2, sort_keys=True) + "\n").encode()).decode(),
        })
    except urllib.error.HTTPError as error:
        if error.code not in (409, 422):
            raise
        error.close()
        if read_record(api, path) != bundle:
            raise ValueError("concurrent baseline selection conflict") from None
    return bundle


def require_build(api: GitHubAPI, repository: str, source: str) -> None:
    runs = api.request("GET", f"/actions/workflows/build.yml/runs?head_sha={source}&event=push&per_page=100")
    if not any(
        run["conclusion"] == "success" and run["head_sha"] == source
        and run["head_branch"] == "main"
        and run["head_repository"]["full_name"] == repository for run in runs["workflow_runs"]
    ):
        raise ValueError(f"{source}: no successful same-repository main push build")


def require_publication(
    api: GitHubAPI, repository: str, url: str, expected_source: str | None = None,
) -> None:
    prefix = f"https://github.com/{repository}/actions/runs/"
    if not url.startswith(prefix) or not url.removeprefix(prefix).isdigit():
        raise ValueError("publication evidence must identify a run in this repository")
    run = api.request("GET", f"/actions/runs/{url.removeprefix(prefix)}")
    if (run["conclusion"] != "success" or run["event"] != "workflow_run"
            or run["path"] != ".github/workflows/publish.yml"
            or run["head_repository"]["full_name"] != repository):
        raise ValueError("selected publication workflow did not complete successfully")
    if expected_source is not None and (
        run.get("head_sha") != expected_source or run.get("head_branch") != "main"
    ):
        raise ValueError("selected publication did not publish the exact reviewed main source")


def verify_artifacts(bundle: dict) -> None:
    schema(bundle["solution"], "solution-release.schema.json")
    solution = bundle["solution"]
    _, iteration = baseline_version(solution["solution"]["version"])
    expected = []
    names = set()
    for module in bundle["modules"]:
        schema(module, "module-release.schema.json")
        _, module_iteration = baseline_version(module["version"])
        if (module["module"] in names or module_iteration != iteration
                or module["source"]["gitTag"] != f"modules/{module['module']}/v{module['version']}"):
            raise ValueError("invalid or duplicate frozen module baseline identity")
        names.add(module["module"])
        verify_artifact_set(module["module"], module["version"], module["artifacts"])
        expected.append({
            "name": module["module"], "version": module["version"],
            "sourceCommit": module["source"]["commit"], "artifacts": module["artifacts"],
            "workflowRun": module["workflow"]["url"], "approvedImpact": module["evidence"]["approvedImpact"],
        })
    if expected != solution["modules"] or solution["solution"]["gitTag"] != f"solution/v{solution['solution']['version']}":
        raise ValueError("solution manifest does not match its frozen module manifests")


def verify_artifact_set(module_name: str, version: str, artifacts: dict) -> None:
    if len(artifacts["images"]) != 1 or len(artifacts["charts"]) > 1:
        raise ValueError("expected one module image and at most one chart")
    image = artifacts["images"][0]
    if image["tag"] != version:
        raise ValueError("baseline image tag does not match the module version")
    validate_digest(image["digest"])
    found = registry_manifest(image["repository"], image["digest"])
    if found is None or found[0] != image["digest"]:
        raise ValueError(f"{module_name}: source image digest is missing")
    for chart in artifacts["charts"]:
        validate_digest(chart["digest"])
        found = registry_manifest(chart["repository"], chart["digest"])
        if found is None or found[0] != chart["digest"]:
            raise ValueError("source chart digest is missing")
        metadata, values = chart_metadata(chart["repository"], chart["digest"])
        if (metadata.get("name") != module_name or metadata.get("version") != chart["version"]
                or values.get("image", {}).get("repository") != image["repository"]
                or values.get("image", {}).get("digest") != image["digest"]):
            raise ValueError("chart does not pin the selected image or package identity")


def verify_module_artifacts(module: dict) -> None:
    schema(module, "module-release.schema.json")
    verify_artifact_set(module["module"], module["version"], module["artifacts"])


def promote_image(image: dict) -> None:
    existing = registry_manifest(image["repository"], image["tag"])
    if existing is not None:
        if existing[0] != image["digest"]:
            raise ValueError("refusing to overwrite an existing baseline image tag")
        return
    path = image["repository"].removeprefix("ghcr.io/")
    auth = base64.b64encode(f"{os.environ['GITHUB_ACTOR']}:{os.environ['GH_TOKEN']}".encode()).decode()
    request = urllib.request.Request(
        "https://ghcr.io/token?" + urllib.parse.urlencode({
            "service": "ghcr.io", "scope": f"repository:{path}:pull,push",
        }), headers={"Authorization": f"Basic {auth}"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        token = json.load(response)["token"]
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json",
    }
    request = urllib.request.Request(
        f"https://ghcr.io/v2/{path}/manifests/{image['digest']}", headers=headers,
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = response.read()
        content_type = response.headers["Content-Type"]
    if "sha256:" + hashlib.sha256(raw).hexdigest() != image["digest"]:
        raise ValueError("downloaded manifest bytes do not match the recorded digest")
    request = urllib.request.Request(
        f"https://ghcr.io/v2/{path}/manifests/{image['tag']}", data=raw, method="PUT",
        headers={"Authorization": f"Bearer {token}", "Content-Type": content_type},
    )
    with urllib.request.urlopen(request, timeout=60):
        pass
    verified = registry_manifest(image["repository"], image["tag"])
    if verified is None or verified[0] != image["digest"]:
        raise ValueError("promoted baseline digest verification failed")


def ensure_tag(api: GitHubAPI, tag: str, commit: str, manifest: dict) -> None:
    path = urllib.parse.quote(tag, safe="/")
    message = json.dumps(manifest, sort_keys=True)
    try:
        existing = api.request("GET", f"/git/ref/tags/{path}")
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        error.close()
        obj = api.request("POST", "/git/tags", {
            "tag": tag, "message": message, "object": commit, "type": "commit",
        })
        try:
            api.request("POST", "/git/refs", {"ref": f"refs/tags/{tag}", "sha": obj["sha"]})
            return
        except urllib.error.HTTPError as conflict:
            if conflict.code != 422:
                raise
            conflict.close()
            existing = api.request("GET", f"/git/ref/tags/{path}")
    if existing["object"]["type"] != "tag":
        raise ValueError("existing baseline tag is not an immutable annotated release tag")
    obj = api.request("GET", f"/git/tags/{existing['object']['sha']}")
    if (obj["object"]["type"] != "commit" or obj["object"]["sha"] != commit
            or obj["message"].rstrip("\n") != message):
        raise ValueError("existing baseline tag conflicts with the frozen manifest")


def release(api: GitHubAPI, manifest: dict, tag: str, prerelease: bool = True) -> None:
    version = (
        manifest["solution"]["version"]
        if manifest["kind"] == "solution-release" else manifest["version"]
    )
    if "-it" in version:
        description = "Immutable iteration baseline. No rebuild or deployment."
    elif "-rc" in version:
        description = "Immutable release candidate. No rebuild or deployment."
    else:
        description = "Immutable stable release. No rebuild or deployment."
    body = description + "\n\n```json\n" + json.dumps(
        manifest, indent=2, sort_keys=True,
    ) + "\n```\n"
    try:
        existing = api.request("GET", f"/releases/tags/{urllib.parse.quote(tag, safe='')}")
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        error.close()
        api.request("POST", "/releases", {
            "tag_name": tag, "name": tag, "body": body, "prerelease": prerelease,
            "draft": False, "make_latest": "false",
        })
        return
    if existing["body"] != body or existing["prerelease"] != prerelease or existing["draft"]:
        raise ValueError("existing GitHub release conflicts with the frozen manifest")


def main() -> None:
    if os.environ.get("RELEASE_ACTION") == "catalog-pr":
        from scripts.release_promotion import create_stable_catalog_pr

        try:
            create_stable_catalog_pr()
        except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
            sys.exit(f"ERROR stable catalog PR: {error}")
        return
    if os.environ.get("RELEASE_CHOICE"):
        from scripts.release_promotion import run_release

        try:
            run_release()
        except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
            sys.exit(f"ERROR release automation: {error}")
        return
    try:
        repository = os.environ["GITHUB_REPOSITORY"]
        api = GitHubAPI(repository, os.environ["GH_TOKEN"])
        if os.environ["GITHUB_REF"] != "refs/heads/main":
            raise ValueError("baseline release must run from trusted main tooling")
        source = os.environ["BASELINE_SOURCE"]
        if not re.fullmatch(r"[a-f0-9]{40}", source):
            raise ValueError("BASELINE_SOURCE must be a full lowercase commit SHA")
        version = os.environ["BASELINE_VERSION"]
        baseline_version(version)
        root = Path(os.environ["SOURCE_ROOT"]).resolve()
        if git(root, "rev-parse", "HEAD") != source or not ancestor(root, source, "origin/main"):
            raise ValueError("baseline source must be a commit on main")
        errors = validate_catalog(root)
        if errors:
            raise ValueError("\n".join(errors))
        require_build(api, repository, source)
        path = f"release-baselines/solution-{version}.json"
        bundle = read_record(api, path)
        if bundle is None:
            state, _ = GitHubState(api).read()
            selected = select_publications(root, state, source)
            for entry in selected:
                require_build(api, repository, entry["sourceCommit"])
                require_publication(api, repository, entry["publication"]["workflowRun"])
            run = f"https://github.com/{repository}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
            bundle = build_bundle(root, selected, version, source, repository, run, datetime.now(timezone.utc))
            verify_artifacts(bundle)
            bundle = freeze(api, path, bundle)
        if (bundle["solution"]["solution"]["sourceCommit"] != source
                or bundle["solution"]["solution"]["version"] != version):
            raise ValueError("baseline version is already frozen for a different source")
        schema(bundle["solution"], "solution-release.schema.json")
        verify_artifacts(bundle)
        for module in bundle["modules"]:
            for image in module["artifacts"]["images"]:
                promote_image(image)
            ensure_tag(api, module["source"]["gitTag"], module["source"]["commit"], module)
            release(api, module, module["source"]["gitTag"])
        solution = bundle["solution"]
        ensure_tag(api, solution["solution"]["gitTag"], source, solution)
        release(api, solution, solution["solution"]["gitTag"])
        output = Path(os.environ.get("BASELINE_OUTPUT", str(ROOT / ".local" / "baseline")))
        output.mkdir(parents=True, exist_ok=True)
        (output / "solution-release.json").write_text(json.dumps(solution, indent=2) + "\n")
        for module in bundle["modules"]:
            (output / f"{module['module']}.json").write_text(json.dumps(module, indent=2) + "\n")
        print(f"Released {solution['solution']['gitTag']}; PI schedule and stable catalog versions unchanged.")
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(f"ERROR baseline release: {error}")


if __name__ == "__main__":
    main()
