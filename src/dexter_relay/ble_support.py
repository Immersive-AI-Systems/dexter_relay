"""Windows-specific BLE helpers for Dexter discovery diagnostics."""

from __future__ import annotations

import asyncio
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable

from .process_util import free_dexter_ble_processes


_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
_ADDRESS_CACHE = Path.home() / ".dexter_relay" / "ble_address"


def normalize_ble_address(address: str) -> str:
    address = address.strip().upper()
    if not _MAC_RE.fullmatch(address):
        raise ValueError(
            f"invalid BLE address {address!r}; expected format AA:BB:CC:DD:EE:FF"
        )
    return address


def ble_address_to_int(address: str) -> int:
    return int(normalize_ble_address(address).replace(":", ""), 16)


def format_ble_address(address_int: int) -> str:
    return ":".join(f"{byte:02X}" for byte in address_int.to_bytes(6, byteorder="big"))


async def windows_dexter_connection_state(
    address: str,
) -> tuple[str | None, bool]:
    """Return (device_name, is_connected_to_windows) for a BLE address."""

    if sys.platform != "win32":
        return None, False

    from winrt.windows.devices.bluetooth import (
        BluetoothConnectionStatus,
        BluetoothLEDevice,
    )

    device = await BluetoothLEDevice.from_bluetooth_address_async(
        ble_address_to_int(address)
    )
    name = device.name or None
    connected = device.connection_status == BluetoothConnectionStatus.CONNECTED
    return name, connected


def windows_connected_hint(address: str) -> str:
    return (
        f"Windows Bluetooth already has Dexter connected at {address}. "
        "Open Settings > Bluetooth & devices > Devices, disconnect or remove "
        '"Dexter", then start the relay again. Only one program can use Dexter '
        "BLE at a time."
    )


async def diagnose_ble_failure(
    *,
    address: str | None,
    scan_timeout: float,
    retries: int,
    last_error: Exception | None,
) -> str:
    if address:
        try:
            name, connected = await windows_dexter_connection_state(address)
        except Exception:
            name, connected = None, False

        if connected and (name is None or "dexter" in name.lower()):
            return windows_connected_hint(normalize_ble_address(address))

    detail = str(last_error) if last_error else "unknown error"
    return (
        "failed to connect to Dexter over BLE after "
        f"{retries} attempt(s) with a {scan_timeout:g}s scan window; "
        f"last error: {detail}. "
        "Ensure Dexter is powered on and in range, close the visualizer or any "
        "other relay instance, and if Windows shows Dexter as Connected, "
        "disconnect it in Bluetooth settings. "
        "If Dexter advertises slowly, pass --ble-scan-timeout 10. "
        "To check whether Windows is holding the device, pass "
        "--ble-address AA:BB:CC:DD:EE:FF using the address shown in "
        "Windows Bluetooth device details."
    )


def run_async(coro_factory: Callable[[], asyncio.Future]):
    return asyncio.run(coro_factory())


async def discover_dexter_address(timeout: float) -> str | None:
    from bleak import BleakScanner

    device = await BleakScanner.find_device_by_filter(
        lambda device, adv_data: "dexter"
        in (device.name or adv_data.local_name or "").lower(),
        timeout=timeout,
    )
    if device is None:
        return None
    return normalize_ble_address(device.address)


def read_cached_ble_address() -> str | None:
    try:
        text = _ADDRESS_CACHE.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text:
        return None
    try:
        return normalize_ble_address(text)
    except ValueError:
        return None


def write_cached_ble_address(address: str) -> None:
    _ADDRESS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    _ADDRESS_CACHE.write_text(normalize_ble_address(address) + "\n", encoding="utf-8")


async def winrt_dexter_address(ble_address: str | None) -> str | None:
    """Resolve Dexter by address on Windows even when it is not advertising."""

    if sys.platform != "win32":
        return normalize_ble_address(ble_address) if ble_address else None

    from winrt.windows.devices.bluetooth import BluetoothLEDevice

    candidates: list[str] = []
    if ble_address:
        candidates.append(normalize_ble_address(ble_address))
    cached = read_cached_ble_address()
    if cached and cached not in candidates:
        candidates.append(cached)

    for address in candidates:
        device = await BluetoothLEDevice.from_bluetooth_address_async(
            ble_address_to_int(address)
        )
        name = (device.name or "").lower()
        device.close()
        if "dexter" in name or address == ble_address or address == cached:
            return address
    return None


async def resolve_dexter_address(
    *,
    ble_address: str | None,
    discovery_timeout: float,
) -> str | None:
    if ble_address:
        return normalize_ble_address(ble_address)

    discovered = await discover_dexter_address(discovery_timeout)
    if discovered:
        write_cached_ble_address(discovered)
        return discovered

    winrt_address = await winrt_dexter_address(None)
    if winrt_address:
        return winrt_address

    return read_cached_ble_address()


