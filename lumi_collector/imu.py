from __future__ import annotations

import math
import time
from typing import Protocol

from lumi_collector.config import ImuConfig
from lumi_collector.gpmf import ImuSample


class Imu(Protocol):
    """``read`` is the live value. A take is ``start_stream``, ``poll`` every
    ``poll_interval_s``, then ``finish_stream`` for its timed samples."""

    poll_interval_s: float

    def open(self) -> None: ...
    def close(self) -> None: ...
    def read(self) -> ImuSample | None: ...
    def start_stream(self) -> None: ...
    def poll(self) -> None: ...
    def finish_stream(self) -> list[ImuSample]: ...
    def stream_info(self) -> dict: ...


class PolledStream:
    """A take of ``read`` calls at the configured rate; each is timed when read."""

    cfg: ImuConfig

    @property
    def poll_interval_s(self) -> float:
        return 1.0 / float(self.cfg.rate_hz)

    def start_stream(self) -> None:
        self._stream: list[ImuSample] = []

    def poll(self) -> None:
        sample = self.read()
        if sample is not None:
            self._stream.append(sample)

    def finish_stream(self) -> list[ImuSample]:
        samples, self._stream = self._stream, []
        return samples

    def stream_info(self) -> dict:
        return {"timing": "read", "clock_origin_monotonic_s": self._t0}


def _map_axes(
    xyz: tuple[float, float, float],
    axis_map: tuple[int, int, int],
    sign: tuple[int, int, int],
) -> tuple[float, float, float]:
    return (
        sign[0] * xyz[axis_map[0]],
        sign[1] * xyz[axis_map[1]],
        sign[2] * xyz[axis_map[2]],
    )


class MockImu(PolledStream):
    """Deterministic 200 Hz stand-in for tests and hardware-less boots."""

    def __init__(self, cfg: ImuConfig):
        self.cfg = cfg
        self._t0 = 0.0
        self._n = 0
        self._open = False

    def open(self) -> None:
        self._t0 = time.monotonic()
        self._n = 0
        self._open = True

    def close(self) -> None:
        self._open = False

    def read(self) -> ImuSample | None:
        if not self._open:
            return None
        t = self._n / float(self.cfg.rate_hz)
        self._n += 1
        omega = 2.0 * math.pi * 0.5
        acc = (0.0, 0.0, 9.80665 + 0.05 * math.sin(omega * t))
        gyr = (0.0, 0.1 * math.sin(omega * t), 0.0)
        return ImuSample(
            t_s=t,
            acc_zxy=_map_axes(acc, self.cfg.acc_map, self.cfg.acc_sign),
            gyr_zxy=_map_axes(gyr, self.cfg.gyr_map, self.cfg.gyr_sign),
            temp_c=36.0,
        )


