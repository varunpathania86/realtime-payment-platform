"""Plan and publish trusted source artifacts after successful push CI."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from scripts.module_catalog import ROOT, _load_yaml, _module_manifests, _schema_errors, plan_matrix, validate_catalog
from scripts.release_state import GitHubAPI, github_store, record_publication, reserve
from scripts.release_versions import (
    RC_BRANCH, active_iteration, branch_version, classify_rc_branch, normalized_branch, target_version,
)


def publication_matrix(
    source: Path, base: str, commit: str, branch: str, pr_targets: list[str], now: datetime
) -> dict:
    errors = validate_catalog(source)
    if errors:
        raise ValueError("\n".join(errors))
    selected = plan_matrix(base, commit, source)["include"]
    modules = _module_manifests(source)
    solution = _load_yaml(source / "solution.yaml")
    policy = _load_yaml(ROOT / "releases" / "impacts.yaml")
    policy_errors = _schema_errors(policy, "impacts.schema.json", "approved impact policy")
    if policy_errors:
        raise ValueError("\n".join(policy_errors))
    if set(policy["modules"]) - set(modules):
        raise ValueError("impact policy references an unknown module")
    rc_branch = classify_rc_branch(source, branch, commit, pr_targets)
    rc = RC_BRANCH.fullmatch(rc_branch) if rc_branch else None
    if rc:
        paths = subprocess.run(
            ["git", "diff", "--name-only", f"origin/{rc_branch}...{commit}"],
            cwd=source, check=True, capture_output=True, text=True,
        ).stdout.splitlines()
        changed_modules = {path.split("/")[1] for path in paths if path.startswith("modules/")}
        if changed_modules - {rc.group(1)}:
            raise ValueError("RC-fix branches must modify only their own module")
    result = []
    for entry in selected:
        name = entry["module"]
        if not (source / "modules" / name / "Dockerfile").is_file():
            print(f"skip {name}: no OCI image packaging", flush=True)
            continue
        if rc and name != rc.group(1):
            continue
        impact = policy["modules"].get(name, policy["defaultImpact"])
        target = rc.group(2) if rc else target_version(modules[name][1]["version"], [impact])
        iteration = None if rc else active_iteration(solution["pi"], now)
        line = f"rc{rc.group(6)}" if rc else "main"
        result.append({
            "module": name, "targetVersion": target, "iteration": iteration,
            "releaseLine": line, "sourceCommit": commit, "branch": normalized_branch(branch),
            "integrated": branch == "main", "approvedImpact": impact,
            "registry": solution["registries"]["images"],
        })
    return {"include": result}


def registry_manifest(repository: str, version: str) -> tuple[str, dict] | None:
    if not re.fullmatch(r"ghcr\.io/[a-z0-9][a-z0-9./_-]*", repository):
        raise ValueError("publication requires an explicit lowercase GHCR repository")
    path = repository.removeprefix("ghcr.io/")
    auth = base64.b64encode(
        f"{os.environ['GITHUB_ACTOR']}:{os.environ['GH_TOKEN']}".encode()
    ).decode()
    token_request = urllib.request.Request(
        "https://ghcr.io/token?" + urllib.parse.urlencode({
            "service": "ghcr.io", "scope": f"repository:{path}:pull,push",
        }),
        headers={"Authorization": f"Basic {auth}"},
    )
    with urllib.request.urlopen(token_request, timeout=60) as response:
        token = json.load(response)["token"]
    request = urllib.request.Request(
        f"https://ghcr.io/v2/{path}/manifests/{version}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.headers["Docker-Content-Digest"], json.load(response)
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        error.close()
        return None


def publish(source: Path, context: dict, output: Path) -> dict:
    integrated = context["integrated"]
    if not integrated:
        api = GitHubAPI(os.environ["GITHUB_REPOSITORY"], os.environ["GH_TOKEN"])
        tip = api.request("GET", f"/git/ref/heads/{urllib.parse.quote(context['branch'], safe='/')}")
        if tip["object"]["sha"] != context["sourceCommit"]:
            raise ValueError("branch has advanced; refusing to overwrite its tag with a stale build")
    store = github_store() if integrated else None
    reservation = reserve(store, context) if integrated else context
    version = reservation["reservedVersion"] if integrated else branch_version(
        context["targetVersion"], context["releaseLine"], context["iteration"], context["branch"]
    )
    repository = f"{reservation['registry']}/{context['module']}"
    existing = registry_manifest(repository, version)
    annotations = existing[1].get("annotations", {}) if existing else {}
    if existing:
        if integrated and annotations.get("org.opencontainers.image.revision") != context["sourceCommit"]:
            raise ValueError("immutable revision tag already belongs to another source commit")
        if not integrated and annotations.get("org.opencontainers.image.ref.name") != context["branch"]:
            raise ValueError("four-character branch hash collision; refusing to overwrite another branch")
    saved = reservation.get("publication")
    if saved and (existing is None or existing[0] != saved["digest"]):
        raise ValueError("published immutable artifact is missing or its digest has changed; recovery required")
    if existing and annotations.get("org.opencontainers.image.revision") == context["sourceCommit"]:
        digest = existing[0]
    else:
        with tempfile.TemporaryDirectory() as directory:
            metadata = Path(directory) / "metadata.json"
            subprocess.run([
                "docker", "buildx", "build",
                "--file", str(source / "modules" / context["module"] / "Dockerfile"),
                "--tag", f"{repository}:{version}", "--push", "--platform", "linux/amd64",
                "--provenance=mode=max", "--sbom=true",
                "--annotation", f"index:org.opencontainers.image.revision={context['sourceCommit']}",
                "--annotation", f"index:org.opencontainers.image.ref.name={context['branch']}",
                "--annotation", f"index:org.opencontainers.image.version={version}",
                "--metadata-file", str(metadata),
                str(source / "modules" / context["module"]),
            ], check=True)
            digest = json.loads(metadata.read_text(encoding="utf-8"))["containerimage.digest"]
        verified = registry_manifest(repository, version)
        if verified is None or verified[0] != digest:
            raise ValueError("registry digest verification failed after publication")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("registry returned an invalid immutable digest")
    publication = {
        "schemaVersion": 1, "module": context["module"], "version": version,
        "sourceCommit": context["sourceCommit"], "branch": context["branch"],
        "approvedImpact": reservation["approvedImpact"],
        "repository": repository, "digest": digest,
        "workflowRun": f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}",
        "testRun": os.environ["SOURCE_RUN_URL"],
        "sbom": f"{repository}@{digest}", "provenance": f"{repository}@{digest}",
    }
    if integrated:
        record_publication(store, reservation, publication)
    output.mkdir(parents=True, exist_ok=True)
    (output / f"{context['module']}.json").write_text(
        json.dumps(publication, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Published {repository}:{version} -> {digest}", flush=True)
    return publication


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["plan", "publish"])
    args = parser.parse_args()
    source = Path(os.environ["SOURCE_ROOT"]).resolve()
    try:
        if args.action == "plan":
            api = GitHubAPI(os.environ["GITHUB_REPOSITORY"], os.environ["GH_TOKEN"])
            run = api.request("GET", f"/actions/runs/{os.environ['SOURCE_RUN_ID']}")
            if run["event"] != "push" or run["conclusion"] != "success":
                raise ValueError("only successful push CI can publish")
            if run["path"] != ".github/workflows/build.yml":
                raise ValueError("publication requires the repository's module CI workflow")
            if run["head_repository"]["full_name"] != os.environ["GITHUB_REPOSITORY"]:
                raise ValueError("fork publication is forbidden")
            branch, commit = run["head_branch"], run["head_sha"]
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=source, check=True, capture_output=True, text=True
            ).stdout.strip()
            if head != commit:
                raise ValueError("source checkout does not match the tested commit")
            # Main publishes the integrated commit; branches publish their cumulative main diff.
            base = subprocess.run(
                ["git", "rev-parse", f"{commit}^"] if branch == "main"
                else ["git", "merge-base", "origin/main", commit],
                cwd=source, check=True, capture_output=True, text=True,
            ).stdout.strip()
            pulls = api.request("GET", "/pulls?" + urllib.parse.urlencode({
                "state": "open", "head": f"{os.environ['GITHUB_REPOSITORY'].split('/')[0]}:{branch}",
                "per_page": 100,
            }))
            if len(pulls) == 100:
                raise ValueError("too many PR targets; cannot classify safely")
            matrix = publication_matrix(
                source, base, commit, branch, [pr["base"]["ref"] for pr in pulls], datetime.now(timezone.utc)
            )
            with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
                output.write(f"matrix={json.dumps(matrix, separators=(',', ':'))}\n")
                output.write(f"has_modules={'true' if matrix['include'] else 'false'}\n")
        else:
            publish(source, json.loads(os.environ["PUBLICATION_CONTEXT"]), Path(os.environ["PUBLICATION_OUTPUT"]))
    except (ValueError, OSError, subprocess.CalledProcessError, KeyError) as error:
        raise SystemExit(f"ERROR {error}") from error


if __name__ == "__main__":
    main()
