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

if [ -x "$REPO_ROOT/.venv/bin/python" ] &&
  "$REPO_ROOT/.venv/bin/python" -c 'import avro.schema, jsonschema, yaml' >/dev/null 2>&1; then
  ok "catalog validation dependencies"
else
  bad "catalog validation dependencies are missing (run: make setup)"
fi

for t in kubectl minikube helm terraform go jq yq nodejs tflint trivy; do
  want=$(pinned_version "$t"); have=$(installed_version "$t")
  if [ -z "$have" ]; then bad "$t is missing, want $want (run: make setup)"
  elif [ "$have" != "$want" ]; then bad "$t is $have, want $want (run: make setup)"
  else ok "$t $have"; fi
done

if PATH="$BIN_DIR:$PATH" npm --version >/dev/null 2>&1; then
  ok npm
else
  bad "npm is missing (run: make setup)"
fi

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

if [[ "$(pinned_version postgres)" =~ ^[0-9]+\.[0-9]+-alpine$ ]]; then
  ok "PostgreSQL container pin $(pinned_version postgres) (pulled by local deployment/integration tests)"
else
  bad "PostgreSQL container pin is missing/invalid in .tool-versions; restore it before make dev-up or module integration"
fi

for runtime in kafka apicurio otelcol prometheus alertmanager loki tempo grafana; do
  if [[ "$(pinned_version "$runtime")" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    ok "$runtime container pin $(pinned_version "$runtime") (pulled by local deployment/integration tests)"
  else
    bad "$runtime container pin is missing/invalid in .tool-versions; restore it before make dev-up or module integration"
  fi
done

if [ "${MULTI_CLUSTER:-0}" = 1 ]; then
  if [ -r /proc/sys/fs/inotify/max_user_instances ] &&
    [ "$(cat /proc/sys/fs/inotify/max_user_instances)" -ge 1024 ]; then
    ok "multi-cluster host inotify instance limit"
  else
    bad "multi-cluster needs fs.inotify.max_user_instances >= 1024 on the Docker host (run: MULTI_CLUSTER=1 make setup)"
  fi
fi

cnpg_chart="$REPO_ROOT/.local/tools/cloudnative-pg-$(pinned_version cnpg-chart).tgz"
if [ -f "$cnpg_chart" ] &&
  printf '%s  %s\n' "$(pinned_version cnpg-chart-sha256)" "$cnpg_chart" | sha256sum --check --status; then
  ok "CNPG operator chart $(pinned_version cnpg-chart) (checksum verified)"
else
  bad "CNPG chart is missing or checksum differs (run: make setup)"
fi

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) bad "$BIN_DIR is not on PATH (add it in ~/.bashrc)" ;;
esac

exit $fail