class Mpu6050Imu:
    """GY-521 breakout: InvenSense MPU-6050 at 200 Hz, streamed through its FIFO.

    ±8 g (4096 LSB/g) and ±2000 °/s (16.4 LSB per °/s). The GPMF track still
    stores acceleration in m/s² and gyro in rad/s.

    During a take the chip queues accel and gyro on its own sample clock, so
    a late poll loses nothing and every sample keeps its place in time. Times
    come from the sample count: sample k of a segment is at t0 + k * dt, with
    dt and t0 fitted to the poll times (see ``FifoClock``). An overflow drops
    the queue and starts a new segment.
    """

    PWR_MGMT_1 = 0x6B
    SMPLRT_DIV = 0x19
    CONFIG = 0x1A
    GYRO_CONFIG = 0x1B
    ACCEL_CONFIG = 0x1C
    FIFO_EN = 0x23
    INT_ENABLE = 0x38
    INT_STATUS = 0x3A
    ACCEL_XOUT_H = 0x3B
    TEMP_OUT_H = 0x41
    USER_CTRL = 0x6A
    FIFO_COUNT_H = 0x72
    FIFO_R_W = 0x74
    WHO_AM_I = 0x75
    EXPECTED_ID = 0x68

    FIFO_ACCEL_GYRO = 0x78  # XG, YG, ZG and ACCEL: 12 bytes, ax ay az gx gy gz
    FIFO_OFLOW = 0x10
    USER_FIFO_EN = 0x40
    USER_FIFO_RESET = 0x04
    FRAME_BYTES = 12
    FIFO_BYTES = 1024
    # InvenSense's driver waits 50 ms; at 200 Hz that is about 10 samples.
    FIFO_RESET_S = 0.05
    # SMBus block reads stop at 32 bytes; two samples per transfer.
    READ_CHUNK = 24
    # DLPF_CFG 3: 44 Hz accel / 42 Hz gyro bandwidth, about 4.8 ms group delay.
    DLPF_CFG = 0x03
    FILTER_DELAY_S = 0.0048
    # 85 samples fill the FIFO in 425 ms; draining at 40 ms leaves a 10x margin.
    poll_interval_s = 0.04

    def __init__(self, cfg: ImuConfig, bus=None):
        self.cfg = cfg
        self._bus = bus
        self._t0 = 0.0
        self._clock = FifoClock(1.0 / float(cfg.rate_hz))
        self._raw: list[tuple[int, int, tuple[int, ...], float]] = []

    def open(self) -> None:
        if self._bus is None:
            from smbus2 import SMBus

            self._bus = SMBus(1)
        # 0x01 selects the X-gyro PLL. The chip needs ~100 ms after leaving sleep.
        self._bus.write_byte_data(self.cfg.address, self.PWR_MGMT_1, 0x01)
        time.sleep(0.1)
        chip = self._bus.read_byte_data(self.cfg.address, self.WHO_AM_I)
        if chip != self.EXPECTED_ID:
            raise RuntimeError(f"MPU-6050 chip id 0x{chip:02x}, expected 0x68")
        divider = round(1000 / self.cfg.rate_hz) - 1  # 1 kHz with the DLPF on
        self._bus.write_byte_data(self.cfg.address, self.SMPLRT_DIV, divider)
        self._bus.write_byte_data(self.cfg.address, self.CONFIG, self.DLPF_CFG)
        self._bus.write_byte_data(self.cfg.address, self.GYRO_CONFIG, 0x18)  # ±2000 dps
        self._bus.write_byte_data(self.cfg.address, self.ACCEL_CONFIG, 0x10)  # ±8 g
        # INT_STATUS only latches the overflow bit when its interrupt is enabled.
        self._bus.write_byte_data(self.cfg.address, self.INT_ENABLE, self.FIFO_OFLOW)
        self._bus.write_byte_data(self.cfg.address, self.FIFO_EN, self.FIFO_ACCEL_GYRO)
        self._t0 = time.monotonic()

    def close(self) -> None:
        bus = self._bus
        if bus is not None and hasattr(bus, "close"):
            bus.close()

    def read(self) -> ImuSample | None:
        raw = self._bus.read_i2c_block_data(self.cfg.address, self.ACCEL_XOUT_H, 14)
        values = [_i16(raw[i], raw[i + 1]) for i in range(0, 14, 2)]
        return self._sample(
            time.monotonic() - self._t0,
            (*values[0:3], *values[4:7]),
            values[3] / 340.0 + 36.53,
        )

    def _sample(self, t_s: float, counts: tuple[int, ...], temp_c: float) -> ImuSample:
        ax, ay, az = (c / 4096.0 * 9.80665 for c in counts[0:3])
        gx, gy, gz = (math.radians(c / 16.4) for c in counts[3:6])
        return ImuSample(
            t_s=t_s,
            acc_zxy=_map_axes((ax, ay, az), self.cfg.acc_map, self.cfg.acc_sign),
            gyr_zxy=_map_axes((gx, gy, gz), self.cfg.gyr_map, self.cfg.gyr_sign),
            temp_c=temp_c,
        )

    def _reset_fifo(self) -> None:
        # Resetting while the sensors still write can keep half a sample, and
        # every later read then pairs gyro with the next accel. InvenSense's
        # driver stops the writes, resets, waits, then restarts them.
        addr = self.cfg.address
        self._bus.write_byte_data(addr, self.FIFO_EN, 0)
        self._bus.write_byte_data(addr, self.USER_CTRL, 0)
        self._bus.write_byte_data(addr, self.USER_CTRL, self.USER_FIFO_RESET)
        time.sleep(self.FIFO_RESET_S)
        self._bus.write_byte_data(addr, self.USER_CTRL, self.USER_FIFO_EN)
        self._bus.write_byte_data(addr, self.FIFO_EN, self.FIFO_ACCEL_GYRO)
        self._bus.read_byte_data(addr, self.INT_STATUS)

    def start_stream(self) -> None:
        self._raw = []
        self._clock = FifoClock(1.0 / float(self.cfg.rate_hz))
        self._reset_fifo()
        self._clock.start_segment()

    def poll(self) -> None:
        addr = self.cfg.address
        status = self._bus.read_byte_data(addr, self.INT_STATUS)
        hi, lo = self._bus.read_i2c_block_data(addr, self.FIFO_COUNT_H, 2)
        count = (hi << 8) | lo
        t_read = time.monotonic() - self._t0
        if status & self.FIFO_OFLOW or count >= self.FIFO_BYTES:
            self._reset_fifo()
            self._clock.start_segment()
            return
        n = count // self.FRAME_BYTES
        if n == 0:
            return
        data: list[int] = []
        remaining = n * self.FRAME_BYTES
        while remaining:
            size = min(self.READ_CHUNK, remaining)
            data += self._bus.read_i2c_block_data(addr, self.FIFO_R_W, size)
            remaining -= size
        t_hi, t_lo = self._bus.read_i2c_block_data(addr, self.TEMP_OUT_H, 2)
        temp_c = _i16(t_hi, t_lo) / 340.0 + 36.53
        segment, first = self._clock.add_poll(n, t_read)
        for i in range(n):
            frame = data[i * self.FRAME_BYTES : (i + 1) * self.FRAME_BYTES]
            counts = tuple(_i16(frame[j], frame[j + 1]) for j in range(0, self.FRAME_BYTES, 2))
            self._raw.append((segment, first + i, counts, temp_c))

    def finish_stream(self) -> list[ImuSample]:
        times = self._clock.fit()
        samples = [
            # The filter delays the signal; the sample describes motion that much earlier.
            self._sample(times[segment](k) - self.FILTER_DELAY_S, counts, temp_c)
            for segment, k, counts, temp_c in self._raw
        ]
        self._raw = []
        return samples

    def stream_info(self) -> dict:
        return {
            "timing": "fifo",
            "clock_origin_monotonic_s": self._t0,
            "filter_delay_s": self.FILTER_DELAY_S,
            "segments": self._clock.segment_count,
            "sample_period_s": self._clock.fitted_period_s,
        }


