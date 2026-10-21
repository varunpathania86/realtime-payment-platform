#!/usr/bin/env bash
# Install the pinned CLI tools from .tool-versions into $BIN_DIR (default ~/.local/bin).
# No sudo needed. Idempotent: tools already at the pinned version are skipped.
# Every download is verified with SHA-256.
# Usage: scripts/install-tools.sh [tool ...]   (default: all)
set -euo pipefail
# shellcheck source=scripts/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

ALL_TOOLS=(kubectl minikube helm terraform go jq yq)
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$BIN_DIR"

fetch() { curl -fsSL --retry 3 -o "$2" "$1"; }

verify() { # file expected-sha256
  echo "$2  $1" | sha256sum --check --status || { echo "Checksum mismatch for $(basename "$1")" >&2; exit 1; }
}

# SHA-256 of a GitHub release asset, from the GitHub API.
github_digest() { # owner/repo tag asset
  curl -fsSL "https://api.github.com/repos/$1/releases/tags/$2" |
    python3 -c 'import sys, json; a = {x["name"]: x.get("digest") or "" for x in json.load(sys.stdin)["assets"]}; print(a[sys.argv[1]].removeprefix("sha256:"))' "$3"
}

install_kubectl() {
  local v=$1 base="https://dl.k8s.io/release/v$1/bin/$OS/$ARCH"
  fetch "$base/kubectl" "$TMP/kubectl"
  verify "$TMP/kubectl" "$(curl -fsSL "$base/kubectl.sha256")"
  install -m 0755 "$TMP/kubectl" "$BIN_DIR/kubectl"
}

install_minikube() {
  local v=$1 asset="minikube-$OS-$ARCH"
  fetch "https://github.com/kubernetes/minikube/releases/download/v$v/$asset" "$TMP/minikube"
  verify "$TMP/minikube" "$(github_digest kubernetes/minikube "v$v" "$asset")"
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
  local v=$1 asset="jq-$OS-$ARCH"
  fetch "https://github.com/jqlang/jq/releases/download/jq-$v/$asset" "$TMP/jq"
  verify "$TMP/jq" "$(github_digest jqlang/jq "jq-$v" "$asset")"
  install -m 0755 "$TMP/jq" "$BIN_DIR/jq"
}

install_yq() {
  local v=$1 asset="yq_${OS}_$ARCH"
  fetch "https://github.com/mikefarah/yq/releases/download/v$v/$asset" "$TMP/yq"
  verify "$TMP/yq" "$(github_digest mikefarah/yq "v$v" "$asset")"
  install -m 0755 "$TMP/yq" "$BIN_DIR/yq"
}

tools=("$@")
[ ${#tools[@]} -gt 0 ] || tools=("${ALL_TOOLS[@]}")

for t in "${tools[@]}"; do
  want=$(pinned_version "$t")
  [ -n "$want" ] || { echo "Unknown tool: $t" >&2; exit 1; }
  have=$(installed_version "$t")
  if [ "$have" = "$want" ]; then
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
