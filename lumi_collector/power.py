from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

from lumi_collector.config import PowerConfig


# INA219 bus-voltage register. Bits 15:3 are the voltage, 4 mV each.
_INA219_BUS_VOLTAGE = 0x02
# Single Li-ion cell. Readings outside this are a bad sample, not 0%.
_CELL_EMPTY_V = 3.0
_CELL_FULL_V = 4.2


def hold_percent(shown: float | None, pct: float | None) -> float | None:
    """Keep the displayed integer until the reading has moved a full percent past it."""
    if pct is None:
        return shown
    if shown is None:
        return float(round(pct))
    if pct >= shown + 1.0 or pct <= shown - 1.0:
        return float(round(pct))
    return shown


def percent_from_ina219_word(raw: int) -> float | None:
    """SMBus delivers the register little-endian; the INA219 sends it big-endian."""
    be = ((raw & 0xFF) << 8) | ((raw >> 8) & 0xFF)
    volts = ((be >> 3) & 0x1FFF) * 0.004
    if volts < 2.5 or volts > 4.5:
        return None
    pct = (volts - _CELL_EMPTY_V) / (_CELL_FULL_V - _CELL_EMPTY_V) * 100.0
    return max(0.0, min(100.0, pct))


@dataclass
class PowerStatus:
    battery_pct: float | None
    charging: bool


class PowerMonitor:
    def __init__(self, cfg: PowerConfig, bus=None):
        self.cfg = cfg
        self._bus = bus
        self._charger = None
        self._smooth: float | None = None
        self._shown: float | None = None

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
                raw = self._bus.read_word_data(self.cfg.fuel_gauge_address, _INA219_BUS_VOLTAGE)
                pct = percent_from_ina219_word(raw)
            except Exception:
                pct = None
            if pct is not None:
                if self._smooth is None:
                    self._smooth = pct
                else:
                    self._smooth = 0.2 * pct + 0.8 * self._smooth
                self._shown = hold_percent(self._shown, self._smooth)
        charging = False
        if self._charger is not None:
            charging = bool(self._charger.value)
        return PowerStatus(battery_pct=self._shown, charging=charging)

    def shutdown(self) -> None:
        cmd = list(self.cfg.shutdown_cmd)
        if shutil.which(cmd[0]) is None:
            return
        subprocess.Popen(cmd)


def free_gb(path) -> float:
    usage = shutil.disk_usage(path)
    return usage.free / (1024 ** 3)