class FifoClock:
    """Sample times for a FIFO stream from (samples so far, poll time) pairs.

    A poll at time t that has seen k samples bounds sample k-1 to at or
    before t. The period is the slope of poll time over sample count; the
    start is the tightest bound, which is the poll with the least latency.
    """

    MIN_POLLS_FOR_SLOPE = 5

    def __init__(self, nominal_period_s: float):
        self.nominal_period_s = nominal_period_s
        self._segments: list[list[tuple[int, float]]] = []
        self._counts: list[int] = []
        self.fitted_period_s = nominal_period_s

    @property
    def segment_count(self) -> int:
        return len(self._segments)

    def start_segment(self) -> None:
        self._segments.append([])
        self._counts.append(0)

    def add_poll(self, n_samples: int, t_read: float) -> tuple[int, int]:
        """Record a poll; returns (segment, index of its first sample)."""
        segment = len(self._segments) - 1
        first = self._counts[segment]
        self._counts[segment] += n_samples
        self._segments[segment].append((self._counts[segment], t_read))
        return segment, first

    def fit(self) -> list:
        """One callable per segment: sample index to seconds."""
        slopes = []
        for polls in self._segments:
            if len(polls) >= self.MIN_POLLS_FOR_SLOPE:
                k = [p[0] for p in polls]
                t = [p[1] for p in polls]
                slopes.append((_slope(k, t), len(polls)))
        # The chip's clock is one oscillator; pool segments weighted by polls.
        if slopes:
            total = sum(w for _, w in slopes)
            self.fitted_period_s = sum(s * w for s, w in slopes) / total
        dt = self.fitted_period_s
        clocks = []
        for polls in self._segments:
            if polls:
                t0 = min(t - (k - 1) * dt for k, t in polls)
            else:
                t0 = 0.0
            clocks.append(lambda k, t0=t0: t0 + k * dt)
        return clocks


