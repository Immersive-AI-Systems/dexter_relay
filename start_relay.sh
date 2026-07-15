#!/usr/bin/env bash
set -euo pipefail

DEXTER_ADDRESS="CA:2B:20:4E:8E:0D"

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
    --playback-csv "$2" \
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

# BlueZ can keep a BLE peripheral connected after its client crashes. Dexter
# does not advertise while that stale connection exists, so release it before
# the relay starts scanning. It is harmless if Dexter is already disconnected.
bluetoothctl power on >/dev/null
if bluetoothctl info "$DEXTER_ADDRESS" 2>/dev/null \
  | grep -q 'Connected: yes'; then
  echo "Releasing stale Bluetooth connection to Dexter..."
  bluetoothctl disconnect "$DEXTER_ADDRESS" >/dev/null || true
  sleep 2
fi

exec "${relay_command[@]}" \
  --ble \
  --ble-address "$DEXTER_ADDRESS" \
  --ble-adapter auto \
  --ble-scan-timeout 10 \
  --ble-connect-retries 3 \
  --ble-stale-timeout 3 \
  --ble-reconnect-initial-delay 1 \
  --ble-reconnect-max-delay 30 \
  --bind 0.0.0.0 \
  --port 45678
