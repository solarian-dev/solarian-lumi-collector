"""GoPro Metadata Format (GPMF) writer for Hero-10-compatible IMU tracks.

UMI's ``01_extract_gopro_imu.py`` runs OpenImuCameraCalibrator against a
``gpmd`` MP4 track. Accel is m/s², gyro is rad/s, stored ZXY at ~200 Hz.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np

# Hero 10-class scales: int16 / SCAL = SI unit. 418 matches published ACCL
# examples (±8 g). 1000 keeps ±32.7 rad/s inside int16 for a ±2000 dps gyro.
ACCL_SCALE = 418
GYRO_SCALE = 1000


@dataclass(frozen=True)
class ImuSample:
    t_s: float
    acc_zxy: tuple[float, float, float]
    gyr_zxy: tuple[float, float, float]
    temp_c: float = 35.0


def _pad4(data: bytes) -> bytes:
    return data + b"\x00" * ((4 - (len(data) % 4)) % 4)


def klv(key: str, typ: int, struct_size: int, data: bytes) -> bytes:
    key_b = key.encode("ascii")
    if len(key_b) != 4:
        raise ValueError(f"FourCC must be 4 chars, got {key!r}")
    repeat = 0 if struct_size == 0 else len(data) // struct_size
    header = key_b + bytes((typ, struct_size)) + struct.pack(">H", repeat)
    return header + _pad4(data)


def nest(key: str, children: bytes) -> bytes:
    padded = _pad4(children)
    return klv(key, 0, 1, padded)


def _string(key: str, text: str) -> bytes:
    raw = text.encode("ascii", errors="replace")
    return klv(key, ord("c"), len(raw), raw)


def _u32(key: str, value: int) -> bytes:
    return klv(key, ord("L"), 4, struct.pack(">I", value & 0xFFFFFFFF))


def _i16(key: str, value: int) -> bytes:
    return klv(key, ord("s"), 2, struct.pack(">h", int(value)))


def _f32(key: str, value: float) -> bytes:
    return klv(key, ord("f"), 4, struct.pack(">f", float(value)))


def _pack_zxy(values: np.ndarray, scale: int) -> bytes:
    """(N, 3) ZXY in SI units to big-endian int16, rounded half to even and clipped."""
    return np.clip(np.rint(values * scale), -32768, 32767).astype(">i2").tobytes()


def _stream(
    name: str,
    unit: str,
    fourcc: str,
    scale: int,
    values: np.ndarray,
    total_samples: int,
    temp_c: float,
) -> bytes:
    body = b"".join(
        (
            _u32("TSMP", total_samples),
            _string("STNM", name),
            _f32("TMPC", temp_c),
            _string("SIUN", unit),
            _i16("SCAL", scale),
            _string("ORIN", "ZXY"),
            klv(fourcc, ord("s"), 6, _pack_zxy(values, scale)),
        )
    )
    return nest("STRM", body)


def build_payload(
    samples: np.ndarray,
    *,
    serial: str,
    acc_total: int,
    gyr_total: int,
    device_name: str = "LUMI Collector",
    device_id: int = 1,
) -> bytes:
    """One ~1 s GPMF payload: DEVC with ACCL and GYRO streams.

    ``samples`` rows are t_s, acc ZXY, gyr ZXY, temp_c (``seal.sample_array``).
    """
    if not len(samples):
        return nest(
            "DEVC",
            _u32("DVID", device_id) + _string("DVNM", device_name) + _string("CASN", serial),
        )
    temp = sum(samples[:, 7].tolist()) / len(samples)
    body = b"".join(
        (
            _u32("DVID", device_id),
            _string("DVNM", device_name),
            _string("CASN", serial),
            _stream("Accelerometer", "m/s2", "ACCL", ACCL_SCALE, samples[:, 1:4], acc_total, temp),
            _stream("Gyroscope", "rad/s", "GYRO", GYRO_SCALE, samples[:, 4:7], gyr_total, temp),
        )
    )
    return nest("DEVC", body)


def build_payloads(
    samples: np.ndarray,
    *,
    serial: str,
    window_s: float = 1.0,
) -> list[bytes]:
    if not len(samples):
        return []
    payloads: list[bytes] = []
    total = 0
    t_s = samples[:, 0]
    t = t_s[0]
    end = t_s[-1]
    while t <= end + 1e-9:
        window = samples[(t_s >= t) & (t_s < t + window_s)]
        total += len(window)
        payloads.append(build_payload(window, serial=serial, acc_total=total, gyr_total=total))
        t += window_s
    return payloads


def write_gpmd(path, payloads: list[bytes]) -> None:
    """Concatenate payloads. ffmpeg treats this as a 1 Hz ``gpmd`` track."""
    path.write_bytes(b"".join(payloads))


def iter_klv(blob: bytes):
    """Yield (key, typ, struct_size, repeat, data) for tests / recovery."""
    i = 0
    n = len(blob)
    while i + 8 <= n:
        key = blob[i : i + 4].decode("ascii", errors="replace")
        typ = blob[i + 4]
        struct_size = blob[i + 5]
        repeat = struct.unpack(">H", blob[i + 6 : i + 8])[0]
        nbytes = struct_size * repeat
        data = blob[i + 8 : i + 8 + nbytes]
        padded = nbytes + ((4 - (nbytes % 4)) % 4)
        i += 8 + padded
        yield key, typ, struct_size, repeat, data


def extract_stream_samples(blob: bytes, fourcc: str, scale: int) -> list[tuple[float, float, float]]:
    out: list[tuple[float, float, float]] = []
    for key, typ, struct_size, repeat, data in iter_klv(blob):
        if typ == 0:
            out.extend(extract_stream_samples(data, fourcc, scale))
            continue
        if key != fourcc:
            continue
        for i in range(repeat):
            off = i * struct_size
            z, x, y = struct.unpack(">hhh", data[off : off + 6])
            out.append((z / scale, x / scale, y / scale))
    return out


def telemetry_rows(samples: np.ndarray, *, start: datetime, stream_name: str) -> Iterator[dict]:
    """gopro-telemetry-shaped samples (what extract_metadata_single.js emits), one at a time."""
    origin = start.astimezone(timezone.utc).timestamp()
    columns = slice(1, 4) if stream_name == "ACCL" else slice(4, 7)
    for row in samples:
        t_s = float(row[0])
        dt = datetime.fromtimestamp(origin + t_s, tz=timezone.utc)
        yield {
            "value": row[columns].tolist(),
            "cts": t_s * 1000.0,
            "date": dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "temperature [°C]": float(row[7]),
        }
