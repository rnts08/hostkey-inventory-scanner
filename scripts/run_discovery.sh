#!/usr/bin/env bash
set -euo pipefail

# Wrapper around hostkey_matrix.py for local and CI runs.
# Optional env:
#   HOSTKEY_API_BASE (optional; defaults to auto-fallback invapi/api)
#   HOSTKEY_TOKEN    (optional)
#   WORKERS          (default: 12)
#   PROBE_FILTERS    (default: 1) enables location/group probe mode
#   LOCATIONS        (optional comma-separated list)
#   GROUPS           (optional comma-separated list)
#   REQUIRE_LINUX_HOURLY (default: 0) run os.php compatibility checks
#   REQUEST_TIMEOUT  (default: 8) per API request timeout in seconds

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
PROBE_FILTERS="${PROBE_FILTERS:-1}"

EXTRA_ARGS=()
if [[ "${PROBE_FILTERS}" == "1" ]]; then
  EXTRA_ARGS+=(--probe-filters)
fi
if [[ -n "${LOCATIONS:-}" ]]; then
  EXTRA_ARGS+=(--locations "${LOCATIONS}")
fi
if [[ -n "${GROUPS:-}" ]]; then
  EXTRA_ARGS+=(--groups "${GROUPS}")
fi
if [[ "${REQUIRE_LINUX_HOURLY:-0}" == "1" ]]; then
  EXTRA_ARGS+=(--require-linux-hourly)
fi

"${PYTHON_BIN}" "${ROOT_DIR}/scripts/hostkey_matrix.py" \
  --api-base "${HOSTKEY_API_BASE:-}" \
  --token "${HOSTKEY_TOKEN:-}" \
  --workers "${WORKERS:-12}" \
  --request-timeout "${REQUEST_TIMEOUT:-8}" \
  --out-json "${ROOT_DIR}/outputs/hostkey_candidates.json" \
  --out-csv "${ROOT_DIR}/outputs/hostkey_candidates.csv" \
  "${EXTRA_ARGS[@]}"
