from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

from lumi_collector.config import PowerConfig


@dataclass
class PowerStatus:
    battery_pct: float | None
    charging: bool


class PowerMonitor:
    def __init__(self, cfg: PowerConfig, bus=None):
        self.cfg = cfg
        self._bus = bus
        self._charger = None

    def open(self) -> None:
        if self._bus is None:
            try:
                from smbus2 import SMBus

                self._bus = SMBus(1)
            except Exception:
                self._bus = None
        try:
            from gpiozero import DigitalInputDevice

            self._charger = DigitalInputDevice(self.cfg.charger_gpio, pull_up=False)
        except Exception:
            self._charger = None

    def read(self) -> PowerStatus:
        pct = None
        if self._bus is not None:
            try:
                raw = self._bus.read_word_data(self.cfg.fuel_gauge_address, 0x04)
                # MAX17040 SOC is 16-bit, high byte is percent
                pct = float((raw >> 8) & 0xFF) + ((raw & 0xFF) / 256.0)
                pct = max(0.0, min(100.0, pct))
            except Exception:
                pct = None
        charging = False
        if self._charger is not None:
            charging = bool(self._charger.value)
        return PowerStatus(battery_pct=pct, charging=charging)

    def shutdown(self) -> None:
        cmd = list(self.cfg.shutdown_cmd)
        if shutil.which(cmd[0]) is None:
            return
        subprocess.Popen(cmd)


def free_gb(path) -> float:
    usage = shutil.disk_usage(path)
    return usage.free / (1024 ** 3)
