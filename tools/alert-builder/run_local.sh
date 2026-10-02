#!/usr/bin/env bash
# Run Watchtower on this machine. MOCK=1 skips team config and stays offline.
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"

mock_lc="$(printf '%s' "${MOCK:-}" | tr '[:upper:]' '[:lower:]')"
if [[ "$mock_lc" != "1" && "$mock_lc" != "true" ]]; then
  mapfile -t TEAM_CONFIGS < <(find /config -maxdepth 1 -type f -name '*.config' | sort)
  if [[ ${#TEAM_CONFIGS[@]} -ne 1 ]]; then
    echo "expected exactly one /config/*.config" >&2
    exit 1
  fi
  set -a
  # shellcheck disable=SC1090
  source "${TEAM_CONFIGS[0]}"
  set +a
fi

export PORT="${PORT:-8080}"
export DATA_DIR="${DATA_DIR:-./.data}"
exec python3 main.py
