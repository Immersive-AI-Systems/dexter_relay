"""UDP relay server for Dexter force-vector measurements."""

from __future__ import annotations

import argparse
import socket
import time
from typing import Any

from .protocol import (
    DEFAULT_UDP_PORT,
    MAX_DATAGRAM_BYTES,
    PROTOCOL_VERSION,
    decode_datagram,
    encode_datagram,
    is_subscribe_packet,
    is_unsubscribe_packet,
)
from .source import DexterForceSource, ForceSource, SimulatedForceSource


class UdpRelayServer:
    def __init__(
        self,
        *,
        source: ForceSource,
        bind_host: str,
        port: int,
        send_hz: float,
        client_ttl_s: float,
    ) -> None:
        if send_hz <= 0:
            raise ValueError("send_hz must be greater than 0")
        if client_ttl_s <= 0:
            raise ValueError("client_ttl_s must be greater than 0")

        self.source = source
        self.bind_host = bind_host
        self.port = int(port)
        self.send_interval_s = 1.0 / send_hz
        self.client_ttl_s = client_ttl_s
        self._clients: dict[tuple[str, int], float] = {}
        self._sequence = 0

    def serve_forever(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind((self.bind_host, self.port))
            sock.setblocking(False)
            print(
                f"dexter-relay server listening on {self.bind_host}:{self.port} "
                f"({self.source.transport}, {1.0 / self.send_interval_s:.1f} Hz)"
            )

            next_publish = time.monotonic()
            while True:
                self._receive_subscriptions(sock)

                now = time.monotonic()
                if now >= next_publish:
                    self._publish(sock)
                    next_publish += self.send_interval_s
                    if next_publish < now:
                        next_publish = now + self.send_interval_s

                sleep_for = min(0.01, max(0.0, next_publish - time.monotonic()))
                if sleep_for:
                    time.sleep(sleep_for)

    def _receive_subscriptions(self, sock: socket.socket) -> None:
        while True:
            try:
                data, address = sock.recvfrom(MAX_DATAGRAM_BYTES)
            except BlockingIOError:
                return

            try:
                payload = decode_datagram(data)
            except Exception:
                continue

            if not is_subscribe_packet(payload):
                if is_unsubscribe_packet(payload):
                    self._unsubscribe(address)
                continue

            is_new = address not in self._clients
            self._clients[address] = time.monotonic()
            self._send_ack(sock, address, payload)
            if is_new:
                print(f"client subscribed: {address[0]}:{address[1]}")

    def _unsubscribe(self, address: tuple[str, int]) -> None:
        if address in self._clients:
            del self._clients[address]
            print(f"client unsubscribed: {address[0]}:{address[1]}")

    def _send_ack(
        self, sock: socket.socket, address: tuple[str, int], request: dict[str, Any]
    ) -> None:
        ack = {
            "type": "ack",
            "version": PROTOCOL_VERSION,
            "timestamp": time.time(),
            "client_ttl_s": self.client_ttl_s,
            "server": "dexter-relay",
            "request_client": request.get("client"),
            "request_client_id": request.get("client_id"),
        }
        sock.sendto(encode_datagram(ack), address)

    def _publish(self, sock: socket.socket) -> None:
        self._expire_clients()

        snapshot = self.source.read_snapshot()
        self._sequence += 1
        frame = {
            "type": "force",
            "version": PROTOCOL_VERSION,
            "sequence": self._sequence,
            "timestamp": snapshot["timestamp"],
            "transport": snapshot["transport"],
            "fingers": snapshot["fingers"],
            "status": snapshot["status"],
        }
        data = encode_datagram(frame)

        for address in list(self._clients):
            try:
                sock.sendto(data, address)
            except OSError as exc:
                print(f"failed sending to {address[0]}:{address[1]}: {exc}")

    def _expire_clients(self) -> None:
        now = time.monotonic()
        expired = [
            address
            for address, last_seen in self._clients.items()
            if now - last_seen > self.client_ttl_s
        ]
        for address in expired:
            del self._clients[address]
            print(f"client expired: {address[0]}:{address[1]}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Relay Dexter force-vector measurements to UDP clients."
    )
    parser.add_argument("--bind", default="0.0.0.0", help="UDP bind host")
    parser.add_argument("--port", type=int, default=DEFAULT_UDP_PORT, help="UDP bind port")
    parser.add_argument(
        "--send-hz",
        type=float,
        default=100.0,
        help="force-frame publish rate",
    )
    parser.add_argument(
        "--client-ttl",
        type=float,
        default=5.0,
        help="seconds before an inactive UDP client is forgotten",
    )
    parser.add_argument(
        "--map",
        action="append",
        default=[],
        metavar="PORT:FINGER[,FINGER]",
        help="serial port to finger mapping; repeat for multiple devices; implies serial mode",
    )
    parser.add_argument(
        "--ble",
        action="store_true",
        help="read Dexter over BLE; this is the default when no --map is provided",
    )
    parser.add_argument(
        "--serial",
        action="store_true",
        help="read serial load-cell devices described by --map",
    )
    parser.add_argument(
        "--ble-scan-timeout",
        type=float,
        default=1.0,
        help="BLE scan timeout in seconds",
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="publish generated measurements instead of opening Dexter hardware",
    )
    parser.add_argument(
        "--simulate-channels",
        type=int,
        choices=(3, 4),
        default=4,
        help="raw channel width used by --simulate",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.simulate:
        source: ForceSource = SimulatedForceSource(channels=args.simulate_channels)
    else:
        if args.ble and args.serial:
            parser.error("--ble and --serial cannot be used together")
        if args.ble and args.map:
            parser.error("--map is for serial mode and cannot be used with --ble")

        use_ble = args.ble or (not args.serial and not args.map)
        if args.serial and not args.map:
            parser.error("--serial requires at least one --map PORT:finger[,finger]")

        try:
            source = DexterForceSource(
                mapping_specs=args.map,
                use_ble=use_ble,
                ble_scan_timeout=args.ble_scan_timeout,
            )
        except Exception as exc:
            parser.exit(2, f"error: {exc}\n")

    server = UdpRelayServer(
        source=source,
        bind_host=args.bind,
        port=args.port,
        send_hz=args.send_hz,
        client_ttl_s=args.client_ttl,
    )

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nrelay stopped")
    finally:
        source.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
