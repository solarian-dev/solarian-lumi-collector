from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class VideoConfig:
    width: int = 1440
    height: int = 1080
    fps: int = 30
    bitrate: int = 10_000_000
    lores_size: tuple[int, int] = (320, 240)
    # Full binned IMX477 mode. The encode size is this frame scaled to 4:3.
    sensor_width: int = 2028
    sensor_height: int = 1520
    sensor_bit_depth: int = 12
    tuning_file: str = "/home/pi/imx477_fisheye.json"


@dataclass(frozen=True)
class ImuConfig:
    driver: str = "mpu6050"
    address: int = 0x68
    rate_hz: int = 200
    acc_map: tuple[int, int, int] = (2, 0, 1)
    gyr_map: tuple[int, int, int] = (2, 0, 1)
    acc_sign: tuple[int, int, int] = (1, 1, 1)
    gyr_sign: tuple[int, int, int] = (1, 1, 1)


@dataclass(frozen=True)
class ButtonConfig:
    key1: int = 21
    key2: int = 20
    key3: int = 16
    joy_up: int = 6
    joy_down: int = 19
    joy_left: int = 5
    joy_right: int = 26
    joy_press: int = 13
    long_press_s: float = 2.0


@dataclass(frozen=True)
class DisplayConfig:
    width: int = 128
    height: int = 128
    spi_port: int = 0
    spi_device: int = 0
    gpio_dc: int = 25
    gpio_rst: int = 27
    gpio_bl: int = 24
    rotate: int = 0


@dataclass(frozen=True)
class PowerConfig:
    fuel_gauge_address: int = 0x43
    charger_gpio: int = 4
    shutdown_cmd: tuple[str, ...] = ("sudo", "shutdown", "-h", "now")


@dataclass(frozen=True)
class CollectorConfig:
    data_dir: Path = Path("/home/pi/lumi")
    camera_serial: str = ""
    primarykey_url: str = "https://app.primarykey.ai"
    setup_port: int = 8080
    video: VideoConfig = field(default_factory=VideoConfig)
    imu: ImuConfig = field(default_factory=ImuConfig)
    buttons: ButtonConfig = field(default_factory=ButtonConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)
    power: PowerConfig = field(default_factory=PowerConfig)

    @property
    def raw_videos_dir(self) -> Path:
        return self.data_dir / "raw_videos"

    @property
    def work_dir(self) -> Path:
        return self.data_dir / "work"

    @property
    def credentials_path(self) -> Path:
        return self.data_dir / "credentials.json"

    @property
    def catalog_path(self) -> Path:
        return self.data_dir / "catalog.json"


def _tuple3(value: Any, default: tuple[int, int, int]) -> tuple[int, int, int]:
    if value is None:
        return default
    nums = [int(x) for x in value]
    return (nums[0], nums[1], nums[2])


def load_config(path: Path | None = None) -> CollectorConfig:
    raw: dict[str, Any] = {}
    if path is not None and path.is_file():
        raw = yaml.safe_load(path.read_text()) or {}

    video_raw = raw.get("video") or {}
    lores = video_raw.get("lores_size") or [320, 240]
    imu_raw = raw.get("imu") or {}
    btn_raw = raw.get("buttons") or {}
    disp_raw = raw.get("display") or {}
    pwr_raw = raw.get("power") or {}
    shutdown = pwr_raw.get("shutdown_cmd") or ["sudo", "shutdown", "-h", "now"]

    return CollectorConfig(
        data_dir=Path(raw.get("data_dir") or "/home/pi/lumi"),
        camera_serial=str(raw.get("camera_serial") or ""),
        primarykey_url=str(raw.get("primarykey_url") or "https://app.primarykey.ai").rstrip("/"),
        setup_port=int(raw.get("setup_port") or 8080),
        video=VideoConfig(
            width=int(video_raw.get("width") or 1440),
            height=int(video_raw.get("height") or 1080),
            fps=int(video_raw.get("fps") or 30),
            bitrate=int(video_raw.get("bitrate") or 10_000_000),
            lores_size=(int(lores[0]), int(lores[1])),
            sensor_width=int(video_raw.get("sensor_width") or 2028),
            sensor_height=int(video_raw.get("sensor_height") or 1520),
            sensor_bit_depth=int(video_raw.get("sensor_bit_depth") or 12),
            tuning_file=str(video_raw.get("tuning_file") or "/home/pi/imx477_fisheye.json"),
        ),
        imu=ImuConfig(
            driver=str(imu_raw.get("driver") or "mpu6050"),
            address=int(imu_raw.get("address") or 0x68),
            rate_hz=int(imu_raw.get("rate_hz") or 200),
            acc_map=_tuple3(imu_raw.get("acc_map"), (2, 0, 1)),
            gyr_map=_tuple3(imu_raw.get("gyr_map"), (2, 0, 1)),
            acc_sign=_tuple3(imu_raw.get("acc_sign"), (1, 1, 1)),
            gyr_sign=_tuple3(imu_raw.get("gyr_sign"), (1, 1, 1)),
        ),
        buttons=ButtonConfig(
            key1=int(btn_raw.get("key1") or 21),
            key2=int(btn_raw.get("key2") or 20),
            key3=int(btn_raw.get("key3") or 16),
            joy_up=int(btn_raw.get("joy_up") or 6),
            joy_down=int(btn_raw.get("joy_down") or 19),
            joy_left=int(btn_raw.get("joy_left") or 5),
            joy_right=int(btn_raw.get("joy_right") or 26),
            joy_press=int(btn_raw.get("joy_press") or 13),
            long_press_s=float(btn_raw.get("long_press_s") or 2.0),
        ),
        display=DisplayConfig(
            width=int(disp_raw.get("width") or 128),
            height=int(disp_raw.get("height") or 128),
            spi_port=int(disp_raw.get("spi_port") or 0),
            spi_device=int(disp_raw.get("spi_device") or 0),
            gpio_dc=int(disp_raw.get("gpio_dc") or 25),
            gpio_rst=int(disp_raw.get("gpio_rst") or 27),
            gpio_bl=int(disp_raw.get("gpio_bl") or 24),
            rotate=int(disp_raw.get("rotate") or 0),
        ),
        power=PowerConfig(
            fuel_gauge_address=int(pwr_raw.get("fuel_gauge_address") or 0x43),
            charger_gpio=int(pwr_raw.get("charger_gpio") or 4),
            shutdown_cmd=tuple(str(x) for x in shutdown),
        ),
    )


def resolve_camera_serial(cfg: CollectorConfig) -> str:
    if cfg.camera_serial:
        return cfg.camera_serial
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.is_file():
        for line in cpuinfo.read_text().splitlines():
            if line.lower().startswith("serial"):
                hex_id = line.split(":")[-1].strip()[-8:].upper()
                return f"LUMI{hex_id}"
    return "LUMI00000000"
