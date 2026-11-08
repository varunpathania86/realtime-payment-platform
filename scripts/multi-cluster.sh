#!/usr/bin/env bash
# Opt-in three-profile Kafka learning topology. Never operates on existing unowned profiles.
set -euo pipefail
# shellcheck source=scripts/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
export PATH="$BIN_DIR:$PATH"
cd "$REPO_ROOT"
umask 077

BASE="rpp"
PROFILES=("$BASE-1" "$BASE-2" "$BASE-3")
NETWORK="$BASE-net"
STATE="$REPO_ROOT/.local/$BASE-multi"
ROOT="$REPO_ROOT/platform/terraform/environments/kafka-multicluster"
MARKER="$STATE/owned"
MINIKUBE_ROOT="${MINIKUBE_HOME:-$HOME}/.minikube"
ACTION="${1:-}"
FULL_STACK="${MULTI_FULL_STACK:-1}"
if [ -f "$MARKER" ] && [ ! -f "$STATE/full-stack" ]; then FULL_STACK="${MULTI_FULL_STACK:-0}"; fi
[[ "$FULL_STACK" = 0 || "$FULL_STACK" = 1 ]] || { echo "MULTI_FULL_STACK must be 0 or 1" >&2; exit 1; }
if [ -f "$STATE/full-stack" ]; then FULL_STACK=1; fi

member_for_profile() {
  case "$1" in
    "${PROFILES[0]}") printf 1 ;;
    "${PROFILES[1]}") printf 2 ;;
    "${PROFILES[2]}") printf 3 ;;
    *) echo "Invalid multi-cluster profile: $1" >&2; return 1 ;;
  esac
}

profile_for_member() {
  case "$1" in
    1) printf '%s' "${PROFILES[0]}" ;;
    2) printf '%s' "${PROFILES[1]}" ;;
    3) printf '%s' "${PROFILES[2]}" ;;
    *) echo "Invalid database member: $1" >&2; return 1 ;;
  esac
}

database_member_number() {
  case "$1" in
    pg-1|pg-2|pg-3) printf '%s' "${1#pg-}" ;;
    *) echo "Invalid database member: $1" >&2; return 1 ;;
  esac
}

state_empty() {
  [ ! -f "$1" ] ||
    jq -e '[(.resources // [])[] | select(.mode == "managed")] | length == 0' "$1" >/dev/null
}

stale_lock_cleanup() {
  local lock_path
  while IFS= read -r lock_path; do
    [ -n "$lock_path" ] || continue
    if ! pgrep -af "terraform.*-chdir=$ROOT" >/dev/null 2>&1; then
      echo "Removing stale Terraform state lock: $lock_path" >&2
      rm -f "$lock_path"
    fi
  done < <(find "$STATE" -maxdepth 2 \( -name '*.lock.info' -o -name '.terraform.tfstate.lock.info' \) -print 2>/dev/null)
}

tf() {
  stale_lock_cleanup
  TF_DATA_DIR="$STATE/terraform" terraform -chdir="$ROOT" "$@"
}

configuration() {
  local options=() settings
  if [ -n "${DEV_CONFIG:-}" ]; then options+=("--config=$DEV_CONFIG"); fi
  settings=$("$REPO_ROOT/.venv/bin/python" -m scripts.topology_config "$STATE" "${options[@]}")
  read -r NODE_CPUS NODE_MEMORY <<< "$settings"
}

resize_profile() {
  local profile="$1"
  node_budget "$profile"
  [ "$(docker inspect "$profile" --format '{{index .Config.Labels "name.minikube.sigs.k8s.io"}}')" = "$profile" ] ||
    { echo "Refusing node budget update: container identity mismatch for $profile" >&2; return 1; }
  docker update --cpus="$NODE_CPUS" --memory="${NODE_MEMORY}m" --memory-swap="$((NODE_MEMORY * 2))m" "$profile"
}

node_budget() {
  local profile="$1" settings
  settings=$(jq -r --arg key "$(member_for_profile "$profile")" '(.nodes[$key] // .node) | "\(.cpus) \(.memory_mib)"' "$STATE/configuration.json")
  read -r NODE_CPUS NODE_MEMORY <<< "$settings"
}

