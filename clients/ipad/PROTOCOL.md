# Dexter Touch UDP protocol v2

Each touch callback produces one UTF-8 JSON object in one UDP datagram. The app
tracks two direct (finger) touches. Both the touch ID and its `left`/`right` role
remain stable from `began` through `ended` or `cancelled`. A third simultaneous
touch and Apple Pencil input are ignored.

```json
{
  "protocol": "ipad-dexter-touch",
  "version": 2,
  "coordinateSystem": "target-offset-centimeters",
  "sessionId": "68006B75-F109-4804-B34E-8B37D6631E5C",
  "sequence": 42,
  "timestamp": 1783968912.125,
  "monotonicTime": 18433.551,
  "event": "moved",
  "touches": [
    {
      "id": 1,
      "role": "left",
      "x": 0.0,
      "y": 0.0,
      "normalizedX": 0.42,
      "normalizedY": 0.91,
      "active": true,
      "state": "moved"
    },
    {
      "id": 2,
      "role": "right",
      "x": -0.35,
      "y": 1.2,
      "normalizedX": 0.55,
      "normalizedY": 0.84,
      "active": true,
      "state": "stationary"
    }
  ],
  "view": {"width": 1194, "height": 710},
  "orientation": "landscapeLeft",
  "calibration": {
    "pointsPerCentimeter": 51.9685,
    "targetSeparationCm": 3.0
  }
}
```

Fields:

- `sessionId` changes whenever the sender is started. It lets receivers accept a
  sequence restart without confusing it with an old or reordered packet.
- `sequence` starts at 1 and increases once per transmitted event.
- `timestamp` is Unix time in seconds. `monotonicTime` is iPad uptime in seconds
  and is appropriate for intervals within one session.
- Packet `event` is `began`, `moved`, `ended`, or `cancelled`.
- `role` is `left` or `right`. When two fingers begin together, the leftmost is
  assigned left and the rightmost is assigned right. A single finger is assigned
  to its nearest X. The role does not change if fingers cross.
- Each touch `state` can also be `stationary` when the other tracked touch caused
  the event. `active` is false only on the touch's final `ended` or `cancelled`
  packet.
- `x` and `y` are signed centimeters relative to the center of the matching X.
  `(0, 0)` means the finger is exactly on its target. Positive `x` is right and
  positive `y` is up, matching a conventional Unity XY plane.
- `normalizedX` and `normalizedY` retain the top-left-origin `[0, 1]` coordinates
  from the full dark touch surface for diagnostics or alternate consumers.
- `calibration.pointsPerCentimeter` records the physical ruler setting used for
  conversion. The two target centers are exactly `targetSeparationCm` apart under
  that calibration.
- `view` reports the touch surface size in UIKit points for diagnostics only.

UDP may lose, duplicate, or reorder datagrams. Consumers should use the newest
sequence within each `(sender address, sessionId)` and clear a touch immediately
when its final lifecycle state arrives. For additional resilience to a lost final
packet, consumers may also expire active state after a short application-specific
timeout.
