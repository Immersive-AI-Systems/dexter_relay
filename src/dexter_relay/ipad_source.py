"""iPad touch-position input for the Dexter UDP relay."""

from __future__ import annotations

import json
import math
import socket
import time
from dataclasses import dataclass
from typing import Any, Mapping

from .protocol import FINGER_NAMES, MAX_DATAGRAM_BYTES


IPAD_PROTOCOL_NAME = "ipad-dexter-touch"
IPAD_PROTOCOL_VERSION = 2
IPAD_COORDINATE_SYSTEM = "target-offset-centimeters"
IPAD_DEFAULT_PORT = 5005
IPAD_ROLES = ("left", "right")
IPAD_PACKET_EVENTS = frozenset({"began", "moved", "ended", "cancelled"})
IPAD_TOUCH_STATES = IPAD_PACKET_EVENTS | {"stationary"}


@dataclass(frozen=True)
class _TouchSample:
    touch_id: int
    role: str
    x: float
    y: float
    active: bool
    state: str


@dataclass(frozen=True)
class _TouchPacket:
    session_id: str
    sequence: int
    timestamp: float
    event: str
    touches: tuple[_TouchSample, ...]


@dataclass(frozen=True)
class _LatestTouch:
    touch_id: int
    x: float
    y: float
    active: bool
    state: str
    last_update_ts: float