require_owned() {
  [ ! -L "$STATE" ] || { echo "Refusing symlinked topology state: $STATE" >&2; exit 1; }
  [ -f "$MARKER" ] || { echo "No owned multi-cluster topology for $BASE; run make dev-up MULTI_CLUSTER=3" >&2; exit 1; }
  [ "$(cat "$MARKER")" = "$BASE" ] || { echo "Invalid topology ownership marker" >&2; exit 1; }
}

claim() {
  [ ! -L "$STATE" ] || { echo "Refusing symlinked topology state: $STATE" >&2; exit 1; }
  if [ -f "$REPO_ROOT/.local/$BASE/owned" ]; then
    echo "Refusing legacy multi-cluster state in $REPO_ROOT/.local/$BASE; preserve its state and data. See docs/developer-setup.md." >&2
    exit 1
  fi
  if [ -f "$MARKER" ]; then require_numbered_identity "$STATE"; fi
  if [ "$FULL_STACK" = 1 ] && [ -f "$MARKER" ] && [ ! -f "$STATE/full-stack" ]; then
    local profile
    for profile in "${PROFILES[@]}"; do
      if [ -e "$MINIKUBE_ROOT/profiles/$profile" ] ||
        ! state_empty "$REPO_ROOT/.local/$profile/terraform.tfstate"; then
        echo "Refusing in-place conversion of the existing Kafka-only lab. Complete dev-clean MULTI_CLUSTER=3 first." >&2
        exit 1
      fi
    done
    if docker network inspect "$NETWORK" >/dev/null 2>&1 || ! state_empty "$STATE/terraform.tfstate"; then
      echo "Refusing in-place conversion of the existing Kafka-only lab. Complete dev-clean MULTI_CLUSTER=3 first." >&2
      exit 1
    fi
  fi
  if [ ! -f "$MARKER" ]; then
    local metadata=""
    if [ -d "$STATE" ]; then
      metadata=$(find "$STATE" -mindepth 1 -maxdepth 1 ! -name terraform -print -quit)
    fi
    [ -z "$metadata" ] ||
      { echo "Refusing existing unowned topology metadata: $metadata" >&2; exit 1; }
    for profile in "${PROFILES[@]}"; do
      [ ! -e "$MINIKUBE_ROOT/profiles/$profile" ] || { echo "Refusing existing unowned profile $profile" >&2; exit 1; }
      [ ! -e "$REPO_ROOT/.local/$profile" ] || { echo "Refusing existing unowned application state for $profile" >&2; exit 1; }
    done
    if docker network inspect "$NETWORK" >/dev/null 2>&1; then
      echo "Refusing existing unowned Docker network $NETWORK" >&2
      exit 1
    fi
    mkdir -p "$STATE"
    printf '%s\n' "numbered-v1" > "$STATE/cluster-identity"
    printf '%s\n' "$BASE" > "$MARKER"
  fi
  require_owned
  if [ "$FULL_STACK" = 1 ]; then touch "$STATE/full-stack"; fi
}

start_profiles() {
  local gateway prefix profile index=2
  if [ -r /proc/sys/fs/inotify/max_user_instances ] &&
    [ "$(cat /proc/sys/fs/inotify/max_user_instances)" -lt 1024 ]; then
    echo "Three clusters require fs.inotify.max_user_instances >= 1024 on the Docker host. Run MULTI_CLUSTER=3 make setup (sudo required); see docs/developer-setup.md." >&2
    exit 1
  fi
  if ! docker network inspect "$NETWORK" >/dev/null 2>&1; then
    docker network create --label "rpp.multi=$BASE" "$NETWORK"
  fi
  [ "$(docker network inspect "$NETWORK" --format '{{index .Labels "rpp.multi"}}')" = "$BASE" ] ||
    { echo "Network ownership mismatch: $NETWORK" >&2; exit 1; }
  gateway=$(docker network inspect "$NETWORK" --format '{{(index .IPAM.Config 0).Gateway}}')
  prefix="${gateway%.*}"
  for profile in "${PROFILES[@]}"; do
    node_budget "$profile"
    if [ -n "${REJOIN_PROFILE:-}" ] && [ "$profile" != "$REJOIN_PROFILE" ]; then
      index=$((index + 1))
      continue
    fi
    local options=()
    if [ ! -f "$MINIKUBE_ROOT/profiles/$profile/config.json" ]; then
      options+=("--static-ip=$prefix.$index")
    fi
    minikube start -p "$profile" --driver=docker --network="$NETWORK" \
      "${options[@]}" \
      --cpus="$NODE_CPUS" --memory="$NODE_MEMORY" --disk-size=20g --kubernetes-version="v$(pinned_version kubectl)"
    resize_profile "$profile"
    [ "$(minikube -p "$profile" ip)" = "$prefix.$index" ] ||
      { echo "Unexpected node address for $profile; refusing to change Kafka quorum identity" >&2; exit 1; }
    index=$((index + 1))
  done
}

