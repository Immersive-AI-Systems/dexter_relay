# dexter-relay

`dexter-relay` reads Dexter load-cell data through the `dexter_controller` Python package, converts raw channel values to force vectors using the same path as the .NET visualizer, and publishes the latest measurements to any number of UDP clients.

This is pure Python. The .NET visualizer is used only as a reference source for porting the math and BLE finger ordering; no .NET runtime, assemblies, commands, or packages are used by this project. The real Dexter Python dependency is loaded only by the relay server. Local tests for conversion and protocol code do not require hardware.

## Install

```bash
python -m pip install -e .
```

This also installs `dexter-controller` directly from `github.com/fchampalimaud/dexter-controller`, using the `python/dexter-controller` package subdirectory.

## Run the relay

The default server mode is BLE, matching the .NET visualizer's all-five-finger 3-channel path:

```bash
python -m dexter_relay.server
```

You can also pass `--ble` explicitly:

```bash
python -m dexter_relay.server --ble
```

Serial devices use one `--map PORT:finger[,finger]` entry per load-cell device:

```bash
python -m dexter_relay.server --serial --map COM20:thumb,index --map COM5:middle,ring --map COM8:pinky
```

### iPad touch input

Use `--ipad` to receive target-relative XY positions from the Dexter Touch iPad
app instead of opening Dexter BLE or serial hardware:

```bash
python -m dexter_relay.server --ipad
```

The relay listens for iPad protocol-v2 packets on `0.0.0.0:5005` and continues
serving relay subscribers on `0.0.0.0:45678`. These are separate UDP ports. The
iPad app should therefore send to the relay machine's LAN IP on port `5005`.

By default, the iPad's `left` role is published in the Dexter `index` field and
the `right` role in `middle`. Remap them when needed:

```bash
python -m dexter_relay.server --ipad --ipad-left-finger thumb --ipad-right-finger index
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

By default the relay binds UDP `0.0.0.0:45678`, publishes at 20 Hz, downsamples BLE input to the same rate, and forgets clients that stop renewing their subscription for 5 seconds. The default bind address is network-visible, so clients on other machines can subscribe using the server machine's IP address.

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
python -m dexter_relay.server --simulate
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

BLE 3-channel readings produce `[x, y]` in Newtons, matching `DexterController.Visualizer` (`data[f].ToForce3()` and `FingerForces[f].AddForce(force.X, force.Y)`). Serial 4-channel readings are also supported for the Python controller's explicit serial mapping and produce `[x, y, z]`.

In `--ipad` mode, the mapped `force` vectors contain `[x, y]` target offsets in
centimeters for backward compatibility. Check the frame-level
`measurement_kind` and `units` fields before labeling or scaling a vector.

## Visualizer reference

The relay follows `/Users/nova/git/dexter-controller/dotnet/DexterController.Visualizer`:

- `MainWindowViewModel.OnDataUpdated` treats each finger as 3 raw channels.
- The visualizer computes force with `data[f].ToForce3()` and displays `force.X` / `force.Y`.
- `ToForce3()` delegates to `DexterController.Measurements.ForceConverter.ComposeForce3`, so this project ports that math exactly, including `raw[0] -> lc2`, `raw[1] -> lc3`, `raw[2] -> lc1`.
- BLE payload finger slicing follows `DexterController.BLE.DexterHandController.OnPayloadReceived`: `thumb=payload[12:15]`, `index=payload[9:12]`, `middle=payload[6:9]`, `ring=payload[3:6]`, `pinky=payload[0:3]`.
- Raw samples are interpreted as signed Int16 values, matching `FingerData.AsInt16()` in the .NET code.

## Tests

```bash
python -m unittest discover -s tests
```
