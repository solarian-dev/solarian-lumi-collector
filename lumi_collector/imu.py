from __future__ import annotations

import math
import time
from typing import Protocol

from lumi_collector.config import ImuConfig
from lumi_collector.gpmf import ImuSample


class Imu(Protocol):
    def open(self) -> None: ...
    def close(self) -> None: ...
    def read(self) -> ImuSample | None: ...


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


class MockImu:
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
    """GY-521 breakout: InvenSense MPU-6050, polled at 200 Hz.

    ±8 g (4096 LSB/g) and ±2000 °/s (16.4 LSB per °/s). The GPMF track still
    stores acceleration in m/s² and gyro in rad/s.
    """

    PWR_MGMT_1 = 0x6B
    SMPLRT_DIV = 0x19
    CONFIG = 0x1A
    GYRO_CONFIG = 0x1B
    ACCEL_CONFIG = 0x1C
    ACCEL_XOUT_H = 0x3B
    WHO_AM_I = 0x75
    EXPECTED_ID = 0x68

    def __init__(self, cfg: ImuConfig, bus=None):
        self.cfg = cfg
        self._bus = bus
        self._t0 = 0.0

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
        self._bus.write_byte_data(self.cfg.address, self.SMPLRT_DIV, 4)  # 1 kHz / (4+1) = 200
        self._bus.write_byte_data(self.cfg.address, self.CONFIG, 0x03)
        self._bus.write_byte_data(self.cfg.address, self.GYRO_CONFIG, 0x18)  # ±2000 dps
        self._bus.write_byte_data(self.cfg.address, self.ACCEL_CONFIG, 0x10)  # ±8 g
        self._t0 = time.monotonic()

    def close(self) -> None:
        bus = self._bus
        if bus is not None and hasattr(bus, "close"):
            bus.close()

    def read(self) -> ImuSample | None:
        raw = self._bus.read_i2c_block_data(self.cfg.address, self.ACCEL_XOUT_H, 14)
        ax = _i16(raw[0], raw[1]) / 4096.0 * 9.80665
        ay = _i16(raw[2], raw[3]) / 4096.0 * 9.80665
        az = _i16(raw[4], raw[5]) / 4096.0 * 9.80665
        temp = _i16(raw[6], raw[7]) / 340.0 + 36.53
        gx = math.radians(_i16(raw[8], raw[9]) / 16.4)
        gy = math.radians(_i16(raw[10], raw[11]) / 16.4)
        gz = math.radians(_i16(raw[12], raw[13]) / 16.4)
        return ImuSample(
            t_s=time.monotonic() - self._t0,
            acc_zxy=_map_axes((ax, ay, az), self.cfg.acc_map, self.cfg.acc_sign),
            gyr_zxy=_map_axes((gx, gy, gz), self.cfg.gyr_map, self.cfg.gyr_sign),
            temp_c=temp,
        )


class Bmi270Imu:
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
