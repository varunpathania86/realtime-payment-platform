#!/usr/bin/env bash
# Register this machine as a GitHub Actions self-hosted runner ("agent") for this repository.
# Usage: scripts/agent.sh setup|remove|status
# Settings (environment): AGENT_DIR, AGENT_NAME (default rpp-<first label>), AGENT_LABELS (default "local"), AGENT_SERVICE=0 to skip
# the systemd service, RUNNER_TOKEN to supply a registration token instead of using the gh CLI.
set -euo pipefail
# shellcheck source=scripts/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

remote=$(git -C "$REPO_ROOT" remote get-url origin)
slug=$(printf '%s' "$remote" | sed -E 's#^(git@github\.com:|https://github\.com/)##; s#\.git$##')
repo_url="https://github.com/$slug"
AGENT_DIR="${AGENT_DIR:-$HOME/actions-runner/${slug//\//-}}"
AGENT_LABELS="${AGENT_LABELS:-local}"
# Neutral default name: the hostname is public in workflow logs and can reveal an employer's naming scheme.
AGENT_NAME="${AGENT_NAME:-rpp-${AGENT_LABELS%%,*}}"
AGENT_SERVICE="${AGENT_SERVICE:-1}"

# A registration (or removal) token is short-lived. Prefer the gh CLI; fall back to RUNNER_TOKEN or a prompt.
get_token() { # registration-token | remove-token
  if [ -n "${RUNNER_TOKEN:-}" ]; then
    echo "$RUNNER_TOKEN"
  elif command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
    gh api -X POST "repos/$slug/actions/runners/$1" --jq .token
  else
    read -r -s -p "Paste the token from $repo_url/settings/actions/runners/new: " t </dev/tty
    echo >&2
    echo "$t"
  fi
}

has_systemd() { [ -d /run/systemd/system ]; }

download_runner() {
  local v asset arch tmp digest
  v=$(pinned_version actions-runner)
  [ "$ARCH" = amd64 ] && arch=x64 || arch=arm64
  asset="actions-runner-linux-$arch-$v.tar.gz"
  tmp=$(mktemp -d)
  trap 'rm -rf "$tmp"' RETURN
  echo "Downloading runner $v"
  curl -fsSL --retry 3 -o "$tmp/$asset" "https://github.com/actions/runner/releases/download/v$v/$asset"
  digest=$(curl -fsSL "https://api.github.com/repos/actions/runner/releases/tags/v$v" |
    python3 -c 'import sys, json; a = {x["name"]: x.get("digest") or "" for x in json.load(sys.stdin)["assets"]}; print(a[sys.argv[1]].removeprefix("sha256:"))' "$asset")
  echo "$digest  $tmp/$asset" | sha256sum --check --status || { echo "Checksum mismatch for $asset" >&2; exit 1; }
  mkdir -p "$AGENT_DIR"
  tar -xzf "$tmp/$asset" -C "$AGENT_DIR"
}

setup() {
  if [ ! -x "$AGENT_DIR/config.sh" ]; then
    download_runner
    echo "Installing runner OS dependencies (sudo required)"
    sudo "$AGENT_DIR/bin/installdependencies.sh"
  fi

  if [ -f "$AGENT_DIR/.runner" ]; then
    echo "ok       runner already registered in $AGENT_DIR"
  else
    (cd "$AGENT_DIR" && ./config.sh --unattended --replace --url "$repo_url" --token "$(get_token registration-token)" \
      --name "$AGENT_NAME" --labels "$AGENT_LABELS" --work _work)
  fi

  if [ "$AGENT_SERVICE" = 1 ] && has_systemd; then
    if ! (cd "$AGENT_DIR" && sudo ./svc.sh status >/dev/null 2>&1); then
      (cd "$AGENT_DIR" && sudo ./svc.sh install "$USER")
    fi
    (cd "$AGENT_DIR" && sudo ./svc.sh start >/dev/null 2>&1 || true)
    echo "ok       runner service installed and started"
  else
    echo "Run it in the foreground with: cd $AGENT_DIR && ./run.sh"
    echo "(or re-run with systemd enabled to install it as a service)"
  fi
  echo "Runner '$AGENT_NAME' (labels: self-hosted, $AGENT_LABELS): $repo_url/settings/actions/runners"
}

remove() {
  [ -x "$AGENT_DIR/config.sh" ] || { echo "No runner found in $AGENT_DIR"; return 0; }
  if has_systemd; then
    (cd "$AGENT_DIR" && sudo ./svc.sh stop >/dev/null 2>&1 || true; sudo ./svc.sh uninstall >/dev/null 2>&1 || true)
  fi
  if [ -f "$AGENT_DIR/.runner" ]; then
    (cd "$AGENT_DIR" && ./config.sh remove --token "$(get_token remove-token)")
  fi
  rm -rf "$AGENT_DIR"
  echo "Runner removed"
}

status() {
  [ -f "$AGENT_DIR/.runner" ] || { echo "No runner registered in $AGENT_DIR (run: make setup-agent)"; return 1; }
  if has_systemd; then
    unit=$(cat "$AGENT_DIR/.service" 2>/dev/null || true)
    [ -n "$unit" ] && echo "service  $unit: $(systemctl is-active "$unit" || true)"
  fi
  if command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
    gh api "repos/$slug/actions/runners" --jq '.runners[] | "runner   \(.name)\t\(.status)\t\([.labels[].name] | join(","))"'
  else
    echo "runner   GitHub status unavailable (run: gh auth login), see $repo_url/settings/actions/runners"
  fi
}

case "${1:-}" in
  setup) setup ;;
  remove) remove ;;
  status) status ;;
  *) echo "Usage: $0 setup|remove|status" >&2; exit 2 ;;
esac
