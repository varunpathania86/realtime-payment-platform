#!/usr/bin/env bash
set -euo pipefail

[ "${MULTI_CLUSTER:-0}" = 1 ] || exit 0
if [ ! -r /proc/sys/fs/inotify/max_user_instances ]; then
  echo "Configure fs.inotify.max_user_instances >= 1024 on the Linux Docker host; see docs/developer-setup.md" >&2
  exit 1
fi
if [ "$(cat /proc/sys/fs/inotify/max_user_instances)" -lt 1024 ]; then
  echo "Multi-cluster Kubernetes needs a higher shared-host inotify limit (sudo required)."
  printf 'fs.inotify.max_user_instances=1024\n' |
    sudo tee /etc/sysctl.d/90-rpp-multicluster.conf >/dev/null
  sudo sysctl -p /etc/sysctl.d/90-rpp-multicluster.conf
fi
