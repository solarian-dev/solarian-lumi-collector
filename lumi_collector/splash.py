"""Paint the loading frame as soon as SPI exists, then exit.

Imports stay limited to spidev and lgpio. PIL and luma take several seconds
when the SD card is busy during boot, and the glass is white until the first
bytes land. The panel keeps this image after the process exits.
"""

from __future__ import annotations

import time
from pathlib import Path

PANEL_READY = Path("/run/lumi-panel-ready")

# Waveshare 1.44 inch LCD HAT, same pins as DisplayConfig.
_DC = 25
_RST = 27
_BL = 24
_SPI_PORT = 0
_SPI_DEV = 0
# Controller RAM starts one column and two rows before the glass.
_H_OFF = 1
_V_OFF = 2
_WIDTH = 128
_HEIGHT = 128


def _cmd(chip, spi, command: int, *args: int) -> None:
    import lgpio

    lgpio.gpio_write(chip, _DC, 0)
    spi.writebytes2(bytes([command]))
    if args:
        lgpio.gpio_write(chip, _DC, 1)
        spi.writebytes2(bytes(args))


def _pixels(chip, spi, blob: bytes) -> None:
    import lgpio

    left, top = _H_OFF, _V_OFF
    right, bottom = left + _WIDTH, top + _HEIGHT
    _cmd(
        chip,
        spi,
        0x2A,
        left >> 8,
        left & 0xFF,
        (right - 1) >> 8,
        (right - 1) & 0xFF,
    )
    _cmd(
        chip,
        spi,
        0x2B,
        top >> 8,
        top & 0xFF,
        (bottom - 1) >> 8,
        (bottom - 1) & 0xFF,
    )
    _cmd(chip, spi, 0x2C)
    lgpio.gpio_write(chip, _DC, 1)
    for off in range(0, len(blob), 4096):
        spi.writebytes2(blob[off : off + 4096])


def _wake(chip, spi) -> None:
    """SWRESET and SLPOUT each need 120 ms or the ST7735S stays white."""
    import lgpio

    lgpio.gpio_write(chip, _RST, 0)
    time.sleep(0.1)
    lgpio.gpio_write(chip, _RST, 1)
    time.sleep(0.12)
    _cmd(chip, spi, 0x01)
    time.sleep(0.12)
    _cmd(chip, spi, 0x11)
    time.sleep(0.12)
    _cmd(chip, spi, 0xB1, 0x01, 0x2C, 0x2D)
    _cmd(chip, spi, 0xB2, 0x01, 0x2C, 0x2D)
    _cmd(chip, spi, 0xB3, 0x01, 0x2C, 0x2D, 0x01, 0x2C, 0x2D)
    _cmd(chip, spi, 0xB4, 0x07)
    _cmd(chip, spi, 0xC0, 0xA2, 0x02, 0x84)
    _cmd(chip, spi, 0xC1, 0xC5)
    _cmd(chip, spi, 0xC2, 0x0A, 0x00)
    _cmd(chip, spi, 0xC3, 0x8A, 0x2A)
    _cmd(chip, spi, 0xC4, 0x8A, 0xEE)
    _cmd(chip, spi, 0xC5, 0x0E)
    _cmd(chip, spi, 0x36, 0x68)
    _cmd(chip, spi, 0x20)
    _cmd(chip, spi, 0x3A, 0x06)
    _cmd(chip, spi, 0x13)
    _cmd(
        chip,
        spi,
        0xE0,
        0x0F, 0x1A, 0x0F, 0x18, 0x2F, 0x28, 0x20, 0x22,
        0x1F, 0x1B, 0x23, 0x37, 0x00, 0x07, 0x02, 0x10,
    )
    _cmd(
        chip,
        spi,
        0xE1,
        0x0F, 0x1B, 0x0F, 0x17, 0x33, 0x2C, 0x29, 0x2E,
        0x30, 0x30, 0x39, 0x3F, 0x00, 0x07, 0x03, 0x10,
    )
    _cmd(chip, spi, 0x29)


def main() -> int:
    import lgpio
    import spidev

    blob = Path(__file__).with_name("data").joinpath("loading.rgb").read_bytes()
    chip = lgpio.gpiochip_open(0)
    for pin in (_DC, _RST, _BL):
        lgpio.gpio_claim_output(chip, pin, 0)
    lgpio.gpio_write(chip, _BL, 1)
    spi = spidev.SpiDev()
    spi.open(_SPI_PORT, _SPI_DEV)
    spi.max_speed_hz = 8_000_000
    try:
        _wake(chip, spi)
        _pixels(chip, spi, blob)
    finally:
        spi.close()
    try:
        PANEL_READY.write_text("1\n")
    except OSError:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