def _require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _require_integer(value: Any, field: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{field} must be an integer >= {minimum}")
    return value


def _require_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be a finite number")
    return result


def decode_ipad_packet(data: bytes) -> _TouchPacket:
    """Validate the subset of Dexter Touch protocol v2 consumed by the relay."""

    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("iPad datagram must be a UTF-8 JSON object") from exc
    if not isinstance(payload, dict):
        raise ValueError("iPad datagram JSON must be an object")

    protocol_name = _require_string(payload.get("protocol"), "protocol")
    if protocol_name != IPAD_PROTOCOL_NAME:
        raise ValueError(f"unsupported iPad protocol {protocol_name!r}")
    version = _require_integer(payload.get("version"), "version", minimum=1)
    if version != IPAD_PROTOCOL_VERSION:
        raise ValueError(f"unsupported iPad protocol version {version}")
    coordinate_system = _require_string(
        payload.get("coordinateSystem"), "coordinateSystem"
    )
    if coordinate_system != IPAD_COORDINATE_SYSTEM:
        raise ValueError(f"unsupported coordinate system {coordinate_system!r}")

    session_id = _require_string(payload.get("sessionId"), "sessionId")
    sequence = _require_integer(payload.get("sequence"), "sequence", minimum=1)
    timestamp = _require_number(payload.get("timestamp"), "timestamp")
    event = _require_string(payload.get("event"), "event")
    if event not in IPAD_PACKET_EVENTS:
        raise ValueError(f"unsupported iPad event {event!r}")

    raw_touches = payload.get("touches")
    if not isinstance(raw_touches, list) or not 1 <= len(raw_touches) <= 2:
        raise ValueError("touches must contain one or two objects")

    touches: list[_TouchSample] = []
    seen_roles: set[str] = set()
    seen_ids: set[int] = set()
    for index, raw_touch in enumerate(raw_touches):
        if not isinstance(raw_touch, dict):
            raise ValueError(f"touches[{index}] must be an object")
        touch_id = _require_integer(
            raw_touch.get("id"), f"touches[{index}].id", minimum=1
        )
        role = _require_string(raw_touch.get("role"), f"touches[{index}].role")
        if role not in IPAD_ROLES:
            raise ValueError(f"touches[{index}].role must be left or right")
        if role in seen_roles or touch_id in seen_ids:
            raise ValueError("touch roles and IDs must be unique within a packet")
        seen_roles.add(role)
        seen_ids.add(touch_id)

        x = _require_number(raw_touch.get("x"), f"touches[{index}].x")
        y = _require_number(raw_touch.get("y"), f"touches[{index}].y")
        active = raw_touch.get("active")
        if not isinstance(active, bool):
            raise ValueError(f"touches[{index}].active must be a boolean")
        state = _require_string(
            raw_touch.get("state"), f"touches[{index}].state"
        )
        if state not in IPAD_TOUCH_STATES:
            raise ValueError(f"unsupported touch state {state!r}")
        if active == (state in {"ended", "cancelled"}):
            raise ValueError("ended/cancelled touches must be inactive")

        touches.append(_TouchSample(touch_id, role, x, y, active, state))

    return _TouchPacket(session_id, sequence, timestamp, event, tuple(touches))


class IpadTouchSource:
    """Receive iPad XY offsets and expose them through relay finger vectors.

    The existing ``force`` vector field is retained for compatibility with relay
    clients, but frames explicitly identify the measurement as a position in cm.
    """

    transport = "ipad"
    measurement_kind = "position"
    units = "cm"

    def __init__(
        self,
        *,
        bind_host: str = "0.0.0.0",
        port: int = IPAD_DEFAULT_PORT,
        role_mapping: Mapping[str, str] | None = None,
    ) -> None:
        if not 0 <= port <= 65_535:
            raise ValueError("ipad port must be between 0 and 65535")

        mapping = dict(role_mapping or {"left": "index", "right": "middle"})
        if set(mapping) != set(IPAD_ROLES):
            raise ValueError("iPad role mapping must define left and right")
        if any(finger not in FINGER_NAMES for finger in mapping.values()):
            raise ValueError("iPad roles must map to known Dexter fingers")
        if len(set(mapping.values())) != len(mapping):
            raise ValueError("left and right must map to different Dexter fingers")

        self.bind_host = bind_host
        self.port = int(port)
        self.role_mapping = mapping
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._socket.bind((self.bind_host, self.port))
            self._socket.setblocking(False)
        except Exception:
            self._socket.close()
            raise

        self._closed = False
        self._touches: dict[str, _LatestTouch] = {}
        self._source_key: tuple[str, str] | None = None
        self._last_sequences: dict[tuple[str, str], int] = {}
        self._sender: tuple[str, int] | None = None
        self._source_sequence: int | None = None
        self._source_timestamp: float | None = None
        self._event: str | None = None
        self._received_at: float | None = None
        self._accepted = 0
        self._invalid = 0
        self._out_of_order = 0

    @property
    def address(self) -> tuple[str, int]:
        host, port = self._socket.getsockname()[:2]
        return str(host), int(port)

    def read_snapshot(self) -> dict[str, Any]:
        self._drain_datagrams()
        now = time.time()
        role_by_finger = {finger: role for role, finger in self.role_mapping.items()}
        fingers: dict[str, dict[str, Any]] = {}

        for finger in FINGER_NAMES:
            role = role_by_finger.get(finger)
            touch = self._touches.get(role) if role is not None else None
            has_data = bool(touch is not None and touch.active)
            measurement: dict[str, Any] = {
                "raw": [],
                "force": [touch.x, touch.y] if touch is not None else [],
                "channels": 2 if role is not None else 0,
                "has_data": has_data,
                "last_update_ts": touch.last_update_ts if touch is not None else 0.0,
                "age_s": (
                    max(0.0, now - touch.last_update_ts)
                    if touch is not None
                    else None
                ),
                "measurement_kind": self.measurement_kind,
                "units": self.units,
                "mapped": role is not None,
            }
            if role is not None:
                measurement["ipad_role"] = role
            if touch is not None:
                measurement.update(
                    {
                        "touch_id": touch.touch_id,
                        "touch_state": touch.state,
                    }
                )
            fingers[finger] = measurement

        sender = (
            {"host": self._sender[0], "port": self._sender[1]}
            if self._sender is not None
            else None
        )
        return {
            "transport": self.transport,
            "measurement_kind": self.measurement_kind,
            "units": self.units,
            "timestamp": self._source_timestamp or now,
            "fingers": fingers,
            "status": {
                "ports": {
                    "available": [f"udp://{self.address[0]}:{self.address[1]}"],
                    "unavailable": [],
                },
                "fingers": {
                    name: {
                        "has_data": measurement["has_data"],
                        "last_update_ts": measurement["last_update_ts"],
                        "mapped": measurement["mapped"],
                    }
                    for name, measurement in fingers.items()
                },
                "ipad": {
                    "protocol": IPAD_PROTOCOL_NAME,
                    "version": IPAD_PROTOCOL_VERSION,
                    "coordinate_system": IPAD_COORDINATE_SYSTEM,
                    "listen_host": self.address[0],
                    "listen_port": self.address[1],
                    "role_mapping": dict(self.role_mapping),
                    "sender": sender,
                    "session_id": self._source_key[1] if self._source_key else None,
                    "source_sequence": self._source_sequence,
                    "source_timestamp": self._source_timestamp,
                    "event": self._event,
                    "received_at": self._received_at,
                    "accepted": self._accepted,
                    "invalid": self._invalid,
                    "out_of_order": self._out_of_order,
                },
            },
        }

    def _drain_datagrams(self) -> None:
        while not self._closed:
            try:
                data, address = self._socket.recvfrom(MAX_DATAGRAM_BYTES)
            except BlockingIOError:
                return
            except OSError:
                if self._closed:
                    return
                raise

            try:
                packet = decode_ipad_packet(data)
            except ValueError:
                self._invalid += 1
                continue
            self._apply_packet(packet, (str(address[0]), int(address[1])))

    def _apply_packet(
        self, packet: _TouchPacket, sender: tuple[str, int]
    ) -> None:
        source_key = (sender[0], packet.session_id)
        previous_sequence = self._last_sequences.get(source_key)
        if previous_sequence is not None and packet.sequence <= previous_sequence:
            self._out_of_order += 1
            return

        if source_key != self._source_key:
            self._touches.clear()
            self._source_key = source_key

        received_at = time.time()
        for touch in packet.touches:
            self._touches[touch.role] = _LatestTouch(
                touch.touch_id,
                touch.x,
                touch.y,
                touch.active,
                touch.state,
                received_at,
            )

        self._sender = sender
        self._last_sequences[source_key] = packet.sequence
        self._source_sequence = packet.sequence
        self._source_timestamp = packet.timestamp
        self._event = packet.event
        self._received_at = received_at
        self._accepted += 1

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._socket.close()
