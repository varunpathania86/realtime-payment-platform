"""Plan and publish trusted source artifacts after successful push CI."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import yaml

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
            "chartRegistry": solution["registries"]["charts"],
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


def validate_digest(digest: str) -> None:
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("registry returned an invalid immutable digest")


def chart_metadata(repository: str, digest: str) -> tuple[dict, dict]:
    validate_digest(digest)
    with tempfile.TemporaryDirectory() as directory:
        subprocess.run(
            ["helm", "pull", f"oci://{repository}@{digest}", "--destination", directory],
            check=True,
        )
        archives = list(Path(directory).glob("*.tgz"))
        if len(archives) != 1:
            raise ValueError("expected exactly one chart archive from the registry")
        with tarfile.open(archives[0], "r:gz") as archive:
            metadata = [name for name in archive.getnames() if name.count("/") == 1 and name.endswith("/Chart.yaml")]
            if len(metadata) != 1:
                raise ValueError("published chart has no unique Chart.yaml")
            prefix = metadata[0].removesuffix("Chart.yaml")
            chart_file = archive.extractfile(metadata[0])
            values_file = archive.extractfile(prefix + "values.yaml")
            if chart_file is None or values_file is None:
                raise ValueError("published chart metadata or values are missing")
            metadata, values = yaml.safe_load(chart_file), yaml.safe_load(values_file)
            if not isinstance(metadata, dict) or not isinstance(values, dict):
                raise ValueError("published chart metadata and values must be mappings")
            return metadata, values


def publish_chart(
    source: Path, context: dict, reservation: dict, version: str, image_repository: str, image_digest: str
) -> dict | None:
    chart_source = source / "modules" / context["module"] / "chart"
    saved = reservation.get("publication", {}).get("chart")
    if not (chart_source / "Chart.yaml").is_file():
        if saved:
            raise ValueError("recorded chart is missing from its tested source")
        return None
    registry = reservation.get("chartRegistry")
    if registry is None and reservation.get("publication") and not saved:
        print(f"Reuse legacy image-only reservation for {context['module']}; no chart registry was reserved.", flush=True)
        return None
    if not registry or not re.fullmatch(r"oci://ghcr\.io/[a-z0-9][a-z0-9/_-]*", registry):
        raise ValueError("chart publication requires a reserved explicit OCI GHCR chart registry")
    repository = f"{registry.removeprefix('oci://')}/{context['module']}"
    existing = registry_manifest(repository, version)
    if saved and (
        saved["repository"] != repository or saved["version"] != version
        or existing is None or saved["digest"] != existing[0]
    ):
        raise ValueError("published immutable chart is missing or its digest has changed; recovery required")
    if existing:
        metadata, values = chart_metadata(repository, existing[0])
        annotations = metadata.get("annotations", {})
        if annotations.get("org.opencontainers.image.ref.name") != context["branch"]:
            raise ValueError("chart branch hash collision or release-line mismatch")
        same_source = annotations.get("org.opencontainers.image.revision") == context["sourceCommit"]
        if context["integrated"] and not same_source:
            raise ValueError("immutable chart version already belongs to another source commit")
        if same_source:
            expected = {"repository": image_repository, "digest": image_digest, "tag": version, "pullPolicy": "IfNotPresent"}
            if metadata.get("name") != context["module"] or metadata.get("version") != version or values.get("image") != expected:
                raise ValueError("existing chart does not reference the approved image digest/version")
            validate_digest(existing[0])
            return {"repository": repository, "version": version, "digest": existing[0]}
    with tempfile.TemporaryDirectory() as directory:
        staging = Path(directory) / context["module"]
        shutil.copytree(chart_source, staging)
        metadata = _load_yaml(staging / "Chart.yaml")
        if metadata.get("name") != context["module"]:
            raise ValueError("chart name must match its module identity")
        metadata.update({"version": version, "appVersion": version})
        metadata.setdefault("annotations", {}).update({
            "org.opencontainers.image.revision": context["sourceCommit"],
            "org.opencontainers.image.ref.name": context["branch"],
        })
        (staging / "Chart.yaml").write_text(yaml.safe_dump(metadata, sort_keys=False))
        values = _load_yaml(staging / "values.yaml")
        values["image"] = {
            "repository": image_repository, "tag": version, "digest": image_digest, "pullPolicy": "IfNotPresent",
        }
        (staging / "values.yaml").write_text(yaml.safe_dump(values, sort_keys=False))
        subprocess.run(["helm", "lint", str(staging), "--strict"], check=True)
        rendered = subprocess.run(
            ["helm", "template", context["module"], str(staging)],
            check=True, capture_output=True, text=True,
        ).stdout
        images = [
            container["image"]
            for document in yaml.safe_load_all(rendered)
            if document and document.get("kind") == "Deployment"
            for container in document["spec"]["template"]["spec"]["containers"]
        ]
        if not images or any(image != f"{image_repository}@{image_digest}" for image in images):
            raise ValueError("published chart must render the approved image by immutable digest")
        subprocess.run(["helm", "package", str(staging), "--destination", directory], check=True)
        archive = Path(directory) / f"{context['module']}-{version}.tgz"
        pushed = subprocess.run(
            ["helm", "push", str(archive), registry],
            check=True, capture_output=True, text=True,
        )
        match = re.search(r"Digest:\s*(sha256:[a-f0-9]{64})", pushed.stdout + pushed.stderr)
        if match is None:
            raise ValueError("Helm push did not report the chart OCI digest")
        digest = match[1]
    verified = registry_manifest(repository, version)
    if verified is None or verified[0] != digest:
        raise ValueError("registry chart digest verification failed after publication")
    return {"repository": repository, "version": version, "digest": digest}


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
            pins = dict(
                line.split() for line in (source / ".tool-versions").read_text().splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            )
            subprocess.run([
                "docker", "buildx", "build",
                "--build-arg", f"GO_VERSION={pins['go']}",
                "--build-arg", f"NODE_VERSION={pins['nodejs']}",
                "--build-arg", f"VERSION={version}",
                "--build-arg", f"COMMIT={context['sourceCommit']}",
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
    validate_digest(digest)
    chart = publish_chart(source, context, reservation, version, repository, digest)
    publication = {
        "schemaVersion": 1, "module": context["module"], "version": version,
        "sourceCommit": context["sourceCommit"], "branch": context["branch"],
        "approvedImpact": reservation["approvedImpact"],
        "repository": repository, "digest": digest,
        "workflowRun": f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}",
        "testRun": os.environ["SOURCE_RUN_URL"],
        "sbom": f"{repository}@{digest}", "provenance": f"{repository}@{digest}",
    }
    if chart is not None:
        publication["chart"] = chart
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
    except (ValueError, OSError, subprocess.CalledProcessError, KeyError, yaml.YAMLError, tarfile.TarError) as error:
        raise SystemExit(f"ERROR {error}") from error


if __name__ == "__main__":
    main()
