# Dexter Touch iPad Client

This native Swift iPadOS app tracks two fingers, converts their positions to
calibrated target-relative centimeters, and sends protocol-v2 JSON datagrams to
the relay's `--source ipad` listener.

```text
iPad client --UDP 5005--> dexter-relay --UDP 45678--> subscribed clients
```

## Run

1. Start the relay:

   ```bash
   python -m dexter_relay.server --source ipad
   ```

2. Open `DexterTouch.xcodeproj` in Xcode.
3. Select the **DexterTouch** target, open **Signing & Capabilities**, and choose
   your Apple development team.
4. Connect and unlock the iPad, enable Developer Mode, and select it as the run
   destination.
5. Run the app and allow local-network access when prompted.
6. Enter the relay machine's LAN IP and UDP port `5005`.
7. Use **Calibrate** with a physical ruler, then tap **Start Sending**.

The app remembers the last host, port, and calibration. It sends only while
**Start Sending** is active and emits final `ended` or `cancelled` events so the
relay can clear inactive touches promptly.

## Packet Format

Each touch event is one UTF-8 JSON UDP datagram. Packets identify the
`ipad-dexter-touch` protocol version, sender session, sequence number, touch
lifecycle state, stable `left` or `right` role, and calibrated XY offset in
centimeters. See [PROTOCOL.md](PROTOCOL.md) for the complete schema.

The relay maps `left` to `index` and `right` to `middle` by default. These can
be changed with `--ipad-left-finger` and `--ipad-right-finger`.

## Build Check

The project targets iPadOS 16 or newer. A simulator build without code signing
can be checked from the repository root:

```bash
xcodebuild -project clients/ipad/DexterTouch.xcodeproj -scheme DexterTouch -configuration Debug -sdk iphonesimulator -derivedDataPath /tmp/dexter-relay-ipad-derived-data CODE_SIGNING_ALLOWED=NO build
```
