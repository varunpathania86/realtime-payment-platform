#!/usr/bin/env bash
# Install the pinned CLI tools from .tool-versions into $BIN_DIR (default ~/.local/bin).
# No sudo needed. Idempotent: tools already at the pinned version are skipped.
# Every download is verified with SHA-256.
# Usage: scripts/install-tools.sh [tool ...]   (default: all)
set -euo pipefail
# shellcheck source=scripts/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

ALL_TOOLS=(kubectl minikube helm terraform go jq yq nodejs tflint trivy)
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$BIN_DIR"

fetch() { curl -fsSL --retry 3 -o "$2" "$1"; }

verify() { # file expected-sha256
  [[ "$2" =~ ^[a-fA-F0-9]{64}$ ]] || { echo "Invalid or missing SHA-256 for $(basename "$1")" >&2; exit 1; }
  echo "$2  $1" | sha256sum --check --status || { echo "Checksum mismatch for $(basename "$1")" >&2; exit 1; }
}

# SHA-256 of a GitHub release asset, from the GitHub API.
github_digest() { # owner/repo tag asset
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
    digest = next(asset.get("digest") or "" for asset in assets if asset["name"] == sys.argv[2])
    if not re.fullmatch(r"sha256:[a-fA-F0-9]{64}", digest):
        raise ValueError("missing or invalid SHA-256 digest")
except (OSError, ValueError, KeyError, TypeError, StopIteration) as error:
    sys.exit(f"Invalid GitHub checksum metadata for {sys.argv[2]}: {error}")
print(digest.removeprefix("sha256:"))
PY
}

install_kubectl() {
  local v=$1 base="https://dl.k8s.io/release/v$1/bin/$OS/$ARCH"
  fetch "$base/kubectl" "$TMP/kubectl"
  verify "$TMP/kubectl" "$(curl -fsSL "$base/kubectl.sha256")"
  install -m 0755 "$TMP/kubectl" "$BIN_DIR/kubectl"
}

install_minikube() {
  local v=$1 asset="minikube-$OS-$ARCH" digest
  fetch "https://github.com/kubernetes/minikube/releases/download/v$v/$asset" "$TMP/minikube"
  digest=$(github_digest kubernetes/minikube "v$v" "$asset") || return 1
  verify "$TMP/minikube" "$digest"
  install -m 0755 "$TMP/minikube" "$BIN_DIR/minikube"
}

install_helm() {
  local v=$1 f="helm-v$1-$OS-$ARCH.tar.gz"
  fetch "https://get.helm.sh/$f" "$TMP/$f"
  verify "$TMP/$f" "$(curl -fsSL "https://get.helm.sh/$f.sha256sum" | awk '{print $1}')"
  tar -xzf "$TMP/$f" -C "$TMP" "$OS-$ARCH/helm"
  install -m 0755 "$TMP/$OS-$ARCH/helm" "$BIN_DIR/helm"
}

install_terraform() {
  local v=$1 f="terraform_$1_${OS}_$ARCH.zip" base="https://releases.hashicorp.com/terraform/$1"
  fetch "$base/$f" "$TMP/$f"
  verify "$TMP/$f" "$(curl -fsSL "$base/terraform_${v}_SHA256SUMS" | awk -v f="$f" '$2 == f {print $1}')"
  python3 -c 'import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extract("terraform", sys.argv[2])' "$TMP/$f" "$TMP"
  install -m 0755 "$TMP/terraform" "$BIN_DIR/terraform"
}

install_go() {
  local v=$1 f="go$1.$OS-$ARCH.tar.gz" goroot="${GOROOT_DIR:-$HOME/.local/go}"
  fetch "https://go.dev/dl/$f" "$TMP/$f"
  verify "$TMP/$f" "$(curl -fsSL 'https://go.dev/dl/?mode=json&include=all' |
    python3 -c 'import sys, json; f = sys.argv[1]; print(next(x["sha256"] for r in json.load(sys.stdin) for x in r["files"] if x["filename"] == f))' "$f")"
  rm -rf "$goroot"
  mkdir -p "$goroot"
  tar -xzf "$TMP/$f" -C "$goroot" --strip-components=1
  ln -sf "$goroot/bin/go" "$BIN_DIR/go"
  ln -sf "$goroot/bin/gofmt" "$BIN_DIR/gofmt"
}