def _slope(x: list[int], y: list[float]) -> float:
    n = len(x)
    mx = sum(x) / n
    my = sum(y) / n
    sxx = sum((xi - mx) ** 2 for xi in x)
    sxy = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    return sxy / sxx


class Bmi270Imu(PolledStream):
    """Bosch BMI270 (same family as the Hero 9/10 BMI260).

    Full features need the official config blob at
    ``data/bmi270_config.bin`` (see ``deploy/setup.sh``).
    """

    CHIP_ID = 0x00
    PWR_CONF = 0x7C
    PWR_CTRL = 0x7D
    INIT_CTRL = 0x59
    INIT_DATA = 0x5E
    ACC_CONF = 0x40
    ACC_RANGE = 0x41
    GYR_CONF = 0x42
    GYR_RANGE = 0x43
    DATA_8 = 0x0C
    EXPECTED_ID = 0x24

    def __init__(self, cfg: ImuConfig, bus=None, config_blob: bytes = b""):
        self.cfg = cfg
        self._bus = bus
        self._config_blob = config_blob
        self._t0 = 0.0

    def open(self) -> None:
        if self._bus is None:
            from smbus2 import SMBus

            self._bus = SMBus(1)
        chip = self._bus.read_byte_data(self.cfg.address, self.CHIP_ID)
        if chip != self.EXPECTED_ID:
            raise RuntimeError(f"BMI270 chip id 0x{chip:02x}, expected 0x24")
        self._bus.write_byte_data(self.cfg.address, self.PWR_CONF, 0x00)
        time.sleep(0.001)
        if self._config_blob:
            self._bus.write_byte_data(self.cfg.address, self.INIT_CTRL, 0x00)
            for off in range(0, len(self._config_blob), 16):
                chunk = self._config_blob[off : off + 16]
                self._bus.write_i2c_block_data(self.cfg.address, self.INIT_DATA, list(chunk))
            self._bus.write_byte_data(self.cfg.address, self.INIT_CTRL, 0x01)
            time.sleep(0.05)
        # acc ODR 200 Hz, OSRnorm, ±8 g; gyro ODR 200 Hz, ±2000 dps
        self._bus.write_byte_data(self.cfg.address, self.ACC_CONF, 0xA8)
        self._bus.write_byte_data(self.cfg.address, self.ACC_RANGE, 0x02)
        self._bus.write_byte_data(self.cfg.address, self.GYR_CONF, 0xA9)
        self._bus.write_byte_data(self.cfg.address, self.GYR_RANGE, 0x00)
        self._bus.write_byte_data(self.cfg.address, self.PWR_CTRL, 0x0E)
        self._t0 = time.monotonic()

    def close(self) -> None:
        bus = self._bus
        if bus is not None and hasattr(bus, "close"):
            bus.close()

    def read(self) -> ImuSample | None:
        raw = self._bus.read_i2c_block_data(self.cfg.address, self.DATA_8, 12)
        gx = math.radians(_i16(raw[1], raw[0]) / 16.4)
        gy = math.radians(_i16(raw[3], raw[2]) / 16.4)
        gz = math.radians(_i16(raw[5], raw[4]) / 16.4)
        ax = _i16(raw[7], raw[6]) / 4096.0 * 9.80665
        ay = _i16(raw[9], raw[8]) / 4096.0 * 9.80665
        az = _i16(raw[11], raw[10]) / 4096.0 * 9.80665
        return ImuSample(
            t_s=time.monotonic() - self._t0,
            acc_zxy=_map_axes((ax, ay, az), self.cfg.acc_map, self.cfg.acc_sign),
            gyr_zxy=_map_axes((gx, gy, gz), self.cfg.gyr_map, self.cfg.gyr_sign),
            temp_c=35.0,
        )


def _i16(msb: int, lsb: int) -> int:
    value = (msb << 8) | lsb
    return value - 65536 if value & 0x8000 else value


def open_imu(cfg: ImuConfig, config_blob: bytes = b"") -> Imu:
    driver = cfg.driver.lower()
    if driver == "mock":
        imu = MockImu(cfg)
    elif driver == "mpu6050":
        imu = Mpu6050Imu(cfg)
    elif driver == "bmi270":
        imu = Bmi270Imu(cfg, config_blob=config_blob)
    else:
        raise ValueError(f"unknown imu driver {cfg.driver!r}")
    imu.open()
    return imu