inventory() {
  local addresses=() profile address index=0 fenced=""
  if [ -f "$STATE/database-fence.json" ]; then
    fenced=$(database_member_number "$(jq -er '.old' "$STATE/database-fence.json")")
  fi
  for profile in "${PROFILES[@]}"; do
    if [ "$(member_for_profile "$profile")" = "$fenced" ] && [ "${REJOINING:-0}" != 1 ]; then
      address=$(jq -r ".addresses[$index]" "$STATE/topology.tfvars.json")
    else
      address=$(minikube -p "$profile" ip)
    fi
    addresses+=("$address")
    index=$((index + 1))
  done
  jq -n --arg profile1 "${PROFILES[0]}" --arg profile2 "${PROFILES[1]}" --arg profile3 "${PROFILES[2]}" \
    --arg address1 "${addresses[0]}" --arg address2 "${addresses[1]}" --arg address3 "${addresses[2]}" \
    --arg config "${KUBECONFIG:-$HOME/.kube/config}" \
    '{profiles:[$profile1,$profile2,$profile3],addresses:[$address1,$address2,$address3],kube_config_path:$config}' > "$STATE/topology.pending.json"
  if [ -f "$STATE/topology.tfvars.json" ] && [ -f "$STATE/terraform.tfstate" ]; then
    if ! jq -e --slurpfile previous "$STATE/topology.tfvars.json" \
      '.addresses == $previous[0].addresses and .profiles == $previous[0].profiles' "$STATE/topology.pending.json" >/dev/null; then
      echo "Topology identity changed; restore the original profiles/network before updating or destroying resources" >&2
      rm -f "$STATE/topology.pending.json"
      exit 1
    fi
  fi
  mv "$STATE/topology.pending.json" "$STATE/topology.tfvars.json"
  if [ -f "$STATE/configuration.json" ]; then
    jq --slurpfile config "$STATE/configuration.json" '. + {cluster_sizes:$config[0].clusters, observability_enabled:($config[0].observability // false), identity_enabled:($config[0].identity // false)}' \
      "$STATE/topology.tfvars.json" > "$STATE/topology.pending.json"
    mv "$STATE/topology.pending.json" "$STATE/topology.tfvars.json"
  fi
  if [ "$FULL_STACK" = 1 ]; then
    jq '. + {full_stack:true}' "$STATE/topology.tfvars.json" > "$STATE/topology.pending.json"
    mv "$STATE/topology.pending.json" "$STATE/topology.tfvars.json"
    if [ -f "$STATE/database-role.json" ]; then
      jq -s '.[0] * .[1]' "$STATE/topology.tfvars.json" "$STATE/database-role.json" > "$STATE/topology.pending.json"
      mv "$STATE/topology.pending.json" "$STATE/topology.tfvars.json"
    fi
  fi
}

enable_metrics() {
  local profile
  for profile in "${PROFILES[@]}"; do
    minikube -p "$profile" addons enable metrics-server
    kubectl --context "$profile" --request-timeout=15s -n kube-system \
      rollout status deployment/metrics-server --timeout=120s
  done
  echo "Metrics-server enabled. Initial CPU/RAM samples may take about a minute; run make dev-status MULTI_CLUSTER=3."
}

init() {
  tf init -input=false -reconfigure -backend-config="path=$STATE/terraform.tfstate"
}