async def release_windows_dexter_connection(address: str) -> bool:
    """Drop a Windows-held GATT session so the relay can connect."""

    if sys.platform != "win32":
        return False

    from winrt.windows.devices.bluetooth import (
        BluetoothConnectionStatus,
        BluetoothLEDevice,
    )

    device = await BluetoothLEDevice.from_bluetooth_address_async(
        ble_address_to_int(address)
    )
    was_connected = device.connection_status == BluetoothConnectionStatus.CONNECTED
    device.close()
    await asyncio.sleep(0.3)
    return was_connected


async def release_linux_dexter_connection(address: str) -> bool:
    """Disconnect a BlueZ session left behind by a crashed BLE client."""

    if not sys.platform.startswith("linux"):
        return False

    def disconnect() -> bool:
        try:
            controllers_result = subprocess.run(
                ["bluetoothctl", "list"],
                capture_output=True,
                text=True,
                timeout=5.0,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

        controllers = re.findall(
            r"^Controller\s+([0-9A-Fa-f:]{17})\b",
            controllers_result.stdout,
            flags=re.MULTILINE,
        )
        if not controllers:
            controllers = [None]

        for controller in controllers:
            commands = []
            if controller is not None:
                commands.append(f"select {controller}")
            commands.extend(
                [f"disconnect {normalize_ble_address(address)}", "quit"]
            )
            try:
                result = subprocess.run(
                    ["bluetoothctl"],
                    input="\n".join(commands) + "\n",
                    capture_output=True,
                    text=True,
                    timeout=5.0,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                continue
            output = f"{result.stdout}\n{result.stderr}".lower()
            if "successful disconnected" in output:
                return True
        return False

    return await asyncio.to_thread(disconnect)


async def prepare_dexter_ble(
    *,
    ble_address: str | None,
    discovery_timeout: float = 3.0,
    discover: bool = True,
) -> str | None:
    """Stop competing Dexter BLE users and release any Windows connection."""

    killed = free_dexter_ble_processes()
    for pid in killed:
        print(f"stopped process using Dexter BLE: {pid}")
    if killed:
        await asyncio.sleep(1.0)

    if discover:
        address = await resolve_dexter_address(
            ble_address=ble_address,
            discovery_timeout=discovery_timeout,
        )
    else:
        address = (
            normalize_ble_address(ble_address)
            if ble_address
            else read_cached_ble_address()
        )

    if address is not None:
        if sys.platform == "win32":
            released = await release_windows_dexter_connection(address)
            platform_name = "Windows"
        elif sys.platform.startswith("linux"):
            released = await release_linux_dexter_connection(address)
            platform_name = "Linux"
        else:
            released = False
            platform_name = sys.platform

        if released:
            print(
                f"released stale {platform_name} Bluetooth connection "
                f"to Dexter at {address}"
            )
            await asyncio.sleep(1.0)

    if address is not None:
        write_cached_ble_address(address)

    return address


def prepare_dexter_ble_sync(
    *,
    ble_address: str | None,
    discovery_timeout: float = 3.0,
    discover: bool = True,
) -> str | None:
    return asyncio.run(
        prepare_dexter_ble(
            ble_address=ble_address,
            discovery_timeout=discovery_timeout,
            discover=discover,
        )
    )


async def discover_dexter_endpoint(
    *, address: str | None, discovery_timeout: float
) -> tuple[str, str] | None:
    """Return the Linux adapter and current address advertising as Dexter."""

    if not sys.platform.startswith("linux"):
        return None

    from bleak import BleakScanner

    normalized_address = normalize_ble_address(address) if address else None
    adapters = sorted(path.name for path in Path("/sys/class/bluetooth").glob("hci*"))
    for adapter in adapters:
        device = await BleakScanner.find_device_by_filter(
            lambda device, adv_data: (
                "dexter" in (device.name or adv_data.local_name or "").lower()
                or (
                    normalized_address is not None
                    and device.address.upper() == normalized_address
                )
            ),
            timeout=discovery_timeout,
            adapter=adapter,
        )
        if device is not None:
            current_address = normalize_ble_address(device.address)
            write_cached_ble_address(current_address)
            return adapter, current_address
    return None


def discover_dexter_endpoint_sync(
    *, address: str | None, discovery_timeout: float
) -> tuple[str, str] | None:
    return asyncio.run(
        discover_dexter_endpoint(
            address=address,
            discovery_timeout=discovery_timeout,
        )
    )
