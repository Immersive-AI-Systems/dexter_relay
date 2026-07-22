"""Playback source for previously recorded relay frames."""

from __future__ import annotations

import copy
import json
import math
import time
from pathlib import Path
from typing import Any, Mapping

from .protocol import FINGER_NAMES


def _empty_measurement() -> dict[str, Any]:
    return {
        "raw": [],
        "force": [],
        "channels": 0,
        "has_data": False,
        "last_update_ts": 0.0,
        "age_s": None,
    }


def _load_json_frames(path: Path) -> list[Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read recording {path}: {exc}") from exc

    if not text.strip():
        raise ValueError(f"recording {path} is empty")

    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        frames = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            try:
                frames.append(json.loads(stripped))
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"invalid JSON in recording {path} at line {line_number}: "
                    f"{exc.msg}"
                ) from exc
        return frames

    if isinstance(document, list):
        return document
    if isinstance(document, dict) and "frames" in document:
        frames = document["frames"]
        if not isinstance(frames, list):
            raise ValueError(f"recording {path} field 'frames' must be an array")
        return frames
    return [document]


def _normalize_frame(frame: Any, *, path: Path, index: int) -> dict[str, Any]:
    label = f"recording {path} frame {index + 1}"
    if not isinstance(frame, Mapping):
        raise ValueError(f"{label} must be a JSON object")
    if frame.get("type") not in (None, "force"):
        raise ValueError(f"{label} must be a relay force frame")

    fingers = frame.get("fingers")
    if not isinstance(fingers, Mapping):
        raise ValueError(f"{label} field 'fingers' must be an object")

    normalized_fingers: dict[str, Any] = {}
    for name in FINGER_NAMES:
        measurement = fingers.get(name)
        if measurement is None:
            normalized_fingers[name] = _empty_measurement()
            continue
        if not isinstance(measurement, Mapping):
            raise ValueError(f"{label} finger {name!r} must be an object")
        normalized = dict(measurement)
        normalized.setdefault("raw", [])
        normalized.setdefault("force", [])
        normalized.setdefault("channels", len(normalized["raw"]))
        normalized.setdefault("has_data", bool(normalized["force"]))
        normalized.setdefault("last_update_ts", 0.0)
        normalized.setdefault("age_s", None)
        normalized_fingers[name] = normalized

    timestamp = frame.get("timestamp", float(index))
    try:
        recorded_timestamp = float(timestamp)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} field 'timestamp' must be numeric") from exc
    if not math.isfinite(recorded_timestamp):
        raise ValueError(f"{label} field 'timestamp' must be finite")

    status = frame.get("status", {})
    if not isinstance(status, Mapping):
        raise ValueError(f"{label} field 'status' must be an object")

    return {
        "recorded_timestamp": recorded_timestamp,
        "recorded_sequence": frame.get("sequence"),
        "original_transport": str(frame.get("transport", "unknown")),
        "measurement_kind": str(frame.get("measurement_kind", "force")),
        "units": str(frame.get("units", "N")),
        "fingers": normalized_fingers,
        "status": dict(status),
    }


class RecordingForceSource:
    """Replay relay force frames, advancing once per publish tick."""

    transport = "recording"

    def __init__(self, path: str | Path, *, loop: bool = True) -> None:
        self.path = Path(path).expanduser()
        raw_frames = _load_json_frames(self.path)
        if not raw_frames:
            raise ValueError(f"recording {self.path} contains no frames")
        self._frames = [
            _normalize_frame(frame, path=self.path, index=index)
            for index, frame in enumerate(raw_frames)
        ]
        self.loop = bool(loop)
        self._index = 0

    def read_snapshot(self) -> dict[str, Any]:
        index = self._index
        frame = copy.deepcopy(self._frames[index])

        if index + 1 < len(self._frames):
            self._index += 1
        elif self.loop:
            self._index = 0

        status = frame["status"]
        status["recording"] = {
            "path": str(self.path),
            "frame_index": index,
            "frame_count": len(self._frames),
            "loop": self.loop,
            "recorded_sequence": frame["recorded_sequence"],
            "recorded_timestamp": frame["recorded_timestamp"],
            "original_transport": frame["original_transport"],
        }

        return {
            "transport": self.transport,
            "timestamp": time.time(),
            "measurement_kind": frame["measurement_kind"],
            "units": frame["units"],
            "fingers": frame["fingers"],
            "status": status,
        }

    def close(self) -> None:
        return None