install_jq() {
  local v=$1 asset="jq-$OS-$ARCH" digest
  fetch "https://github.com/jqlang/jq/releases/download/jq-$v/$asset" "$TMP/jq"
  digest=$(github_digest jqlang/jq "jq-$v" "$asset") || return 1
  verify "$TMP/jq" "$digest"
  install -m 0755 "$TMP/jq" "$BIN_DIR/jq"
}

install_yq() {
  local v=$1 asset="yq_${OS}_$ARCH" digest
  fetch "https://github.com/mikefarah/yq/releases/download/v$v/$asset" "$TMP/yq"
  digest=$(github_digest mikefarah/yq "v$v" "$asset") || return 1
  verify "$TMP/yq" "$digest"
  install -m 0755 "$TMP/yq" "$BIN_DIR/yq"
}

install_nodejs() {
  local v=$1 node_arch=$ARCH
  [ "$node_arch" != amd64 ] || node_arch=x64
  local f="node-v$v-$OS-$node_arch.tar.gz" base="https://nodejs.org/dist/v$v"
  fetch "$base/$f" "$TMP/$f"
  verify "$TMP/$f" "$(curl -fsSL "$base/SHASUMS256.txt" | awk -v f="$f" '$2 == f {print $1}')"
  local node_root="$HOME/.local/node-v$v"
  mkdir -p "$node_root"
  tar -xzf "$TMP/$f" -C "$node_root" --strip-components=1
  ln -sf "$node_root/bin/node" "$BIN_DIR/node"
  ln -sf "$node_root/bin/npm" "$BIN_DIR/npm"
  ln -sf "$node_root/bin/npx" "$BIN_DIR/npx"
}

install_tflint() {
  local v=$1 f="tflint_${OS}_$ARCH.zip" base="https://github.com/terraform-linters/tflint/releases/download/v$1" digest
  fetch "$base/$f" "$TMP/$f"
  digest=$(github_digest terraform-linters/tflint "v$v" "$f") || return 1
  verify "$TMP/$f" "$digest"
  python3 -c 'import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extract("tflint", sys.argv[2])' "$TMP/$f" "$TMP"
  install -m 0755 "$TMP/tflint" "$BIN_DIR/tflint"
}

install_trivy() {
  local v=$1 trivy_arch=$ARCH digest
  [ "$trivy_arch" != amd64 ] || trivy_arch=64bit
  [ "$trivy_arch" != arm64 ] || trivy_arch=ARM64
  local f="trivy_${v}_Linux-${trivy_arch}.tar.gz" base="https://github.com/aquasecurity/trivy/releases/download/v$v"
  fetch "$base/$f" "$TMP/$f"
  digest=$(github_digest aquasecurity/trivy "v$v" "$f") || return 1
  verify "$TMP/$f" "$digest"
  tar -xzf "$TMP/$f" -C "$TMP" trivy
  install -m 0755 "$TMP/trivy" "$BIN_DIR/trivy"
}

tools=("$@")
[ ${#tools[@]} -gt 0 ] || tools=("${ALL_TOOLS[@]}")

for t in "${tools[@]}"; do
  want=$(pinned_version "$t")
  [ -n "$want" ] || { echo "Unknown tool: $t" >&2; exit 1; }
  have=$(installed_version "$t")
  if [ "$have" = "$want" ] &&
    { [ "$t" != nodejs ] || PATH="$BIN_DIR:$PATH" npm --version >/dev/null 2>&1; }; then
    echo "ok       $t $have"
    continue
  fi
  echo "install  $t $want${have:+ (found $have)}"
  "install_$t" "$want"
done

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "NOTE: add $BIN_DIR to PATH:  echo 'export PATH=\"$BIN_DIR:\$PATH\"' >> ~/.bashrc" ;;
esac
