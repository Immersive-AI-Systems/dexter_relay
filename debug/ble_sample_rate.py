#!/usr/bin/env python3
"""Measure Dexter's unique hardware-sample rate directly over BLE.

This bypasses the UDP relay, records every sample de-duplicated by the device's
``timestamp_us``, and compares hardware timestamps with host receive times.
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from dexter_relay.source import _connect_ble_device


@dataclass(frozen=True)
class BleEvent:
    recv_mono_s: float
    timestamp_us: int
    counter: int


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * fraction
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    weight = rank - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def collect(device, duration_s: float) -> list[BleEvent]:
    events: list[BleEvent] = []
    start = time.monotonic()
    deadline = start + duration_s
    print(f"Collecting unique BLE samples for {duration_s:.1f} s...")

    while time.monotonic() < deadline:
        for event in device.get_events():
            events.append(
                BleEvent(
                    recv_mono_s=time.monotonic() - start,
                    timestamp_us=event.timestamp_us,
                    counter=event.counter,
                )
            )
        time.sleep(0.001)
    return events


def intervals(values: list[float]) -> list[float]:
    return [current - previous for previous, current in zip(values, values[1:])]


def rate_hz(count: int, span_s: float) -> float:
    return (count - 1) / span_s if count >= 2 and span_s > 0 else 0.0


def print_interval_stats(label: str, values_s: list[float]) -> None:
    if not values_s:
        print(f"{label}\n  not enough samples")
        return

    values_ms = [value * 1000.0 for value in values_s]
    rate = 1000.0 / statistics.mean(values_ms)
    stdev = statistics.stdev(values_ms) if len(values_ms) > 1 else 0.0
    print(label)
    print(f"  rate: {rate:.2f} Hz")
    print(
        f"  intervals: n={len(values_ms)}, mean={statistics.mean(values_ms):.3f} ms, "
        f"median={statistics.median(values_ms):.3f} ms"
    )
    print(
        f"  range: min={min(values_ms):.3f} ms, max={max(values_ms):.3f} ms, "
        f"stdev={stdev:.3f} ms"
    )
    print(
        f"  percentiles: p5={percentile(values_ms, 0.05):.3f} ms, "
        f"p95={percentile(values_ms, 0.95):.3f} ms"
    )


def counter_gaps(counters: list[int]) -> tuple[int, int, list[int]]:
    sizes = []
    for previous, current in zip(counters, counters[1:]):
        delta = current - previous
        if delta > 1:
            sizes.append(delta)
    return len(sizes), sum(delta - 1 for delta in sizes), sizes


def write_csv(path: Path, events: list[BleEvent]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=(
                "recv_mono_s",
                "timestamp_us",
                "counter",
                "host_interval_ms",
                "device_interval_ms",
            ),
        )
        writer.writeheader()
        for index, event in enumerate(events):
            previous = events[index - 1] if index else None
            writer.writerow(
                {
                    "recv_mono_s": round(event.recv_mono_s, 6),
                    "timestamp_us": event.timestamp_us,
                    "counter": event.counter,
                    "host_interval_ms": ""
                    if previous is None
                    else round((event.recv_mono_s - previous.recv_mono_s) * 1000, 6),
                    "device_interval_ms": ""
                    if previous is None
                    else round((event.timestamp_us - previous.timestamp_us) / 1000, 6),
                }
            )


def analyze(events: list[BleEvent], duration_s: float) -> None:
    host_times = [event.recv_mono_s for event in events]
    device_times = [event.timestamp_us / 1_000_000.0 for event in events]
    counters = [event.counter for event in events]
    host_span = host_times[-1] - host_times[0]
    device_span = device_times[-1] - device_times[0]
    gaps, missed, sizes = counter_gaps(counters)

    print(f"\nUnique samples received: {len(events)}")
    print(f"Collection window: {duration_s:.1f} s")
    print(f"Host span rate: {rate_hz(len(events), host_span):.2f} Hz\n")
    print_interval_stats("Host receive time (PC clock):", intervals(host_times))
    print()
    print_interval_stats(
        "Device timestamp_us (hardware clock; true unique-sample rate):",
        intervals(device_times),
    )
    print(f"\nDevice span rate: {rate_hz(len(events), device_span):.2f} Hz")
    print("\nBLE packet counter:")
    print(f"  first={counters[0]}, last={counters[-1]}, gaps={gaps}")
    if missed:
        print(f"  WARNING: {missed} missed notification(s) (counter jumps: {sizes})")
    else:
        print("  no counter gaps detected")
    print(
        "\nInterpretation: the device timestamp rate is Dexter's maximum "
        "non-repeating sample rate. Host delivery can be bursty because each "
        "BLE notification contains multiple hardware samples."
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Measure Dexter's unique BLE sample rate without the relay."
    )
    parser.add_argument("--duration", type=float, default=10.0)
    parser.add_argument("--scan-timeout", type=float, default=10.0)
    parser.add_argument("--connect-retries", type=int, default=3)
    parser.add_argument(
        "--ble-adapter",
        default="auto" if sys.platform.startswith("linux") else None,
        help="Linux HCI adapter (default: auto), for example hci0",
    )
    parser.add_argument("--ble-address", default=None)
    parser.add_argument("--output", type=Path, help="optional per-sample CSV path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.duration <= 0 or args.scan_timeout <= 0 or args.connect_retries < 1:
        print("duration/scan timeout must be positive and retries at least 1", file=sys.stderr)
        return 2

    print("Connecting directly to Dexter (relay must not be running)...")
    device = None
    try:
        device = _connect_ble_device(
            scan_timeout=args.scan_timeout,
            retries=args.connect_retries,
            ble_address=args.ble_address,
            ble_adapter=args.ble_adapter,
        )
        events = collect(device, args.duration)
    except KeyboardInterrupt:
        print("\nMeasurement interrupted.")
        return 130
    except Exception as exc:
        print(f"Failed to connect or collect BLE data: {exc}", file=sys.stderr)
        return 1
    finally:
        if device is not None:
            device.close()

    if len(events) < 2:
        print(f"Only {len(events)} unique sample(s) received.", file=sys.stderr)
        return 1
    if args.output:
        write_csv(args.output, events)
        print(f"Wrote per-sample CSV: {args.output}")
    analyze(events, args.duration)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
