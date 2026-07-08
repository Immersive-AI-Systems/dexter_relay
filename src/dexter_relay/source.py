"""Measurement sources used by the UDP relay."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from .conversion import compose_force, signed_int16_values


FINGER_NAMES = ("thumb", "index", "middle", "ring", "pinky")
VISUALIZER_BLE_OFFSETS = {
    "thumb": 12,
    "index": 9,
    "middle": 6,
    "ring": 3,
    "pinky": 0,
}


class ForceSource(Protocol):
    transport: str

    def read_snapshot(self) -> dict[str, Any]:
        ...

    def close(self) -> None:
        ...


@dataclass(frozen=True)
class ParsedMapping:
    by_port: dict[str, tuple[str, ...]]


def parse_mapping_specs(specs: Sequence[str] | None) -> ParsedMapping:
    by_port: dict[str, tuple[str, ...]] = {}
    assigned_fingers: set[str] = set()

    for spec in specs or ():
        if ":" not in spec:
            raise ValueError(
                f"invalid mapping {spec!r}; expected PORT:finger[,finger]"
            )

        port, fingers_text = spec.split(":", 1)
        port = port.strip()
        if not port:
            raise ValueError(f"invalid mapping {spec!r}; port cannot be empty")
        if port in by_port:
            raise ValueError(f"duplicate mapping for port {port!r}")

        fingers = tuple(
            finger.strip().lower() for finger in fingers_text.split(",") if finger.strip()
        )
        if not fingers:
            raise ValueError(f"invalid mapping {spec!r}; at least one finger is required")
        if len(fingers) > 2:
            raise ValueError(
                f"invalid mapping {spec!r}; serial devices expose at most two fingers"
            )

        for finger in fingers:
            if finger not in FINGER_NAMES:
                expected = ", ".join(FINGER_NAMES)
                raise ValueError(f"unknown finger {finger!r}; expected one of {expected}")
            if finger in assigned_fingers:
                raise ValueError(f"duplicate finger mapping for {finger!r}")
            assigned_fingers.add(finger)

        by_port[port] = fingers

    return ParsedMapping(by_port=by_port)


def visualizer_ble_finger_slices(payload: Sequence[int]) -> dict[str, list[int]]:
    """Slice a 15-load-cell BLE payload using DexterController.Visualizer order."""

    if len(payload) < 15:
        raise ValueError("BLE payload must have at least 15 load-cell values")

    return {
        name: list(payload[offset : offset + 3])
        for name, offset in VISUALIZER_BLE_OFFSETS.items()
    }


def _force_measurement(
    raw: Sequence[int],
    *,
    has_data: bool,
    last_update_ts: float,
    now: float,
) -> dict[str, Any]:
    signed_raw = list(signed_int16_values(raw))
    force = list(compose_force(signed_raw)) if len(signed_raw) >= 3 else []
    return {
        "raw": signed_raw,
        "force": force,
        "channels": len(signed_raw),
        "has_data": bool(has_data),
        "last_update_ts": float(last_update_ts),
        "age_s": max(0.0, now - last_update_ts) if last_update_ts else None,
    }


class DexterForceSource:
    """Force source backed by the `dexter_controller` package.

    BLE mode consumes `BLELoadCellDevice` directly so finger slicing follows the
    .NET visualizer exactly:

    - thumb: payload[12:15]
    - index: payload[9:12]
    - middle: payload[6:9]
    - ring: payload[3:6]
    - pinky: payload[0:3]

    Serial mode uses `DexterHandController` with the explicit port mapping.
    """

    def __init__(
        self,
        *,
        mapping_specs: Sequence[str] | None = None,
        use_ble: bool = False,
        ble_scan_timeout: float = 1.0,
    ) -> None:
        self.transport = "ble" if use_ble else "serial"
        self._controller = None
        self._device = None
        self._finger_by_name: dict[str, Any] = {}
        self._ble_raw_by_name = {name: [0, 0, 0] for name in FINGER_NAMES}
        self._ble_has_data = {name: False for name in FINGER_NAMES}
        self._ble_last_update_ts = {name: 0.0 for name in FINGER_NAMES}
        self._ble_counter: int | None = None
        self._ble_timestamp_us: int | None = None

        if use_ble:
            try:
                from dexter_controller import BLELoadCellDevice
            except ModuleNotFoundError as exc:
                raise RuntimeError(
                    "dexter_controller is not installed in this Python environment"
                ) from exc

            self._device = BLELoadCellDevice(scan_timeout=ble_scan_timeout)
        else:
            try:
                from dexter_controller import DexterHandController, Finger
            except ModuleNotFoundError as exc:
                raise RuntimeError(
                    "dexter_controller is not installed in this Python environment"
                ) from exc

            self._finger_by_name = {
                name: getattr(Finger, name.upper()) for name in FINGER_NAMES
            }
            parsed = parse_mapping_specs(mapping_specs)
            if not parsed.by_port:
                raise ValueError(
                    "serial mode requires at least one --map PORT:finger[,finger]"
                )

            mapping = {
                port: [self._finger_by_name[finger] for finger in fingers]
                for port, fingers in parsed.by_port.items()
            }
            self._controller = DexterHandController(mapping=mapping)

    def read_snapshot(self) -> dict[str, Any]:
        if self.transport == "ble":
            return self._read_ble_snapshot()
        return self._read_serial_snapshot()

    def _read_serial_snapshot(self) -> dict[str, Any]:
        now = time.time()
        fingers: dict[str, Any] = {}

        for name in FINGER_NAMES:
            finger = self._finger_by_name[name]
            data = self._controller.finger_data[finger]
            raw = list(getattr(data, "raw_data", ()) or ())
            has_data = self._controller.finger_has_data(finger)
            last_update_ts = self._controller.finger_last_update_ts(finger)
            fingers[name] = _force_measurement(
                raw,
                has_data=has_data,
                last_update_ts=last_update_ts,
                now=now,
            )

        return {
            "transport": self.transport,
            "timestamp": now,
            "fingers": fingers,
            "status": _normalize_controller_status(self._controller.get_status()),
        }

    def _read_ble_snapshot(self) -> dict[str, Any]:
        now = time.time()
        self._drain_ble_events(now)

        fingers = {
            name: _force_measurement(
                self._ble_raw_by_name[name],
                has_data=self._ble_has_data[name],
                last_update_ts=self._ble_last_update_ts[name],
                now=now,
            )
            for name in FINGER_NAMES
        }

        identifier = getattr(self._device, "identifier", "BLE:Dexter")
        return {
            "transport": self.transport,
            "timestamp": now,
            "fingers": fingers,
            "status": {
                "ports": {"available": [identifier], "unavailable": []},
                "fingers": {
                    name: {
                        "has_data": self._ble_has_data[name],
                        "last_update_ts": self._ble_last_update_ts[name],
                        "mapped": True,
                    }
                    for name in FINGER_NAMES
                },
                "ble": {
                    "counter": self._ble_counter,
                    "timestamp_us": self._ble_timestamp_us,
                    "finger_order": "DexterController.Visualizer",
                },
            },
        }

    def _drain_ble_events(self, now: float) -> None:
        for event in self._device.get_events():
            payload = list(getattr(event, "payload", ()) or ())
            if len(payload) < 15:
                continue

            for name, raw in visualizer_ble_finger_slices(payload).items():
                self._ble_raw_by_name[name] = raw
                self._ble_has_data[name] = True
                self._ble_last_update_ts[name] = now

            self._ble_counter = getattr(event, "counter", None)
            self._ble_timestamp_us = getattr(event, "timestamp_us", None)

    def close(self) -> None:
        if self._controller is not None:
            self._controller.close()
        if self._device is not None:
            self._device.close()


class SimulatedForceSource:
    """Deterministic local source for smoke testing the UDP relay."""

    def __init__(self, *, channels: int = 4) -> None:
        if channels not in (3, 4):
            raise ValueError("channels must be 3 or 4")
        self.channels = channels
        self.transport = f"simulate-{channels}ch"
        self._start = time.monotonic()

    def read_snapshot(self) -> dict[str, Any]:
        now = time.time()
        elapsed = time.monotonic() - self._start
        fingers: dict[str, Any] = {}

        for index, name in enumerate(FINGER_NAMES):
            phase = elapsed * 2.0 + index * 0.7
            if self.channels == 4:
                raw = [
                    int(400 * math.sin(phase)),
                    int(350 * math.cos(phase * 0.9)),
                    int(450 * math.sin(phase * 1.1 + 0.4)),
                    int(500 * math.cos(phase * 1.2 - 0.2)),
                ]
            else:
                raw = [
                    int(400 * math.sin(phase)),
                    int(420 * math.cos(phase * 1.1)),
                    int(380 * math.sin(phase * 0.8 + 0.5)),
                ]

            fingers[name] = _force_measurement(
                raw,
                has_data=True,
                last_update_ts=now,
                now=now,
            )

        return {
            "transport": self.transport,
            "timestamp": now,
            "fingers": fingers,
            "status": {
                "ports": {"available": [self.transport], "unavailable": []},
                "fingers": {
                    name: {"has_data": True, "last_update_ts": now, "mapped": True}
                    for name in FINGER_NAMES
                },
            },
        }

    def close(self) -> None:
        return None


def _normalize_controller_status(status: Mapping[str, Any]) -> dict[str, Any]:
    fingers = {}
    for key, value in status.get("fingers", {}).items():
        name = getattr(key, "name", str(key)).lower()
        fingers[name] = value

    return {
        "ports": status.get("ports", {}),
        "fingers": fingers,
    }
