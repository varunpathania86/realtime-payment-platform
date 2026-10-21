"""Optimistic GitHub Contents API reservations on a dedicated non-production branch."""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from scripts.module_catalog import _schema_errors
from scripts.release_versions import revision_version

STATE_BRANCH = "automation/release-state"
STATE_PATH = "release-reservations.json"


class GitHubAPI:
    def __init__(self, repository: str, token: str):
        if not token:
            raise ValueError("a GitHub token is required")
        self.base = f"https://api.github.com/repos/{repository}"
        self.token = token

    def request(self, method: str, path: str, data: dict | None = None) -> Any:
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
            },
            method=method,
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)


class GitHubState:
    def __init__(self, api: GitHubAPI):
        self.api = api

    def read(self) -> tuple[dict, str | None]:
        try:
            self.api.request("GET", f"/git/ref/heads/{STATE_BRANCH}")
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
            error.close()
            main = self.api.request("GET", "/git/ref/heads/main")
            try:
                self.api.request(
                    "POST", "/git/refs",
                    {"ref": f"refs/heads/{STATE_BRANCH}", "sha": main["object"]["sha"]},
                )
            except urllib.error.HTTPError as conflict:
                if conflict.code != 422:
                    raise
                conflict.close()
                self.api.request("GET", f"/git/ref/heads/{STATE_BRANCH}")
        try:
            result = self.api.request(
                "GET", f"/contents/{STATE_PATH}?ref={urllib.parse.quote(STATE_BRANCH, safe='')}"
            )
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
            error.close()
            return {"schemaVersion": 1, "reservations": []}, None
        return json.loads(base64.b64decode(result["content"])), result["sha"]

    def write(self, state: dict, sha: str | None) -> bool:
        payload = {
            "message": (
                "chore(releases): reserve or record module artifact\n\n"
                "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
            ),
            "branch": STATE_BRANCH,
            "content": base64.b64encode(
                (json.dumps(state, indent=2, sort_keys=True) + "\n").encode()
            ).decode(),
        }
        if sha is not None:
            payload["sha"] = sha
        try:
            self.api.request("PUT", f"/contents/{STATE_PATH}", payload)
            return True
        except urllib.error.HTTPError as error:
            if error.code not in (409, 422):
                raise
            # Retry only when another writer actually changed the expected file.
            _, current_sha = self.read()
            if current_sha == sha:
                raise
            error.close()
            return False


def update_state(store: Any, change: Callable[[dict], dict]) -> dict:
    for attempt in range(12):
        state, sha = store.read()
        validate_state(state)
        result = change(state)
        validate_state(state)
        if store.write(state, sha):
            return result
        time.sleep(min(0.05 * 2**attempt, 2))
    raise RuntimeError("reservation contention exhausted retries; rerun safely")


def validate_state(state: dict) -> None:
    errors = _schema_errors(state, "release-state.schema.json", "reservation state")
    if errors:
        raise ValueError("\n".join(errors))
    counters: set[tuple] = set()
    sources: set[tuple] = set()
    for entry in state["reservations"]:
        version = revision_version(
            entry["targetVersion"], entry["releaseLine"], entry["iteration"], entry["counter"]
        )
        if entry["reservedVersion"] != version:
            raise ValueError("reservation counter/context does not match its version")
        counter_key = tuple(entry[key] for key in (
            "module", "targetVersion", "iteration", "releaseLine", "counter"
        ))
        source_key = tuple(entry[key] for key in ("module", "releaseLine", "sourceCommit"))
        if counter_key in counters or source_key in sources:
            raise ValueError("duplicate reservation counter or source commit")
        counters.add(counter_key)
        sources.add(source_key)
        publication = entry.get("publication")
        if publication is not None and (
            publication["version"] != version
            or publication["module"] != entry["module"]
            or publication["sourceCommit"] != entry["sourceCommit"]
            or publication["repository"] != f"{entry['registry']}/{entry['module']}"
        ):
            raise ValueError("publication does not match its reserved source, version, or repository")


def reserve(store: Any, context: dict) -> dict:
    def change(state: dict) -> dict:
        entries = state["reservations"]
        existing = [
            entry for entry in entries
            if all(entry[key] == context[key] for key in ("module", "sourceCommit", "releaseLine"))
        ]
        if existing:
            return existing[0]
        keys = ("module", "targetVersion", "iteration", "releaseLine")
        counter = 1 + max(
            (entry["counter"] for entry in entries if all(entry[key] == context[key] for key in keys)),
            default=0,
        )
        entry = {
            **context,
            "counter": counter,
            "reservedVersion": revision_version(
                context["targetVersion"], context["releaseLine"], context["iteration"], counter
            ),
        }
        entries.append(entry)
        return entry
    return update_state(store, change)


def record_publication(store: Any, reservation: dict, publication: dict) -> dict:
    def change(state: dict) -> dict:
        for entry in state["reservations"]:
            if all(entry[key] == reservation[key] for key in ("module", "sourceCommit", "releaseLine")):
                previous = entry.get("publication")
                if previous is not None and previous["digest"] != publication["digest"]:
                    raise ValueError("immutable revision digest cannot be changed")
                if previous is None:
                    entry["publication"] = publication
                return entry
        raise ValueError("publication has no reservation")
    return update_state(store, change)


def github_store() -> GitHubState:
    return GitHubState(GitHubAPI(os.environ["GITHUB_REPOSITORY"], os.environ["GH_TOKEN"]))
