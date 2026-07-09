"""Helpers for reclaiming the relay UDP port from stale server processes."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time

from .process_util import looks_like_dexter_relay_process


def is_address_in_use(error: OSError) -> bool:
    if error.errno in (98, 10048):  # EADDRINUSE on Linux / WinError 10048
        return True
    return getattr(error, "winerror", None) == 10048


def free_stale_relay_listeners(port: int) -> list[int]:
    """Kill other dexter-relay server processes listening on *port*."""

    if sys.platform == "win32":
        return _free_stale_relay_listeners_windows(port)
    return _free_stale_relay_listeners_unix(port)


def bind_udp_socket(host: str, port: int) -> socket.socket:
    """Bind a UDP socket, stopping stale dexter-relay servers if needed."""

    last_error: OSError | None = None
    for attempt in range(2):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind((host, port))
            return sock
        except OSError as exc:
            sock.close()
            last_error = exc
            if attempt == 0 and is_address_in_use(exc):
                killed = free_stale_relay_listeners(port)
                if killed:
                    for pid in killed:
                        print(f"stopped stale relay process: {pid}")
                    time.sleep(0.2)
                    continue
            break

    assert last_error is not None
    if is_address_in_use(last_error):
        raise RuntimeError(
            f"UDP port {port} is already in use by another program that is "
            "not a dexter-relay server"
        ) from last_error
    raise last_error


def _looks_like_relay_process(command: str, executable: str = "") -> bool:
    return looks_like_dexter_relay_process(command, executable)


def _free_stale_relay_listeners_windows(port: int) -> list[int]:
    current_pid = os.getpid()
    script = f"""
$currentPid = {current_pid}
$pids = Get-NetUDPEndpoint -LocalPort {port} -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique
foreach ($procId in $pids) {{
    if ($procId -eq $currentPid) {{ continue }}
    $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
    if (-not $proc) {{ continue }}
    $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId=$procId").CommandLine
    $isRelay = ($cmd -like '*dexter_relay.server*') -or ($cmd -like '*dexter-relay-server*')
    if ($isRelay) {{
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


def _free_stale_relay_listeners_unix(port: int) -> list[int]:
    current_pid = os.getpid()
    try:
        output = subprocess.check_output(
            ["lsof", "-nP", f"-iUDP:{port}", "-t"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []

    killed: list[int] = []
    for line in output.splitlines():
        try:
            pid = int(line.strip())
        except ValueError:
            continue
        if pid == current_pid:
            continue

        try:
            command = subprocess.check_output(
                ["ps", "-p", str(pid), "-o", "command="],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue

        if not _looks_like_relay_process(command):
            continue

        os.kill(pid, signal.SIGTERM)
        killed.append(pid)
    return killed
