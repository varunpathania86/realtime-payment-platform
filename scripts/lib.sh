#!/usr/bin/env bash
# Shared helpers for scripts in this directory. Source it; do not execute it.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"
export OS="linux"

case "$(uname -m)" in
  x86_64) export ARCH="amd64" ;;
  aarch64 | arm64) export ARCH="arm64" ;;
  *) echo "Unsupported architecture: $(uname -m)" >&2; exit 1 ;;
esac

# Print the pinned version of a tool from .tool-versions.
pinned_version() {
  awk -v t="$1" '$1 == t { print $2 }' "$REPO_ROOT/.tool-versions"
}

refuse_fenced_profile() {
  local profile="$1" base="${1%-?}" state
  state="$REPO_ROOT/.local/$base"
  if [[ "$profile" =~ -[abc]$ ]] && [ -f "$state/owned" ] &&
    [ -f "$state/database-fence.json" ]; then
    [ "$(cat "$state/owned")" = "$base" ] || { echo "Invalid topology ownership marker" >&2; return 1; }
    if [ "$(jq -r '.old' "$state/database-fence.json")" = "pg-${profile: -1}" ]; then
      if [ "${REJOINING:-0}" = 1 ] &&
        [ "$(jq -r '.phase' "$state/database-fence.json")" = rebuild-start ]; then
        return 0
      fi
      echo "Profile $profile is fenced. Use make db-rejoin DEV_PROFILE=$base; never restart the old database." >&2
      return 1
    fi
  fi
}

# Print the installed version of a tool (empty if missing). Prefers $BIN_DIR over PATH.
installed_version() {
  local bin="$BIN_DIR:$PATH" out
  case "$1" in
    kubectl)   out=$(PATH=$bin kubectl version --client 2>/dev/null | sed -n 's/^Client Version: v//p') ;;
    minikube)  out=$(PATH=$bin minikube version --short 2>/dev/null | sed 's/^v//') ;;
    helm)      out=$(PATH=$bin helm version --short 2>/dev/null | sed 's/^v//; s/+.*//') ;;
    terraform) out=$(PATH=$bin terraform version 2>/dev/null | sed -n '1s/^Terraform v//p') ;;
    go)        out=$(PATH=$bin go version 2>/dev/null | sed -n 's/^go version go\([0-9.]*\).*/\1/p') ;;
    jq)        out=$(PATH=$bin jq --version 2>/dev/null | sed 's/^jq-//') ;;
    yq)        out=$(PATH=$bin yq --version 2>/dev/null | sed -n 's/.* v\([0-9.]*\)$/\1/p') ;;
    nodejs)    out=$(PATH=$bin node --version 2>/dev/null | sed 's/^v//') ;;
    tflint)    out=$(PATH=$bin tflint --version 2>/dev/null | sed -n '1s/^TFLint version //p') ;;
    trivy)     out=$(PATH=$bin trivy --version 2>/dev/null | sed -n 's/^Version: //p') ;;
    docker)    out=$(PATH=$bin docker --version 2>/dev/null | sed -n 's/^Docker version \([0-9.]*\).*/\1/p') ;;
  esac
  echo "$out"
}
