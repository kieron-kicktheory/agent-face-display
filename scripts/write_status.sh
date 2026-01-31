#!/usr/bin/env bash
# write_status.sh — Write agent status signal file for the face display watcher.
# Usage: write_status.sh <state> [detail]
# States: thinking, searching, reading, coding, composing, reviewing, executing, idle

set -euo pipefail

VALID_STATES="thinking searching reading coding composing reviewing executing idle"
STATUS_DIR="/tmp/clawdbot"
STATUS_FILE="${STATUS_DIR}/agent-status.json"
CONFIG_FILE="${HOME}/.agent-face/config.json"

# --- Usage ---
if [ $# -lt 1 ]; then
  echo "Usage: write_status.sh <state> [detail]" >&2
  echo "States: ${VALID_STATES}" >&2
  exit 1
fi

STATE="$1"
DETAIL="${2:-}"

# --- Validate state ---
VALID=0
for s in ${VALID_STATES}; do
  if [ "$s" = "$STATE" ]; then
    VALID=1
    break
  fi
done

if [ "$VALID" -eq 0 ]; then
  echo "Error: Invalid state '${STATE}'" >&2
  echo "Valid states: ${VALID_STATES}" >&2
  exit 1
fi

# --- Read agent name from config ---
AGENT="unknown"
if [ -f "$CONFIG_FILE" ]; then
  # Use python for reliable JSON parsing (available on macOS)
  AGENT=$(python3 -c "
import json, sys
try:
    with open('${CONFIG_FILE}') as f:
        print(json.load(f).get('agent', {}).get('name', 'unknown'))
except Exception:
    print('unknown')
" 2>/dev/null || echo "unknown")
fi

# --- Get timestamp ---
TS=$(date +%s)

# --- Ensure directory exists ---
mkdir -p "$STATUS_DIR"

# --- Write JSON atomically ---
TMPFILE=$(mktemp "${STATUS_DIR}/.agent-status.XXXXXX")
cat > "$TMPFILE" <<EOF
{"agent":"${AGENT}","state":"${STATE}","detail":"${DETAIL}","ts":${TS}}
EOF
mv -f "$TMPFILE" "$STATUS_FILE"
