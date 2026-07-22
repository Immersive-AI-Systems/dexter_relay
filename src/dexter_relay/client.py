"""Terminal client for the Dexter UDP relay."""

from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from .protocol import (
    DEFAULT_UDP_PORT,
    FINGER_NAMES,
    MAX_DATAGRAM_BYTES,
    decode_datagram,
    encode_datagram,
    make_subscribe_packet,
    make_unsubscribe_packet,
)


def default_relay_host() -> str:
    """Pick a relay host that works from WSL as well as native Windows/Linux."""

    if sys.platform != "linux":
        return "127.0.0.1"

    try:
        version = Path("/proc/version").read_text(encoding="utf-8").lower()
    except OSError:
        return "127.0.0.1"
    if "microsoft" not in version:
        return "127.0.0.1"

    # WSL's /etc/resolv.conf nameserver (often 10.255.255.254) is a DNS proxy,
    # not the Windows host for general UDP. The default-route gateway is.
    try:
        output = subprocess.check_output(
            ["ip", "route", "show", "default"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        for line in output.splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[0] == "default" and parts[1] == "via":
                return parts[2]
    except (subprocess.CalledProcessError, FileNotFoundError, IndexError):
        pass
    return "127.0.0.1"


def format_vector(values: Sequence[float]) -> str:
    return "(" + ", ".join(f"{value:8.3f}" for value in values) + ")"


def format_frame(frame: dict[str, Any], *, show_raw: bool = False) -> str:
    timestamp = datetime.fromtimestamp(float(frame["timestamp"])).strftime("%H:%M:%S")
    measurement_kind = str(frame.get("measurement_kind", "force"))
    units = str(frame.get("units", "N"))
    parts = [
        f"{timestamp}",
        f"seq={frame.get('sequence')}",
        f"transport={frame.get('transport')}",
        f"measurement={measurement_kind}",
    ]
    fingers = frame.get("fingers", {})

    for name in FINGER_NAMES:
        measurement = fingers.get(name)
        if not measurement or not measurement.get("has_data"):
            parts.append(f"{name}=waiting")
            continue

        force = measurement.get("force", ())
        text = f"{name}={format_vector(force)} {units}"
        if show_raw:
            text += f" raw={measurement.get('raw', [])}"
        parts.append(text)

    return " | ".join(parts)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Subscribe to a dexter-relay UDP stream and print force values."
    )
    parser.add_argument(
        "--host",
        default=default_relay_host(),
        help="relay server host (defaults to the Windows host IP when run in WSL)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_UDP_PORT,
        help="relay server UDP port",
    )
    parser.add_argument(
        "--print-interval",
        type=float,
        default=1.0,
        help="seconds between terminal prints",
    )
    parser.add_argument(
        "--subscribe-interval",
        type=float,
        default=2.0,
        help="seconds between UDP subscription renewals",
    )
    parser.add_argument(
        "--show-raw",
        action="store_true",
        help="include signed raw channel values in each printed line",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.print_interval <= 0:
        parser.error("--print-interval must be greater than 0")
    if args.subscribe_interval <= 0:
        parser.error("--subscribe-interval must be greater than 0")

    server_address = (args.host, args.port)
    subscribe_payload = make_subscribe_packet()
    subscribe_packet = encode_datagram(subscribe_payload)
    unsubscribe_packet = encode_datagram(
        make_unsubscribe_packet(str(subscribe_payload["client_id"]))
    )
    latest_frame: dict[str, Any] | None = None
    last_subscribe = 0.0
    next_print = time.monotonic()

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setblocking(False)
        print(f"subscribing to dexter-relay at {args.host}:{args.port}")

        try:
            while True:
                now = time.monotonic()
                if now - last_subscribe >= args.subscribe_interval:
                    sock.sendto(subscribe_packet, server_address)
                    last_subscribe = now

                while True:
                    try:
                        data, _ = sock.recvfrom(MAX_DATAGRAM_BYTES)
                    except BlockingIOError:
                        break

                    try:
                        payload = decode_datagram(data)
                    except Exception:
                        continue

                    if payload.get("type") == "force":
                        latest_frame = payload

                if now >= next_print:
                    if latest_frame is None:
                        print("waiting for relay data...")
                    else:
                        print(format_frame(latest_frame, show_raw=args.show_raw))
                    next_print = now + args.print_interval

                time.sleep(0.01)

        except KeyboardInterrupt:
            print("\nclient stopped")
        finally:
            try:
                sock.sendto(unsubscribe_packet, server_address)
            except OSError:
                pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
