#!/usr/bin/env bash
set -euo pipefail
# shellcheck source=scripts/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

[ "${RISK_MODEL:-0}" = 1 ] || exit 0
docker build --build-arg "RISK_PYTHON_VERSION=$(pinned_version risk-python)" \
  -t "rpp-tools/payment-risk:$(pinned_version risk-python)" \
  "$REPO_ROOT/modules/payment-risk-model"
