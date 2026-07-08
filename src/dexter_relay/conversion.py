"""Force conversion used by DexterController.Visualizer.

The visualizer delegates its numeric force conversion to helper types in
DexterController.Measurements, so those formulas are ported here as pure Python:

- ForceConverter.ComposeRaw
- ForceConverter.ApplyScale
- ForceConverter.ComposeForce3
- CalibrationProfile.Default and Default3Channel

The visualizer path reinterprets raw ushort payloads as signed shorts before
doing math. `signed_int16` preserves that behavior for Python payloads that
arrive as unsigned values, especially BLE readings unpacked with struct format
`H`.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import cos, pi, sin
from typing import Sequence, Tuple, Union


Vector2 = Tuple[float, float]
Vector3 = Tuple[float, float, float]


@dataclass(frozen=True)
class LoadCellCalibration:
    scale: float = 0.0566
    offset: float = 144.0


@dataclass(frozen=True)
class CalibrationProfile:
    g: float
    angle1: float
    angle2: float
    scale_x: float
    offset_x: float
    scale_y: float
    offset_y: float
    scale_z: float
    offset_z: float
    lc1: LoadCellCalibration = LoadCellCalibration()
    lc2: LoadCellCalibration = LoadCellCalibration()
    lc3: LoadCellCalibration = LoadCellCalibration()


DEFAULT_4_CHANNEL = CalibrationProfile(
    g=9.8067,
    angle1=pi / 6.0,
    angle2=5.0 * pi / 6.0,
    scale_x=0.0315528794,
    offset_x=73.6415583568,
    scale_y=0.0315528794,
    offset_y=73.6415583568,
    scale_z=0.0571843043,
    offset_z=-204.9627108,
)

DEFAULT_3_CHANNEL = CalibrationProfile(
    g=9.8067,
    angle1=pi / 6.0,
    angle2=5.0 * pi / 6.0,
    scale_x=1.0,
    offset_x=0.0,
    scale_y=1.0,
    offset_y=0.0,
    scale_z=1.0,
    offset_z=0.0,
    lc1=LoadCellCalibration(),
    lc2=LoadCellCalibration(),
    lc3=LoadCellCalibration(),
)


def signed_int16(value: int) -> int:
    """Return the signed Int16 interpretation of an integer payload value."""

    value = int(value)
    if -32768 <= value <= 32767:
        return value

    value &= 0xFFFF
    if value >= 0x8000:
        return value - 0x10000
    return value


def signed_int16_values(raw: Sequence[int]) -> tuple[int, ...]:
    return tuple(signed_int16(value) for value in raw)


def compose_raw_4(
    raw: Sequence[int], calibration: CalibrationProfile = DEFAULT_4_CHANNEL
) -> Vector3:
    """Convert raw 4-channel readings to an unscaled force vector."""

    values = signed_int16_values(raw)
    if len(values) < 4:
        raise ValueError("raw must have at least 4 elements")

    c1 = cos(calibration.angle1)
    s1 = sin(calibration.angle1)
    c2 = cos(calibration.angle2)
    s2 = sin(calibration.angle2)

    x = values[3] * c1 + values[2] * c2
    y = -values[1] + values[3] * s1 + values[2] * s2
    z = -values[0]

    return (x, y, z)


def apply_scale_4(
    vector: Sequence[float], calibration: CalibrationProfile = DEFAULT_4_CHANNEL
) -> Vector3:
    """Apply per-axis calibration and convert grams-force to Newtons."""

    if len(vector) < 3:
        raise ValueError("vector must have at least 3 elements")

    k = calibration.g / 1000.0
    x = vector[0] * calibration.scale_x * k + calibration.offset_x * k
    y = vector[1] * calibration.scale_y * k + calibration.offset_y * k
    z = vector[2] * calibration.scale_z * k + calibration.offset_z * k
    return (x, y, z)


def compose_force_4(
    raw: Sequence[int], calibration: CalibrationProfile = DEFAULT_4_CHANNEL
) -> Vector3:
    """Compute `[x, y, z]` force in Newtons from 4 raw channels."""

    return apply_scale_4(compose_raw_4(raw, calibration), calibration)


def compose_force_3(
    raw: Sequence[int], calibration: CalibrationProfile = DEFAULT_3_CHANNEL
) -> Vector2:
    """Compute `[x, y]` force in Newtons from 3 raw channels.

    Channel mapping matches the .NET visualizer path:
    raw[0] -> lc2, raw[1] -> lc3, raw[2] -> lc1.
    """

    values = signed_int16_values(raw)
    if len(values) < 3:
        raise ValueError("raw must have at least 3 elements")

    k = calibration.g / 1000.0

    lc1 = (calibration.lc1.scale * values[2] + calibration.lc1.offset) * k
    lc2 = (calibration.lc2.scale * values[0] + calibration.lc2.offset) * k
    lc3 = (calibration.lc3.scale * values[1] + calibration.lc3.offset) * k

    c1 = cos(calibration.angle1)
    s1 = sin(calibration.angle1)
    c2 = cos(calibration.angle2)
    s2 = sin(calibration.angle2)

    x = lc3 * c1 + lc2 * c2
    y = -lc1 + lc3 * s1 + lc2 * s2

    return (x, y)


def compose_force(raw: Sequence[int]) -> Union[Vector2, Vector3]:
    """Choose the 3-channel or 4-channel conversion from raw width."""

    if len(raw) >= 4:
        return compose_force_4(raw[:4])
    if len(raw) >= 3:
        return compose_force_3(raw[:3])
    raise ValueError("raw must have at least 3 elements")
