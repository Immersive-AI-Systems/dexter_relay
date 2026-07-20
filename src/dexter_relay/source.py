"""Measurement sources used by the UDP relay."""

from __future__ import annotations

import asyncio
import math
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from .ble_support import (
    diagnose_ble_failure,
    discover_dexter_endpoint_sync,
    prepare_dexter_ble_sync,
)
from .conversion import compose_force, signed_int16_values
from .protocol import FINGER_NAMES
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


def _connect_ble_device(
    *,
    scan_timeout: float,
    retries: int,
    ble_address: str | None = None,
    ble_adapter: str | None = None,
):
    """Connect to Dexter over BLE, retrying when advertising is intermittent."""

    from .ble_device import RelayBLELoadCellDevice

    if retries < 1:
        raise ValueError("ble_connect_retries must be at least 1")

    auto_adapter = sys.platform.startswith("linux") and ble_adapter == "auto"
    resolved_address = prepare_dexter_ble_sync(
        ble_address=ble_address,
        discovery_timeout=scan_timeout,
        discover=not auto_adapter,
    )
    resolved_adapter = ble_adapter
    if auto_adapter:
        endpoint = discover_dexter_endpoint_sync(
            address=ble_address,
            discovery_timeout=scan_timeout,
        )
        if endpoint is None:
            raise RuntimeError(
                "Dexter is not advertising on any available Bluetooth adapter; "
                "power-cycle Dexter and try again"
            )
        resolved_adapter, resolved_address = endpoint
        print(
            f"Using Bluetooth adapter {resolved_adapter} for Dexter "
            f"at {resolved_address}"
        )

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        if attempt > 1:
            prepare_dexter_ble_sync(
                ble_address=resolved_address or ble_address,
                discovery_timeout=scan_timeout,
            )
        try:
            device = RelayBLELoadCellDevice(
                scan_timeout=scan_timeout,
                ble_address=resolved_address or ble_address,
                ble_adapter=resolved_adapter,
            )
            return device
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(min(1.0, scan_timeout * 0.2))

    message = asyncio.run(
        diagnose_ble_failure(
            address=resolved_address,
            scan_timeout=scan_timeout,
            retries=retries,
            last_error=last_error,
        )
    )
    raise RuntimeError(message) from last_error


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
        ble_scan_timeout: float = 5.0,
        ble_connect_retries: int = 3,
        ble_address: str | None = None,
        ble_adapter: str | None = None,
        ble_sample_hz: float | None = None,
        ble_stale_timeout: float = 3.0,
        ble_reconnect_initial_delay: float = 1.0,
        ble_reconnect_max_delay: float = 30.0,
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
        self._ble_sample_sequence = 0
        self._ble_connection_state = "connecting" if use_ble else "disabled"
        self._ble_reconnect_count = 0
        self._ble_last_error: str | None = None
        self._ble_last_event_monotonic = time.monotonic()
        self._ble_next_reconnect_monotonic = 0.0
        self._ble_lock = threading.Lock()
        self._ble_stop_event = threading.Event()
        self._ble_sampler_thread: threading.Thread | None = None
        if ble_sample_hz is not None and ble_sample_hz <= 0:
            raise ValueError("ble_sample_hz must be greater than 0")
        if ble_stale_timeout <= 0:
            raise ValueError("ble_stale_timeout must be greater than 0")
        if ble_reconnect_initial_delay <= 0:
            raise ValueError("ble_reconnect_initial_delay must be greater than 0")
        if ble_reconnect_max_delay < ble_reconnect_initial_delay:
            raise ValueError(
                "ble_reconnect_max_delay must be at least the initial delay"
            )
        self._ble_sample_hz = ble_sample_hz
        self._ble_stale_timeout = ble_stale_timeout
        self._ble_reconnect_initial_delay = ble_reconnect_initial_delay
        self._ble_reconnect_max_delay = ble_reconnect_max_delay
        self._ble_reconnect_delay = ble_reconnect_initial_delay
        self._ble_connect_args = {
            "scan_timeout": ble_scan_timeout,
            "retries": ble_connect_retries,
            "ble_address": ble_address,
            "ble_adapter": ble_adapter,
        }

        if use_ble:
            try:
                from dexter_controller import BLELoadCellDevice
            except ModuleNotFoundError as exc:
                raise RuntimeError(
                    "dexter_controller is not installed in this Python environment"
                ) from exc

            self._device = _connect_ble_device(
                **self._ble_connect_args,
            )
            self._ble_connection_state = "connected"
            self._ble_last_event_monotonic = time.monotonic()
            if self._ble_sample_hz is not None:
                self._ble_sampler_thread = threading.Thread(
                    target=self._ble_sampler_loop,
                    name="dexter-relay-ble-sampler",
                    daemon=True,
                )
                self._ble_sampler_thread.start()
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
        if self._ble_sample_hz is None:
            self._drain_ble_events(now)

        with self._ble_lock:
            fingers = {
                name: _force_measurement(
                    self._ble_raw_by_name[name],
                    has_data=self._ble_has_data[name],
                    last_update_ts=self._ble_last_update_ts[name],
                    now=now,
                )
                for name in FINGER_NAMES
            }
            ble_counter = self._ble_counter
            ble_timestamp_us = self._ble_timestamp_us
            ble_sample_hz = self._ble_sample_hz
            ble_sample_sequence = self._ble_sample_sequence
            ble_connection_state = self._ble_connection_state
            ble_reconnect_count = self._ble_reconnect_count
            ble_last_error = self._ble_last_error
            ble_last_event_monotonic = self._ble_last_event_monotonic

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
                    "counter": ble_counter,
                    "timestamp_us": ble_timestamp_us,
                    "sample_hz": ble_sample_hz,
                    "sample_sequence": ble_sample_sequence,
                    "connection_state": ble_connection_state,
                    "reconnect_count": ble_reconnect_count,
                    "last_error": ble_last_error,
                    "last_sample_age_s": max(
                        0.0, time.monotonic() - ble_last_event_monotonic
                    ),
                    "finger_order": "DexterController.Visualizer",
                },
            },
        }

    def _apply_ble_event(self, event: Any, now: float) -> None:
        payload = list(getattr(event, "payload", ()) or ())
        if len(payload) < 15:
            return

        with self._ble_lock:
            for name, raw in visualizer_ble_finger_slices(payload).items():
                self._ble_raw_by_name[name] = raw
                self._ble_has_data[name] = True
                self._ble_last_update_ts[name] = now

            self._ble_counter = getattr(event, "counter", None)
            self._ble_timestamp_us = getattr(event, "timestamp_us", None)
            self._ble_sample_sequence += 1

    def _ble_sampler_loop(self) -> None:
        assert self._ble_sample_hz is not None
        interval_s = 1.0 / self._ble_sample_hz
        next_tick = time.monotonic()

        while not self._ble_stop_event.is_set():
            now_mono = time.monotonic()
            if now_mono >= next_tick:
                if self._accept_latest_ble_event(time.time()):
                    with self._ble_lock:
                        self._ble_last_event_monotonic = now_mono
                        self._ble_connection_state = "connected"
                        self._ble_last_error = None
                    self._ble_reconnect_delay = self._ble_reconnect_initial_delay
                elif now_mono - self._ble_last_event_monotonic >= self._ble_stale_timeout:
                    self._maybe_reconnect_ble(now_mono)

                next_tick += interval_s
                if next_tick < now_mono:
                    next_tick = now_mono + interval_s

            sleep_for = min(0.01, max(0.0, next_tick - time.monotonic()))
            if sleep_for:
                self._ble_stop_event.wait(sleep_for)

    def _maybe_reconnect_ble(self, now_mono: float) -> bool:
        if now_mono < self._ble_next_reconnect_monotonic:
            return False

        with self._ble_lock:
            self._ble_connection_state = "reconnecting"

        old_device = self._device
        self._device = None
        if old_device is not None:
            try:
                old_device.close()
            except Exception as exc:
                print(f"warning: failed closing stale Dexter BLE connection: {exc}")

        if self._ble_stop_event.is_set():
            return False

        print("Dexter BLE samples stopped; attempting automatic reconnect...")
        reconnect_args = dict(self._ble_connect_args)
        reconnect_args["retries"] = 1
        try:
            self._device = _connect_ble_device(**reconnect_args)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            with self._ble_lock:
                self._ble_connection_state = "disconnected"
                self._ble_last_error = error
            delay = self._ble_reconnect_delay
            self._ble_next_reconnect_monotonic = time.monotonic() + delay
            self._ble_reconnect_delay = min(
                self._ble_reconnect_max_delay,
                max(delay * 2.0, self._ble_reconnect_initial_delay),
            )
            print(f"Dexter BLE reconnect failed; retrying in {delay:g}s: {error}")
            return False

        with self._ble_lock:
            self._ble_connection_state = "connected"
            self._ble_last_error = None
            self._ble_reconnect_count += 1
            self._ble_last_event_monotonic = time.monotonic()
        self._ble_next_reconnect_monotonic = 0.0
        self._ble_reconnect_delay = self._ble_reconnect_initial_delay
        print("Dexter BLE reconnected successfully")
        return True

    def _accept_latest_ble_event(self, now: float) -> bool:
        device = self._device
        if device is None:
            return False
        events = list(device.get_events())
        if not events:
            return False

        self._apply_ble_event(events[-1], now)
        return True

    def _drain_ble_events(self, now: float) -> None:
        for event in self._device.get_events():
            self._apply_ble_event(event, now)

    def close(self) -> None:
        self._ble_stop_event.set()
        if self._ble_sampler_thread is not None:
            self._ble_sampler_thread.join(timeout=2.0)
            self._ble_sampler_thread = None
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
