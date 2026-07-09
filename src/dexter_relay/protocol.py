"""Small JSON-over-UDP protocol helpers."""

from __future__ import annotations

import json
import uuid
from typing import Any, Mapping


PROTOCOL_VERSION = 1
MAX_DATAGRAM_BYTES = 65535
DEFAULT_UDP_PORT = 45678
FINGER_NAMES = ("thumb", "index", "middle", "ring", "pinky")


def encode_datagram(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def decode_datagram(data: bytes) -> dict[str, Any]:
    payload = json.loads(data.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("datagram JSON must be an object")
    return payload


def make_subscribe_packet(client_name: str = "dexter-relay-client") -> dict[str, Any]:
    return {
        "type": "subscribe",
        "version": PROTOCOL_VERSION,
        "client": client_name,
        "client_id": str(uuid.uuid4()),
    }


def make_unsubscribe_packet(client_id: str, client_name: str = "dexter-relay-client") -> dict[str, Any]:
    return {
        "type": "unsubscribe",
        "version": PROTOCOL_VERSION,
        "client": client_name,
        "client_id": client_id,
    }


def is_subscribe_packet(payload: Mapping[str, Any]) -> bool:
    return (
        payload.get("type") == "subscribe"
        and payload.get("version", PROTOCOL_VERSION) == PROTOCOL_VERSION
    )


def is_unsubscribe_packet(payload: Mapping[str, Any]) -> bool:
    return (
        payload.get("type") == "unsubscribe"
        and payload.get("version", PROTOCOL_VERSION) == PROTOCOL_VERSION
    )
