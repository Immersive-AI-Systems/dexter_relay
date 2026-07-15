"""Dexter BLE device with scan and direct-address connection fallbacks."""

from __future__ import annotations

import asyncio
import struct
import sys
import threading
from collections import deque
from dataclasses import dataclass
from typing import Optional

from bleak import BleakClient, BleakScanner

from .ble_support import ble_address_to_int, normalize_ble_address


DEVICE_NAME = "Dexter"
CHAR_UUID = "a88278d2-7009-4bee-a6f8-e1dc3ff02b92"

LOAD_CELLS = 15
NUM_READINGS = 2
BLOCK_SIZE = 8 + (LOAD_CELLS * 2)
PACKET_SIZE = 4 + (NUM_READINGS * BLOCK_SIZE)

_MIN_CONNECT_TIMEOUT_S = 20.0
_STARTUP_TIMEOUT_GRACE_S = 5.0


def ble_connect_timeout(scan_timeout: float) -> float:
    return max(_MIN_CONNECT_TIMEOUT_S, scan_timeout + 5.0)


def ble_startup_timeout(scan_timeout: float) -> float:
    """Cover discovery, implicit address discovery, connect, and GATT setup."""

    return scan_timeout + ble_connect_timeout(scan_timeout) + _STARTUP_TIMEOUT_GRACE_S


@dataclass
class BLELoadCellEvent:
    payload: list[int]
    counter: int
    timestamp_us: int


class RelayBLELoadCellDevice:
    """BLE load-cell reader with direct-address fallback for Windows."""

    def __init__(
        self,
        *,
        scan_timeout: float = 5.0,
        ble_address: str | None = None,
        ble_adapter: str | None = None,
    ) -> None:
        self.identifier = f"BLE:{DEVICE_NAME}"
        self._queue: deque[BLELoadCellEvent] = deque()
        self._queue_lock = threading.Lock()
        self._recent_timestamps: deque[int] = deque(maxlen=NUM_READINGS * 2)
        self._recent_timestamps_set: set[int] = set()
        self._scan_timeout = scan_timeout
        self._ble_address = (
            normalize_ble_address(ble_address) if ble_address else None
        )
        self._ble_adapter = ble_adapter

        self._stop_event = threading.Event()
        self._ready_event = threading.Event()

        self._client: Optional[BleakClient] = None
        self._connection_error: Optional[Exception] = None
        self._is_connected = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._run_task: asyncio.Task | None = None

        self._thread = threading.Thread(target=self._thread_main, daemon=True)
        self._thread.start()
        ready = self._ready_event.wait(timeout=ble_startup_timeout(scan_timeout))

        if not self._is_connected:
            if not ready:
                self._connection_error = TimeoutError(
                    "timed out while discovering, connecting to, or subscribing "
                    "to Dexter"
                )
                self._cancel_async_run()
            self._stop_event.set()
            self._thread.join(timeout=5.0)
            error = self._connection_error or RuntimeError("Dexter BLE startup failed")
            raise error

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._run())
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self._connection_error = exc
            self._ready_event.set()
        finally:
            self._is_connected = False

    async def _discover_target(self):
        target = DEVICE_NAME.lower()
        address = self._ble_address.lower() if self._ble_address else None
        scanner_options = (
            {"adapter": self._ble_adapter} if self._ble_adapter is not None else {}
        )
        return await BleakScanner.find_device_by_filter(
            lambda device, adv_data: (
                target in (device.name or adv_data.local_name or "").lower()
                or (address is not None and device.address.lower() == address)
            ),
            timeout=self._scan_timeout,
            **scanner_options,
        )

    async def _connect_client(self) -> None:
        assert self._client is not None
        print(f"Connecting to {self.identifier}")
        connect = self._client.connect
        if "pair" in connect.__code__.co_varnames:
            await connect(pair=False)
        else:
            await connect()
        await self._client.start_notify(CHAR_UUID, self._notification_callback)
        self._is_connected = True
        self._ready_event.set()

        while not self._stop_event.is_set():
            await asyncio.sleep(0.05)

    async def _client_for_address(self, address: str) -> BleakClient:
        if sys.platform == "win32":
            from bleak.backends.winrt.client import BleakClientWinRT

            client = BleakClientWinRT(
                address,
                winrt={"use_cached_services": True},
                timeout=max(20.0, self._scan_timeout + 5.0),
            )
            client._device_info = ble_address_to_int(address)
            return client

        return BleakClient(
            address,
            disconnected_callback=lambda _: self._stop_event.set(),
            timeout=ble_connect_timeout(self._scan_timeout),
            adapter=self._ble_adapter,
        )

    def _cancel_async_run(self) -> None:
        loop = self._loop
        task = self._run_task
        if loop is None or task is None or loop.is_closed() or task.done():
            return
        loop.call_soon_threadsafe(task.cancel)

    def _notification_callback(self, _, data: bytearray) -> None:
        if len(data) < PACKET_SIZE:
            return

        counter = struct.unpack_from("<i", data, 0)[0]
        readings = []
        for reading_index in range(NUM_READINGS):
            base = 4 + (reading_index * BLOCK_SIZE)
            timestamp_us = struct.unpack_from("<Q", data, base)[0]
            load_cells = list(struct.unpack_from(f"<{LOAD_CELLS}H", data, base + 8))
            readings.append((timestamp_us, load_cells))

        readings.sort(key=lambda x: x[0])

        with self._queue_lock:
            for timestamp_us, load_cells in readings:
                if timestamp_us in self._recent_timestamps_set:
                    continue

                if len(self._recent_timestamps) == self._recent_timestamps.maxlen:
                    expired_timestamp = self._recent_timestamps.popleft()
                    self._recent_timestamps_set.discard(expired_timestamp)

                self._recent_timestamps.append(timestamp_us)
                self._recent_timestamps_set.add(timestamp_us)
                self._queue.append(
                    BLELoadCellEvent(
                        payload=load_cells,
                        counter=counter,
                        timestamp_us=timestamp_us,
                    )
                )

    async def _run(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._run_task = asyncio.current_task()
        print("Searching for BLE device...")
        device = await self._discover_target()

        if device is not None:
            print(f"Found {device.address}: {device.name or DEVICE_NAME}")
            self._client = BleakClient(
                device,
                disconnected_callback=lambda _: self._stop_event.set(),
                timeout=ble_connect_timeout(self._scan_timeout),
                adapter=self._ble_adapter,
            )
        elif self._ble_address is not None:
            print(
                f"Scan did not find Dexter; connecting directly to {self._ble_address}"
            )
            self._client = await self._client_for_address(self._ble_address)
        else:
            self._connection_error = RuntimeError("No matching BLE peripheral found")
            self._ready_event.set()
            return

        try:
            await self._connect_client()
        except Exception as exc:
            self._connection_error = exc
            self._ready_event.set()
        finally:
            self._is_connected = False
            if self._client and self._client.is_connected:
                try:
                    await self._client.stop_notify(CHAR_UUID)
                finally:
                    await self._client.disconnect()

    def get_events(self):
        events = []
        with self._queue_lock:
            while self._queue:
                events.append(self._queue.popleft())
        return events

    def close(self) -> None:
        self._stop_event.set()
        if self._thread.is_alive():
            self._thread.join()
