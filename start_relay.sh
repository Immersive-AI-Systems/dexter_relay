#!/usr/bin/env bash
set -euo pipefail

if command -v dexter-relay-server >/dev/null 2>&1; then
  relay_command=(dexter-relay-server)
else
  relay_command=(python -m dexter_relay.server)
fi

if [[ $# -gt 0 ]]; then
  if [[ $# -ne 2 || ( "$1" != "--csv" && "$1" != "--playback-csv" ) ]]; then
    echo "usage: $0 [--csv PATH]" >&2
    exit 2
  fi
  exec "${relay_command[@]}" \
    --source recording \
    --recording "$2" \
    --send-hz 20 \
    --bind 0.0.0.0 \
    --port 45678
fi

if ! systemctl is-active --quiet bluetooth; then
  echo "error: Ubuntu Bluetooth service is not running" >&2
  echo "start it with: sudo systemctl start bluetooth" >&2
  exit 1
fi

if ! command -v bluetoothctl >/dev/null 2>&1; then
  echo "error: bluetoothctl is required but was not found" >&2
  exit 1
fi

# The relay releases stale BlueZ sessions itself, then scans every Linux HCI
# adapter for the advertised Dexter name. Do not pin the randomized BLE address.
bluetoothctl power on >/dev/null

exec "${relay_command[@]}" \
  --source ble \
  --ble-adapter auto \
  --ble-scan-timeout 10 \
  --ble-connect-retries 3 \
  --ble-stale-timeout 3 \
  --ble-reconnect-initial-delay 1 \
  --ble-reconnect-max-delay 30 \
  --bind 0.0.0.0 \
  --port 45678
