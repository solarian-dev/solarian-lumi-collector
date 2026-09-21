from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

from lumi_collector.catalog import Episode
from lumi_collector.state import Screen, UiState

BG = (8, 10, 14)
FG = (230, 232, 236)
MUTED = (130, 138, 148)
RED = (220, 40, 48)
GREEN = (46, 184, 92)
AMBER = (232, 176, 48)
CARD = (22, 26, 32)


@dataclass
class Hud:
    battery_pct: float | None
    charging: bool
    free_gb: float
    rec_s: float
    setup_url: str
    wifi: str


def _font(size: int):
    try:
        return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _fmt_s(seconds: float) -> str:
    total = max(0, int(seconds))
    m, s = divmod(total, 60)
    return f"{m:02d}:{s:02d}"


def _fmt_size(n: int) -> str:
    if n >= 1_000_000_000:
        return f"n/a" if n < 0 else f"{n / 1_000_000_000:.1f} GB"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f} MB"
    return f"{n / 1000:.0f} KB"


def render(
    state: UiState,
    episodes: list[Episode],
    hud: Hud,
    *,
    size: tuple[int, int] = (240, 240),
    preview=None,
) -> Image.Image:
    img = Image.new("RGB", size, BG)
    d = ImageDraw.Draw(img)
    title = _font(16)
    body = _font(13)
    small = _font(11)

    d.rectangle((0, 0, size[0], 22), fill=CARD)
    bat = "--" if hud.battery_pct is None else f"{hud.battery_pct:.0f}%"
    plug = "+" if hud.charging else ""
    d.text((6, 4), f"{bat}{plug}", font=small, fill=GREEN if (hud.battery_pct or 0) > 20 else AMBER)
    d.text((size[0] - 78, 4), f"{hud.free_gb:.1f}G", font=small, fill=MUTED)

    if state.screen is Screen.HOME:
        d.text((8, 32), "Lumi collector", font=title, fill=FG)
        last = episodes[-1] if episodes else None
        if last is None:
            d.text((8, 60), "no episodes", font=body, fill=MUTED)
        else:
            d.text((8, 60), last.filename, font=body, fill=FG)
            d.text(
                (8, 80),
                f"{_fmt_s(last.duration_s)}  {_fmt_size(last.size_bytes)}",
                font=small,
                fill=MUTED,
            )
        d.text((8, 160), "KEY1  record", font=small, fill=MUTED)
        d.text((8, 178), "KEY2  delete last", font=small, fill=MUTED)
        d.text((8, 196), "stick  library", font=small, fill=MUTED)
        if state.status:
            d.text((8, 214), state.status, font=small, fill=AMBER)

    elif state.screen is Screen.RECORDING:
        d.ellipse((12, 36, 28, 52), fill=RED)
        d.text((36, 34), "REC", font=title, fill=RED)
        d.text((12, 70), _fmt_s(hud.rec_s), font=_font(36), fill=FG)
        d.text((12, 130), "KEY1 stop", font=body, fill=MUTED)
        if preview is not None:
            thumb = preview.resize((96, 96))
            img.paste(thumb, (132, 120))

    elif state.screen is Screen.LIBRARY:
        d.text((8, 28), "episodes", font=title, fill=FG)
        if not episodes:
            d.text((8, 60), "empty", font=body, fill=MUTED)
        else:
            top = max(0, state.cursor - 3)
            for i, ep in enumerate(episodes[top : top + 7]):
                y = 52 + i * 24
                idx = top + i
                mark = "*" if ep.filename in state.selected else " "
                line = f"{mark}{ep.filename[4:8]} {_fmt_s(ep.duration_s)}"
                fill = FG if idx == state.cursor else MUTED
                if idx == state.cursor:
                    d.rectangle((4, y - 2, size[0] - 4, y + 18), fill=CARD)
                d.text((8, y), line, font=body, fill=fill)

    elif state.screen is Screen.EPISODE:
        ep = episodes[state.cursor] if episodes else None
        d.text((8, 28), "episode", font=title, fill=FG)
        if ep is not None:
            d.text((8, 56), ep.filename, font=body, fill=FG)
            d.text((8, 80), ep.started_at.replace("T", " ")[:19], font=small, fill=MUTED)
            d.text((8, 100), f"{_fmt_s(ep.duration_s)}   {_fmt_size(ep.size_bytes)}", font=body, fill=FG)
            d.text((8, 128), "uploaded" if ep.uploaded else "local", font=small, fill=GREEN if ep.uploaded else AMBER)
        d.text((8, 180), "press  upload", font=small, fill=MUTED)
        d.text((8, 198), "right  play", font=small, fill=MUTED)
        d.text((8, 216), "KEY2   delete", font=small, fill=MUTED)

    elif state.screen is Screen.CONFIRM_DELETE:
        d.text((8, 40), "delete?", font=title, fill=RED)
        for i, name in enumerate(state.pending_delete[:6]):
            d.text((8, 72 + i * 18), name, font=small, fill=FG)
        d.text((8, 200), "press yes   KEY3 no", font=small, fill=MUTED)

    elif state.screen is Screen.UPLOAD:
        d.text((8, 40), "uploading", font=title, fill=FG)
        d.text((8, 80), state.status or "sending…", font=body, fill=AMBER)

    elif state.screen is Screen.SETTINGS:
        d.text((8, 28), "settings", font=title, fill=FG)
        d.text((8, 56), state.username or "not signed in", font=body, fill=FG if state.username else AMBER)
        d.text((8, 80), state.project or "no project", font=small, fill=MUTED)
        d.text((8, 110), "setup", font=small, fill=MUTED)
        d.text((8, 128), hud.setup_url, font=small, fill=GREEN)
        d.text((8, 160), hud.wifi or "wifi off", font=small, fill=MUTED)
        d.text((8, 200), "KEY3 back", font=small, fill=MUTED)

    elif state.screen is Screen.PLAYBACK:
        d.text((8, 28), "play", font=title, fill=FG)
        if preview is not None:
            img.paste(preview.resize((240, 180)), (0, 50))
        d.text((8, 216), "KEY3 back", font=small, fill=MUTED)

    elif state.screen is Screen.SHUTDOWN:
        d.text((40, 100), "shutting down", font=title, fill=AMBER)

    return img


class MockDisplay:
    def __init__(self):
        self.last: Image.Image | None = None

    def show(self, image: Image.Image) -> None:
        self.last = image

    def close(self) -> None:
        return None


class St7789Display:
    """Waveshare 1.3 inch HAT via luma.lcd (ST7789 on SPI0 CE0)."""

    def __init__(self, cfg):
        from gpiozero import DigitalOutputDevice
        from luma.core.interface.serial import spi
        from luma.lcd.device import st7789

        self._bl = DigitalOutputDevice(cfg.gpio_bl)
        self._bl.on()
        serial = spi(
            port=cfg.spi_port,
            device=cfg.spi_device,
            gpio_DC=cfg.gpio_dc,
            gpio_RST=cfg.gpio_rst,
        )
        self._device = st7789(serial, width=cfg.width, height=cfg.height, rotate=cfg.rotate)

    def show(self, image: Image.Image) -> None:
        self._device.display(image.convert("RGB"))

    def close(self) -> None:
        self._bl.off()