deploy() {
  local selected=("${PROFILES[0]}") profile index member targets=()
  if [ "$FULL_STACK" = 1 ]; then selected=("${PROFILES[@]}"); fi
  if [ -f "$STATE/database-fence.json" ] && [ "${REJOINING:-0}" != 1 ]; then
    [ "$(jq -r '.phase' "$STATE/database-fence.json")" = complete ] ||
      { echo "Resume pending database failover/rejoin before updating" >&2; exit 1; }
    local old
    old=$(database_member_number "$(jq -er '.old' "$STATE/database-fence.json")")
    selected=()
    for profile in "${PROFILES[@]}"; do
      member=$(member_for_profile "$profile")
      [ "$member" != "$old" ] || continue
      selected+=("$profile")
      targets+=("-target=module.cluster_${member}" "-target=module.database_${member}[0]")
    done
  fi
  init
  tf plan -input=false -var-file="$STATE/topology.tfvars.json" "${targets[@]}" -out="$STATE/tfplan"
  tf apply -input=false "$STATE/tfplan"
  if [ "$FULL_STACK" = 1 ]; then
    echo "CNPG resources provisioned. Waiting for PostgreSQL members."
    for profile in "${selected[@]}"; do
      kubectl --context "$profile" --request-timeout=15s -n postgres \
        wait --for=condition=Ready "cluster.postgresql.cnpg.io/pg-$(member_for_profile "$profile")" --timeout=300s
    done
  fi
  tf output -json application_vars > "$STATE/events.pending.json"
  tf output -json workload_vars > "$STATE/workloads.pending.json"
  if [ "$FULL_STACK" = 1 ]; then
    tf output -json database_application_vars > "$STATE/database.pending.json"
    jq -s '.[0] * .[1]' "$STATE/events.pending.json" "$STATE/database.pending.json" > "$STATE/full.pending.json"
    mv "$STATE/full.pending.json" "$STATE/events.pending.json"
    rm -f "$STATE/database.pending.json"
  fi
  for profile in "${selected[@]}"; do
    mkdir -p "$REPO_ROOT/.local/$profile"
    jq --arg key "$(member_for_profile "$profile")" --slurpfile workloads "$STATE/workloads.pending.json" \
      '. * $workloads[0][$key]' "$STATE/events.pending.json" > "$REPO_ROOT/.local/$profile/events.tfvars.json"
    jq --arg key "$(member_for_profile "$profile")" --slurpfile config "$STATE/configuration.json" \
      '. + {streaming_enabled: ($key == "1" and ($config[0].streaming // false)), analytics_enabled: ($key == "1" and ($config[0].analytics // false)), lakehouse_enabled: ($key == "1" and ($config[0].lakehouse // false)), bi_enabled: ($key == "1" and ($config[0].bi // false)), ai_enabled: ($key == "1" and ($config[0].ai // false)), identity_enabled: ($key == "1" and ($config[0].identity // false)), dbx_enabled: ($key == "1" and ($config[0].dbx // false)), runtime_profile: ($config[0].runtime_profile // "standard"), risk_enabled: ($key == "1" and ($config[0].risk != null)), risk_package_key: ($config[0].risk.package_key // ""), risk_package_sha256: ($config[0].risk.package_sha256 // "")}' \
      "$REPO_ROOT/.local/$profile/events.tfvars.json" > "$REPO_ROOT/.local/$profile/streaming.pending.json"
    mv "$REPO_ROOT/.local/$profile/streaming.pending.json" "$REPO_ROOT/.local/$profile/events.tfvars.json"
    MINIKUBE_PROFILE="$profile" scripts/local-dev.sh update
  done
  rm -f "$STATE/events.pending.json" "$STATE/workloads.pending.json"
}

