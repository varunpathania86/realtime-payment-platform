#!/usr/bin/env bash
# Shared checksum-verified downloads; callers provide TMP and source scripts/lib.sh.

fetch() { curl -fsSL --retry 3 -o "$2" "$1"; }

verify() {
  [[ "$2" =~ ^[a-fA-F0-9]{64}$ ]] || { echo "Invalid or missing SHA-256 for $(basename "$1")" >&2; exit 1; }
  echo "$2  $1" | sha256sum --check --status || { echo "Checksum mismatch for $(basename "$1")" >&2; exit 1; }
}

github_asset_metadata() { # owner/repo tag asset
  local response="$TMP/github-release.json" token="${GH_TOKEN:-${GITHUB_TOKEN:-}}"
  local headers=(-H "Accept: application/vnd.github+json")
  if [ -n "$token" ]; then
    headers+=(-H "Authorization: Bearer $token")
  fi
  if ! curl -fsSL --retry 3 "${headers[@]}" \
    -o "$response" "https://api.github.com/repos/$1/releases/tags/$2"; then
    echo "Cannot fetch checksum metadata for $1 release $2. Check GitHub API access/rate limits; CI must pass GH_TOKEN." >&2
    return 1
  fi
  python3 - "$response" "$3" <<'PY'
import json
import re
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as stream:
        assets = json.load(stream)["assets"]
    asset = next(asset for asset in assets if asset["name"] == sys.argv[2])
    digest = asset.get("digest") or ""
    if not re.fullmatch(r"sha256:[a-fA-F0-9]{64}", digest):
        raise ValueError("missing or invalid SHA-256 digest")
    asset_id = asset.get("id")
    if isinstance(asset_id, bool) or not isinstance(asset_id, int) or asset_id < 1:
        raise ValueError("missing or invalid asset ID")
except (OSError, ValueError, KeyError, TypeError, StopIteration) as error:
    sys.exit(f"Invalid GitHub checksum metadata for {sys.argv[2]}: {error}")
print(digest.removeprefix("sha256:"), asset_id)
PY
}

fetch_github_asset() { # owner/repo tag asset destination
  local metadata digest asset_id token="${GH_TOKEN:-${GITHUB_TOKEN:-}}"
  metadata=$(github_asset_metadata "$1" "$2" "$3") || return 1
  read -r digest asset_id <<< "$metadata"
  if ! fetch "https://github.com/$1/releases/download/$2/$3" "$4"; then
    echo "Release download failed for $1 $2 $3; retrying through the official GitHub asset API." >&2
    local headers=(-H "Accept: application/octet-stream")
    if [ -n "$token" ]; then
      headers+=(-H "Authorization: Bearer $token")
    fi
    if ! curl -fsSL --retry 3 "${headers[@]}" \
      -o "$4" "https://api.github.com/repos/$1/releases/assets/$asset_id"; then
      echo "Cannot download $1 release $2 asset $3 through either official endpoint." >&2
      return 1
    fi
  fi
  verify "$4" "$digest"
}
