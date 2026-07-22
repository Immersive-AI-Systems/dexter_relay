# dexter-relay

`dexter-relay` is a Python UDP server for sharing Dexter finger measurements over a network. It can read Dexter hardware over BLE or serial, receive calibrated two-finger XY positions from an iPad, replay recorded relay frames, or generate simulated measurements. Every source is published through the same protocol to multiple subscribed terminal, Unity, or custom clients.

## Install

For the terminal client and the iPad, recording, or simulation sources:

```bash
python -m pip install -e .
```

For the Dexter hardware relay server:

```bash
python -m pip install -e ".[server]"
```

The `server` extra installs `dexter-controller>=0.2.2` from public PyPI. There are no install-time dependencies on local checkouts or private GitHub URLs.

## Source types

Select the input with `--source`. Available sources are `ble`, `serial`, `ipad`,
`recording`, and `simulation`; the default is `ble`.

### BLE (default)

BLE reads three load-cell channels from each of the five Dexter fingers. These
two commands are equivalent:

```bash
python -m dexter_relay.server
```

```bash
python -m dexter_relay.server --source ble
```

Use `--ble-address`, `--ble-scan-timeout`, and `--ble-connect-retries` when the
default discovery behavior needs adjustment.

### Serial

Serial uses `dexter_controller.DexterHandController`. Provide one
`--map PORT:finger[,finger]` entry per load-cell device; each serial device can
serve at most two fingers:

```bash
python -m dexter_relay.server --source serial --map COM20:thumb,index --map COM5:middle,ring --map COM8:pinky
```

### iPad

The iPad source receives target-relative XY positions from the Dexter Touch app
instead of opening Dexter hardware:

```bash
python -m dexter_relay.server --source ipad
```

The native Swift client and ready-to-open Xcode project are included in
[`clients/ipad`](clients/ipad/README.md). Enter the relay machine's LAN IP and
port `5005` in the app, then tap **Start Sending**.

The relay listens for iPad protocol-v2 packets on `0.0.0.0:5005` and continues
serving relay subscribers on `0.0.0.0:45678`. These are separate UDP ports. The
iPad app should therefore send to the relay machine's LAN IP on port `5005`.

By default, incoming iPad XY values are multiplied by `5` before they are sent
to Unity, and the relay terminal prints one latest-value line per second showing
both the original iPad coordinates and the scaled Unity coordinates. Override
these defaults with `--ipad-scale` and `--ipad-print-interval` when needed:

```bash
python -m dexter_relay.server --source ipad --ipad-scale 5 --ipad-print-interval 1
```

By default, the iPad's `left` role is published in the Dexter `index` field and
the `right` role in `middle`. Remap them when needed:

```bash
python -m dexter_relay.server --source ipad --ipad-left-finger thumb --ipad-right-finger index
```

Use `--ipad-bind` and `--ipad-port` to change the incoming iPad listener. The
source validates protocol name/version, coordinate system, lifecycle state,
session ID, and sequence number; duplicate and reordered packets are ignored.
An `ended` or `cancelled` touch immediately clears its mapped finger.

For compatibility, iPad XY values remain in each mapped finger's existing
`force` vector field. Frames explicitly set `transport` to `ipad`,
`measurement_kind` to `position`, and `units` to `cm`, so consumers must not
interpret those vectors as Newtons. Unmapped Dexter fingers report
`has_data: false`.

### Recording

Recording playback requires a path:

```bash
python -m dexter_relay.server --source recording --recording recordings/session.jsonl
```

The file may contain one relay `force` frame per line, a JSON array of frames,
or a JSON object with a `frames` array. Playback advances one frame per relay
publish tick, controlled by `--send-hz` and defaulting to `20` frames per
second. It loops by default; use `--no-recording-loop` to hold the final frame:

```bash
python -m dexter_relay.server --source recording --recording recordings/session.jsonl --no-recording-loop
```

Replayed frames use `transport: "recording"`. Their original transport,
timestamp, sequence, path, and playback index are available under
`status.recording`.

### Simulation

