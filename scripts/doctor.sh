#!/usr/bin/env bash
# Verify the developer environment against .tool-versions. Exit 1 if anything is missing or wrong.
set -uo pipefail
# shellcheck source=scripts/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

fail=0
ok()   { printf 'ok       %s\n' "$*"; }
bad()  { printf 'PROBLEM  %s\n' "$*"; fail=1; }

for t in git make python3 curl; do
  if command -v "$t" >/dev/null; then ok "$t"; else bad "$t is missing (see docs/developer-setup.md)"; fi
done

if [ -x "$REPO_ROOT/.venv/bin/pre-commit" ] || command -v pre-commit >/dev/null; then
  ok pre-commit
else
  bad "pre-commit is missing (run: make setup)"
fi

for t in kubectl minikube helm terraform go jq yq; do
  want=$(pinned_version "$t"); have=$(installed_version "$t")
  if [ -z "$have" ]; then bad "$t is missing, want $want (run: make setup)"
  elif [ "$have" != "$want" ]; then bad "$t is $have, want $want (run: make setup)"
  else ok "$t $have"; fi
done

want=$(pinned_version nodejs); have=$(installed_version nodejs)
if [ -z "$have" ]; then bad "node is missing, want $want.x (see docs/developer-setup.md)"
elif [ "${have%%.*}" != "$want" ]; then bad "node is $have, want $want.x (see docs/developer-setup.md)"
else ok "node $have"; fi

if [ -z "$(installed_version docker)" ]; then
  bad "docker is missing (run: make setup)"
elif ! docker info >/dev/null 2>&1; then
  if id -nG "$USER" | grep -qw docker; then
    bad "docker daemon is not reachable (run: make docker, or start Docker Desktop)"
  else
    bad "docker daemon is not reachable (run: make docker, or start Docker Desktop)"
  fi
else
  ok "docker $(installed_version docker)"
fi

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) bad "$BIN_DIR is not on PATH (add it in ~/.bashrc)" ;;
esac

exit $fail
