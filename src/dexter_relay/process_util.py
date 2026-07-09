"""Helpers for stopping competing Dexter relay / controller processes."""

from __future__ import annotations

import os
import signal
import subprocess
import sys


_DEXTER_RELAY_MARKERS = (
    "dexter_relay.server",
    "dexter-relay-server",
)

_DEXTER_BLE_MARKERS = _DEXTER_RELAY_MARKERS + (
    "example_ble",
    "example_ble_force",
    "dextercontroller.visualizer",
    "python -m visualizer",
)


def looks_like_dexter_relay_process(command: str, executable: str = "") -> bool:
    haystack = f"{command} {executable}".lower()
    return any(marker in haystack for marker in _DEXTER_RELAY_MARKERS)


def looks_like_dexter_ble_consumer(command: str, executable: str = "") -> bool:
    haystack = f"{command} {executable}".lower()
    return any(marker in haystack for marker in _DEXTER_BLE_MARKERS)


def free_dexter_ble_processes() -> list[int]:
    """Kill other local processes that may hold Dexter open over BLE."""

    return free_matching_processes(looks_like_dexter_ble_consumer)


def free_matching_processes(match_fn) -> list[int]:
    if sys.platform == "win32":
        return _free_matching_processes_windows(match_fn)
    return _free_matching_processes_unix(match_fn)


def _free_matching_processes_windows(match_fn) -> list[int]:
    current_pid = os.getpid()
    parent_pid = os.getppid()
    markers = _DEXTER_BLE_MARKERS
    marker_checks = " -or ".join(
        f"($cmd -like '*{marker}*')" for marker in markers
    )
    script = f"""
$currentPid = {current_pid}
$parentPid = {parent_pid}
Get-CimInstance Win32_Process | ForEach-Object {{
    $procId = $_.ProcessId
    if ($procId -eq $currentPid -or $procId -eq $parentPid) {{ return }}
    $cmd = $_.CommandLine
    if (-not $cmd) {{ return }}
    $path = (Get-Process -Id $procId -ErrorAction SilentlyContinue).Path
    $isMatch = ({marker_checks})
    if ($isMatch) {{
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        Write-Output $procId
    }}
}}
"""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
    )
    killed: list[int] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.isdigit():
            killed.append(int(line))
    return killed


def _free_matching_processes_unix(match_fn) -> list[int]:
    current_pid = os.getpid()
    try:
        output = subprocess.check_output(
            ["ps", "-ax", "-o", "pid=,command="],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []

    killed: list[int] = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        if pid == current_pid:
            continue
        command = parts[1]
        if not match_fn(command):
            continue
        os.kill(pid, signal.SIGTERM)
        killed.append(pid)
    return killed