Simulation generates deterministic force waveforms without Dexter hardware:

```bash
python -m dexter_relay.server --source simulation
```

It produces four-channel measurements by default. Use
`--simulation-channels 3` to exercise the three-channel conversion path.

## Network behavior

For every source, the relay binds UDP `0.0.0.0:45678`, publishes at `20 Hz`,
and forgets clients that stop renewing their subscription for 5 seconds. BLE
input is downsampled to the publish rate, while recording playback advances one
frame per publish tick. The default bind address is network-visible, so clients
on other machines can subscribe using the relay machine's LAN IP address.

On Windows, `45678/udp` is intentionally unprivileged, below the usual Windows dynamic port range, and away from common service ports. Windows Defender Firewall may still prompt the first time Python accepts inbound UDP; allow it on the intended private network.

### Windows Firewall

If Windows does not prompt automatically, allow inbound UDP traffic on the relay port from an Administrator PowerShell:

```powershell
New-NetFirewallRule -DisplayName "Dexter Relay UDP 45678" -Direction Inbound -Action Allow -Protocol UDP -LocalPort 45678 -Profile Private
```

If the relay must be reachable on a non-private network profile, replace `Private` with `Domain`, `Public`, or `Any`. Prefer `Private` when the Dexter clients are on the same trusted LAN.

You can also add it through the UI: open **Windows Security** -> **Firewall & network protection** -> **Advanced settings** -> **Inbound Rules** -> **New Rule...** -> **Port** -> **UDP** -> **Specific local ports: 45678** -> **Allow the connection** -> select the intended profile -> name it `Dexter Relay UDP 45678`.

## Attach a terminal client

```bash
python -m dexter_relay.client --host 127.0.0.1 --port 45678
```

The client sends a UDP subscribe packet, receives the full stream, and prints the latest force values once per second. On Ctrl+C it sends a best-effort UDP `unsubscribe` packet so the relay can remove it immediately.

## Unity Receiver Demo

A minimal Unity receiver is included in [`unity_demo`](unity_demo/README.md). It is a drop-in asset folder with a single `DexterRelayUdpReceiver` component that subscribes to the relay, receives force frames, displays a simple on-screen overlay, and sends `unsubscribe` on shutdown.

Use `127.0.0.1` when Unity runs on the relay machine. Use the relay machine's LAN IP address when Unity runs on another computer.

## Local smoke test without hardware

```bash
python -m dexter_relay.server --source simulation
```

Then start the client in another terminal:

```bash
python -m dexter_relay.client --host 127.0.0.1 --port 45678
```

## UDP protocol

Clients attach by sending JSON to the server address:

```json
{"type":"subscribe","version":1,"client":"dexter-relay-client"}
```

Clients can detach gracefully by sending:

```json
{"type":"unsubscribe","version":1,"client":"dexter-relay-client"}
```

Because UDP has no real connection state, graceful disconnect is best-effort. If a client process crashes, loses network, or cannot send `unsubscribe`, the relay automatically forgets it after `--client-ttl` seconds.

The server replies with `ack` and then sends `force` frames:

```json
{
  "type": "force",
  "version": 1,
  "sequence": 42,
  "timestamp": 1783500000.123,
  "transport": "serial",
  "measurement_kind": "force",
  "units": "N",
  "fingers": {
    "thumb": {
      "raw": [1, 2, 3, 4],
      "force": [0.723, 0.503, -2.008],
      "channels": 4,
      "has_data": true,
      "last_update_ts": 1783500000.120,
      "age_s": 0.003
    }
  }
}
```

BLE 3-channel readings produce `[x, y]` force vectors in Newtons. Serial 4-channel readings are also supported for the Python controller's explicit serial mapping and produce `[x, y, z]`.

With `--source ipad`, the mapped `force` vectors contain the scaled `[x, y]`
target offsets for backward compatibility. The default scale is `5`, and its
value is also reported as `status.ipad.value_scale`. Check the frame-level
`measurement_kind` and `units` fields before labeling the vector.

## Tests

```bash
python -m unittest discover -s tests
```