rejoin() {
  [ -f "$STATE/database-fence.json" ] || { echo "No fenced member to rebuild" >&2; exit 1; }
  local old target phase profile target_profile primary_system replica_system primary_pod replica_pod
  old=$(jq -er '.old' "$STATE/database-fence.json")
  target=$(jq -er '.target' "$STATE/database-fence.json")
  phase=$(jq -r '.phase' "$STATE/database-fence.json")
  profile=$(profile_for_member "$(database_member_number "$old")")
  target_profile=$(profile_for_member "$(database_member_number "$target")")
  [ "$old" != "$target" ] || { echo "Fenced member and recovery target must differ" >&2; exit 1; }
  case "$phase" in
    complete|rebuild-delete|rebuild-start) ;;
    *) echo "Complete pending failover before rebuilding" >&2; exit 1 ;;
  esac
  configuration
  if [ "$phase" = complete ]; then
    jq --arg old "$old" --arg target "$target" \
      '.bootstrap_sources[$old] = $target | del(.promotion_tokens[$old])' \
      "$STATE/database-role.json" > "$STATE/role.pending.json"
    mv "$STATE/role.pending.json" "$STATE/database-role.json"
    jq '.phase = "rebuild-delete"' "$STATE/database-fence.json" > "$STATE/fence.pending.json"
    mv "$STATE/fence.pending.json" "$STATE/database-fence.json"
    phase=rebuild-delete
  fi
  if [ "$phase" = rebuild-delete ]; then
    if docker inspect "$profile" >/dev/null 2>&1; then
      docker inspect "$profile" | jq -e --arg network "$NETWORK" \
        '.[0] | .State.Running == false and (.NetworkSettings.Networks | has($network) | not)' >/dev/null ||
        { echo "Former primary must remain stopped and disconnected before destructive rebuild" >&2; exit 1; }
    fi
    minikube delete -p "$profile"
    jq '.phase = "rebuild-start"' "$STATE/database-fence.json" > "$STATE/fence.pending.json"
    mv "$STATE/fence.pending.json" "$STATE/database-fence.json"
    phase=rebuild-start
  fi
  [ "$phase" = rebuild-start ] || { echo "Complete pending failover before rebuilding" >&2; exit 1; }
  REJOIN_PROFILE="$profile" start_profiles
  minikube -p "$profile" addons enable metrics-server
  REJOINING=1 inventory
  REJOINING=1 deploy
  primary_pod=$(kubectl --context "$target_profile" --request-timeout=15s -n postgres get cluster "$target" -o jsonpath='{.status.currentPrimary}')
  replica_pod=$(kubectl --context "$profile" --request-timeout=15s -n postgres get cluster "$old" -o jsonpath='{.status.currentPrimary}')
  primary_system=$(kubectl --context "$target_profile" --request-timeout=15s -n postgres \
    exec "$primary_pod" -c postgres -- psql -U postgres -Atqc 'SELECT system_identifier FROM pg_control_system()')
  replica_system=$(kubectl --context "$profile" --request-timeout=15s -n postgres \
    exec "$replica_pod" -c postgres -- psql -U postgres -Atqc "SELECT system_identifier FROM pg_control_system() WHERE pg_is_in_recovery()")
  [ -n "$replica_system" ] && [ "$primary_system" = "$replica_system" ] ||
    { echo "Rebuilt member is not a standby of the current primary; retaining recovery guard" >&2; exit 1; }
  rm -f "$STATE/database-fence.json"
  echo "$profile rebuilt from $target and verified read-only. All three profiles are active."
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  case "$ACTION" in
    observability)
      require_owned
      require_numbered_identity "$STATE"
      jq -e '.observability == true' "$STATE/configuration.json" >/dev/null ||
        { echo "Observability is disabled; select an explicit DEV_CONFIG with make dev-up MULTI_CLUSTER=3 first" >&2; exit 1; }
      exec kubectl --context "${PROFILES[0]}" -n telemetry port-forward --address 127.0.0.1 \
        service/grafana "${GRAFANA_PORT:-3000}:3000"
      ;;
    up)
      claim
      [ ! -f "$STATE/database-fence.json" ] || { echo "Fenced member must not be restarted; use dev-update or db-rejoin" >&2; exit 1; }
      [ ! -f "$STATE/database-transition.json" ] || { echo "Resume the pending db-switchover before starting/updating the topology" >&2; exit 1; }
      configuration
      start_profiles
      enable_metrics
      inventory
      deploy
      echo "Three-cluster stack ready. Use make dev-access MULTI_CLUSTER=3 MINIKUBE_PROFILE=${PROFILES[0]}"
      ;;
    update)
      require_owned
      require_numbered_identity "$STATE"
      [ ! -f "$STATE/database-transition.json" ] || { echo "Resume the pending db-switchover before updating the topology" >&2; exit 1; }
      if [ -f "$STATE/database-fence.json" ] &&
        [ "$(jq -r '.phase' "$STATE/database-fence.json")" != complete ]; then
        echo "Resume pending database failover/rejoin before updating" >&2
        exit 1
      fi
      if [ -f "$STATE/database-fence.json" ]; then
        database_member_number "$(jq -er '.old' "$STATE/database-fence.json")" >/dev/null
      fi
      configuration
      for profile in "${PROFILES[@]}"; do
        if [ -f "$STATE/database-fence.json" ] &&
          [ "pg-$(member_for_profile "$profile")" = "$(jq -r '.old' "$STATE/database-fence.json")" ]; then continue; fi
        resize_profile "$profile"
      done
      inventory
      deploy
      ;;
    status)
      require_owned
      "$REPO_ROOT/.venv/bin/python" scripts/resource_status.py "${PROFILES[@]}"
      ;;
    stop)
      require_owned
      [ ! -f "$STATE/database-fence.json" ] || { echo "Rebuild the fenced member before stopping the topology" >&2; exit 1; }
      [ ! -f "$STATE/database-transition.json" ] || { echo "Complete the database transition before stopping the topology" >&2; exit 1; }
      for profile in "${PROFILES[@]}"; do minikube stop -p "$profile"; done
      echo "Stopped $BASE; all profile data and Terraform state are retained."
      ;;
    metrics)
      require_owned
      require_numbered_identity "$STATE"
      enable_metrics
      ;;
    switchover)
      require_owned
      require_numbered_identity "$STATE"
      [ "$FULL_STACK" = 1 ] || { echo "CNPG switchover requires a full-stack topology" >&2; exit 1; }
      "$REPO_ROOT/.venv/bin/python" -m scripts.database_switchover "$BASE" "${DATABASE_TARGET:-}"
      inventory
      deploy
      ;;
    failover)
      require_owned
      require_numbered_identity "$STATE"
      "$REPO_ROOT/.venv/bin/python" -m scripts.database_failover "$BASE" "${DATABASE_TARGET:-}"
      inventory
      deploy
      ;;
    rejoin)
      require_owned
      require_numbered_identity "$STATE"
      rejoin
      ;;
    down)
      require_owned
      require_numbered_identity "$STATE"
      [ ! -f "$STATE/database-fence.json" ] || { echo "Rebuild the fenced member before destroying the topology" >&2; exit 1; }
      [ ! -f "$STATE/database-transition.json" ] || { echo "Complete the database transition before destroying the topology" >&2; exit 1; }
      [ "$(docker network inspect "$NETWORK" --format '{{index .Labels "rpp.multi"}}')" = "$BASE" ] ||
        { echo "Network ownership mismatch: $NETWORK" >&2; exit 1; }
      for profile in "${PROFILES[@]}"; do
        minikube status -p "$profile" >/dev/null ||
          { echo "All lab nodes must be running for cleanup; $profile is unavailable" >&2; exit 1; }
      done
      for profile in "${PROFILES[@]}"; do
        if [ -f "$REPO_ROOT/.local/$profile/images.tfvars.json" ]; then
          MINIKUBE_PROFILE="$profile" scripts/local-dev.sh down
        fi
      done
      init
      tf destroy -input=false -auto-approve -var-file="$STATE/topology.tfvars.json"
      rm -f "$STATE/database-role.json" "$STATE/database-transition.json"
      echo "Topology resources destroyed; profiles retained for deletion."
      ;;
    cluster-down)
      require_owned
      require_numbered_identity "$STATE"
      [ ! -f "$STATE/database-fence.json" ] || { echo "Rebuild the fenced member before deleting clusters" >&2; exit 1; }
      [ ! -f "$STATE/database-transition.json" ] || { echo "Complete the database transition before deleting clusters" >&2; exit 1; }
      [ "$(docker network inspect "$NETWORK" --format '{{index .Labels "rpp.multi"}}')" = "$BASE" ] ||
        { echo "Network ownership mismatch: $NETWORK" >&2; exit 1; }
      state_empty "$STATE/terraform.tfstate" ||
        { echo "Destroy the topology successfully before deleting clusters" >&2; exit 1; }
      for profile in "${PROFILES[@]}"; do
        state_empty "$REPO_ROOT/.local/$profile/terraform.tfstate" ||
          { echo "Application resources remain; destroy the topology before deleting clusters" >&2; exit 1; }
      done
      for profile in "${PROFILES[@]}"; do minikube delete -p "$profile"; done
      docker network rm "$NETWORK"
      rm -f "$STATE/topology.tfvars.json"
      ;;
    validate)
      mkdir -p "$STATE"
      tf init -backend=false -input=false
      tf validate
      ;;
    *)
      echo "Usage: $0 up|update|status|stop|metrics|observability|switchover|failover|rejoin|down|cluster-down|validate" >&2
      exit 2
      ;;
  esac
fi
