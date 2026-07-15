"""Record relay force frames to CSV and play recorded frames back."""

from __future__ import annotations

import argparse
import copy
import csv
import json
import socket
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, TextIO

from .client import default_relay_host
from .protocol import (
    DEFAULT_UDP_PORT,
    MAX_DATAGRAM_BYTES,
    decode_datagram,
    encode_datagram,
    make_subscribe_packet,
    make_unsubscribe_packet,
)


CSV_COLUMNS = (
    "recorded_at",
    "source_timestamp",
    "sequence",
    "transport",
    "fingers_json",
    "status_json",
)


def recording_path(output_dir: str | Path, now: datetime | None = None) -> Path:
    timestamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S_%f")
    return Path(output_dir).expanduser() / f"dexter_{timestamp}.csv"


def frame_to_csv_row(
    frame: Mapping[str, Any], *, recorded_at: float | None = None
) -> dict[str, Any]:
    return {
        "recorded_at": time.time() if recorded_at is None else float(recorded_at),
        "source_timestamp": float(frame["timestamp"]),
        "sequence": int(frame.get("sequence", 0)),
        "transport": str(frame.get("transport", "unknown")),
        "fingers_json": json.dumps(
            frame.get("fingers", {}), separators=(",", ":"), sort_keys=True
        ),
        "status_json": json.dumps(
            frame.get("status", {}), separators=(",", ":"), sort_keys=True
        ),
    }


def write_frame_row(
    writer: csv.DictWriter, frame: Mapping[str, Any], *, recorded_at: float | None = None
) -> None:
    writer.writerow(frame_to_csv_row(frame, recorded_at=recorded_at))


class CsvPlaybackSource:
    """Loop recorded force frames as a relay measurement source."""

    transport = "playback"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self._rows = self._read_rows(self.path)
        self._index = 0
        self._loop_count = 0
        self._playback_sequence = 0

    @staticmethod
    def _read_rows(path: Path) -> list[dict[str, Any]]:
        try:
            handle: TextIO
            handle = path.open("r", encoding="utf-8", newline="")
        except OSError as exc:
            raise ValueError(f"cannot open playback CSV {path}: {exc}") from exc

        with handle:
            reader = csv.DictReader(handle)
            missing = set(CSV_COLUMNS) - set(reader.fieldnames or ())
            if missing:
                raise ValueError(
                    f"playback CSV is missing columns: {', '.join(sorted(missing))}"
                )

            rows: list[dict[str, Any]] = []
            for line_number, row in enumerate(reader, start=2):
                try:
                    fingers = json.loads(row["fingers_json"])
                    status = json.loads(row["status_json"])
                    source_timestamp = float(row["source_timestamp"])
                    recorded_at = float(row["recorded_at"])
                    sequence = int(row["sequence"])
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise ValueError(
                        f"invalid playback CSV row at line {line_number}: {exc}"
                    ) from exc
                if not isinstance(fingers, dict) or not isinstance(status, dict):
                    raise ValueError(
                        f"invalid playback CSV row at line {line_number}: "
                        "fingers_json and status_json must be objects"
                    )
                rows.append(
                    {
                        "recorded_at": recorded_at,
                        "source_timestamp": source_timestamp,
                        "sequence": sequence,
                        "transport": row["transport"],
                        "fingers": fingers,
                        "status": status,
                    }
                )

        if not rows:
            raise ValueError(f"playback CSV contains no data rows: {path}")
        return rows

    def read_snapshot(self) -> dict[str, Any]:
        row = self._rows[self._index]
        row_index = self._index
        self._index += 1
        if self._index >= len(self._rows):
            self._index = 0
            self._loop_count += 1

        self._playback_sequence += 1
        now = time.time()
        fingers = copy.deepcopy(row["fingers"])
        for measurement in fingers.values():
            if isinstance(measurement, dict) and measurement.get("has_data"):
                measurement["last_update_ts"] = now
                measurement["age_s"] = 0.0

        status = copy.deepcopy(row["status"])
        status["playback"] = {
            "csv_path": str(self.path),
            "row_index": row_index,
            "row_count": len(self._rows),
            "loop_count": self._loop_count,
            "recorded_at": row["recorded_at"],
            "source_timestamp": row["source_timestamp"],
            "source_sequence": row["sequence"],
            "source_transport": row["transport"],
            "playback_sequence": self._playback_sequence,
        }

        return {
            "transport": self.transport,
            "timestamp": now,
            "fingers": fingers,
            "status": status,
        }

    def close(self) -> None:
        pass


def build_recorder_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Record Dexter relay force frames to a timestamped CSV file."
    )
    parser.add_argument("--host", default=default_relay_host(), help="relay host")
    parser.add_argument("--port", type=int, default=DEFAULT_UDP_PORT, help="relay port")
    parser.add_argument(
        "--output-dir",
        default="recording",
        help="directory for timestamped CSV recordings",
    )
    parser.add_argument(
        "--subscribe-interval",
        type=float,
        default=2.0,
        help="seconds between UDP subscription renewals",
    )
    return parser


def recorder_main(argv: list[str] | None = None) -> int:
    parser = build_recorder_parser()
    args = parser.parse_args(argv)
    if args.subscribe_interval <= 0:
        parser.error("--subscribe-interval must be greater than 0")

    path = recording_path(args.output_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    subscribe_payload = make_subscribe_packet("dexter-relay-recorder")
    subscribe_packet = encode_datagram(subscribe_payload)
    unsubscribe_packet = encode_datagram(
        make_unsubscribe_packet(
            str(subscribe_payload["client_id"]), "dexter-relay-recorder"
        )
    )
    address = (args.host, args.port)
    row_count = 0
    last_subscribe = 0.0

    with path.open("x", encoding="utf-8", newline="", buffering=1) as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        print(f"recording Dexter relay at {args.host}:{args.port} to {path}")
        print("press Ctrl+C to stop recording")

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(0.1)
            try:
                while True:
                    now = time.monotonic()
                    if now - last_subscribe >= args.subscribe_interval:
                        sock.sendto(subscribe_packet, address)
                        last_subscribe = now

                    try:
                        data, _ = sock.recvfrom(MAX_DATAGRAM_BYTES)
                    except TimeoutError:
                        continue

                    try:
                        frame = decode_datagram(data)
                    except Exception:
                        continue
                    if frame.get("type") != "force":
                        continue

                    write_frame_row(writer, frame)
                    row_count += 1
            except KeyboardInterrupt:
                print(f"\nrecording stopped: {row_count} frames written to {path}")
            finally:
                try:
                    sock.sendto(unsubscribe_packet, address)
                except OSError:
                    pass

    return 0


if __name__ == "__main__":
    raise SystemExit(recorder_main())
