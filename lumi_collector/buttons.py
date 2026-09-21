from __future__ import annotations

import time
from dataclasses import dataclass, field

from lumi_collector.config import ButtonConfig
from lumi_collector.state import Event


@dataclass
class _Hold:
    pressed: bool = False
    t0: float = 0.0
    long_fired: bool = False


class ButtonSource:
    """Edge detector. Feed raw pressed maps from gpiozero or tests."""

    def __init__(self, cfg: ButtonConfig):
        self.cfg = cfg
        self._prev: dict[str, bool] = {}
        self._key3 = _Hold()

    def poll(self, pressed: dict[str, bool], now: float | None = None) -> list[Event]:
        now = time.monotonic() if now is None else now
        events: list[Event] = []
        key3 = bool(pressed.get("key3"))
        if key3 and not self._key3.pressed:
            self._key3 = _Hold(pressed=True, t0=now)
        elif key3 and self._key3.pressed and not self._key3.long_fired:
            if now - self._key3.t0 >= self.cfg.long_press_s:
                events.append(Event.KEY3_LONG)
                self._key3.long_fired = True
        elif not key3 and self._key3.pressed:
            if not self._key3.long_fired:
                events.append(Event.KEY3)
            self._key3 = _Hold()

        for name, event in (
            ("key1", Event.KEY1),
            ("key2", Event.KEY2),
            ("joy_up", Event.JOY_UP),
            ("joy_down", Event.JOY_DOWN),
            ("joy_left", Event.JOY_LEFT),
            ("joy_right", Event.JOY_RIGHT),
            ("joy_press", Event.JOY_PRESS),
        ):
            is_down = bool(pressed.get(name))
            was = self._prev.get(name, False)
            if is_down and not was:
                events.append(event)
            self._prev[name] = is_down
        return events


@dataclass
class GpioButtons:
    cfg: ButtonConfig
    devices: dict = field(default_factory=dict)

    def open(self) -> None:
        from gpiozero import Button

        mapping = {
            "key1": self.cfg.key1,
            "key2": self.cfg.key2,
            "key3": self.cfg.key3,
            "joy_up": self.cfg.joy_up,
            "joy_down": self.cfg.joy_down,
            "joy_left": self.cfg.joy_left,
            "joy_right": self.cfg.joy_right,
            "joy_press": self.cfg.joy_press,
        }
        self.devices = {
            name: Button(pin, pull_up=True, bounce_time=0.02) for name, pin in mapping.items()
        }

    def read(self) -> dict[str, bool]:
        return {name: dev.is_pressed for name, dev in self.devices.items()}

    def close(self) -> None:
        for dev in self.devices.values():
            dev.close()
        self.devices.clear()
