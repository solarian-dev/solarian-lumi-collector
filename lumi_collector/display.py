from __future__ import annotations

import time
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

from lumi_collector.catalog import Episode
from lumi_collector.state import IMU_ROW, STREAM_ROW, Screen, UiState

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
    play_s: float = 0.0
    stream_port: int = 8888


def _font(size: int):
    try:
        return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _fmt_s(seconds: float) -> str:
    total = max(0, int(seconds))
    m, s = divmod(total, 60)
    return f"{m:02d}:{s:02d}"


def _fmt_xyz(values: tuple[float, float, float], digits: int) -> str:
    return " ".join(f"{v:+.{digits}f}" for v in values)


def _fmt_size(n: int) -> str:
    if n >= 1_000_000_000:
        return f"n/a" if n < 0 else f"{n / 1_000_000_000:.1f} GB"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f} MB"
    return f"{n / 1000:.0f} KB"


CANVAS = (240, 240)
LOADING_TEXT = "Loading Solarian LUMI..."
HOME_MENU = (
    ("Key1 / Push", "record ep"),
    ("Key2 / Down", "delete last ep"),
    ("Up", "List episodes"),
    ("Key3 / Left", "Settings"),
)
LIBRARY_MENU = (
    ("Key1", "select"),
    ("Key2", "delete"),
    ("Push", "open"),
    ("Key3 / Left", "back"),
)


def _battery_color(pct: float | None):
    if pct is None:
        return MUTED
    if pct <= 15:
        return RED
    if pct <= 30:
        return AMBER
    return GREEN


def _draw_battery(draw, x, y, pct, charging, color) -> int:
    """Body plus a terminal nub. Returns the x where the percent label starts.

    Outline is 2 px so it is still one pixel after the 240→128 resample.
    """
    body_w, body_h = 26, 14
    outline = GREEN if charging else color
    draw.rounded_rectangle((x, y, x + body_w, y + body_h), radius=2, outline=outline, width=2)
    nub_h = 6
    draw.rectangle((x + body_w + 2, y + 4, x + body_w + 5, y + 4 + nub_h), fill=outline)
    if pct is not None and pct > 0:
        inner_w = body_w - 6
        fill_w = max(2, round(inner_w * min(pct, 100) / 100.0))
        draw.rectangle((x + 3, y + 3, x + 3 + fill_w, y + body_h - 3), fill=color)
    return x + body_w + 10


def _draw_play(draw, x, y) -> None:
    # The black copy sits one pixel down so the triangle stays visible on the frame.
    for dx, dy, fill in ((1, 1, (0, 0, 0)), (0, 0, FG)):
        ox, oy = x + dx, y + dy
        draw.polygon([(ox, oy), (ox, oy + 20), (ox + 18, oy + 10)], fill=fill)


def _hint(draw, x, y, key: str, action: str, font) -> None:
    draw.text((x, y), key, font=font, fill=FG)
    draw.text((x + draw.textlength(key + " ", font=font), y), action, font=font, fill=MUTED)


def loading_frame(*, size: tuple[int, int] = CANVAS) -> Image.Image:
    """First frame after power-on. The ST7735 glass stays white until this is sent."""
    shrink = CANVAS[0] / size[0]
    img = Image.new("RGB", CANVAS, BG)
    d = ImageDraw.Draw(img)
    font = _font(max(20, round(14 * shrink)))
    max_w = CANVAS[0] - 32
    lines: list[str] = []
    current = ""
    for word in LOADING_TEXT.split():
        trial = word if not current else f"{current} {word}"
        if d.textlength(trial, font=font) <= max_w:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    gap = 8
    heights = []
    for line in lines:
        _left, top, _right, bottom = d.textbbox((0, 0), line, font=font)
        heights.append(bottom - top)
    block = sum(heights) + gap * (len(lines) - 1)
    y = (CANVAS[1] - block) // 2
    for line, height in zip(lines, heights):
        d.text((CANVAS[0] // 2, y), line, font=font, fill=FG, anchor="mt")
        y += height + gap
    return img if size == CANVAS else img.resize(size, Image.LANCZOS)


def render(
    state: UiState,
    episodes: list[Episode],
    hud: Hud,
    *,
    size: tuple[int, int] = CANVAS,
    preview=None,
) -> Image.Image:
    """Lay out on a 240x240 canvas, then resample to the panel.

    Fonts grow on smaller panels so text stays at least 11/10/9 px on the glass.
    """
    shrink = CANVAS[0] / size[0]
    img = Image.new("RGB", CANVAS, BG)
    d = ImageDraw.Draw(img)
    title = _font(max(16, round(11 * shrink)))
    body = _font(max(13, round(10 * shrink)))
    small = _font(max(11, round(9 * shrink)))

    d.rectangle((0, 0, CANVAS[0], 22), fill=CARD)
    bat_color = _battery_color(hud.battery_pct)
    text_x = _draw_battery(d, 6, 4, hud.battery_pct, hud.charging, bat_color)
    label = "--" if hud.battery_pct is None else f"{hud.battery_pct:.0f}%"
    d.text((text_x, 3), label, font=small, fill=bat_color)
    d.text((CANVAS[0] - 6, 3), f"{hud.free_gb:.1f} GB", font=small, fill=MUTED, anchor="ra")

    if state.screen is Screen.HOME:
        d.text((CANVAS[0] // 2, 28), "Solarian LUMI", font=title, fill=GREEN, anchor="mt")
        last = episodes[-1] if episodes else None
        if last is None:
            d.text((8, 54), "no episodes", font=body, fill=MUTED)
        else:
            d.text((8, 54), last.filename, font=body, fill=FG)
            d.text(
                (8, 76),
                f"{_fmt_s(last.duration_s)}  {_fmt_size(last.size_bytes)}",
                font=small,
                fill=MUTED,
            )
        if state.status:
            d.text((CANVAS[0] - 8, 76), state.status, font=small, fill=AMBER, anchor="ra")
        y = 108
        for key, action in HOME_MENU:
            _hint(d, 6, y, key, action, small)
            y += 28

    elif state.screen is Screen.RECORDING:
        d.ellipse((12, 36, 28, 52), fill=RED)
        d.text((36, 34), "REC", font=title, fill=RED)
        d.text((12, 70), _fmt_s(hud.rec_s), font=_font(36), fill=FG)
        if preview is not None:
            thumb = preview.resize((96, 96))
            img.paste(thumb, (132, 120))
        _hint(d, 8, 196, "Key1 / Push", "stop", small)
        _hint(d, 8, 216, "Key3 / Left", "back", small)

    elif state.screen is Screen.LIBRARY:
        d.text((8, 28), "episodes", font=title, fill=FG)
        if not episodes:
            d.text((8, 60), "empty", font=body, fill=MUTED)
        else:
            top = max(0, state.cursor - 2)
            for i, ep in enumerate(episodes[top : top + 5]):
                y = 48 + i * 20
                idx = top + i
                mark = "*" if ep.filename in state.selected else " "
                line = f"{mark}{ep.filename[4:8]} {_fmt_s(ep.duration_s)}"
                fill = FG if idx == state.cursor else MUTED
                if idx == state.cursor:
                    d.rectangle((4, y - 1, CANVAS[0] - 4, y + 17), fill=CARD)
                d.text((8, y), line, font=small, fill=fill, anchor="la")
                stamp = ep.started_at.replace("T", " ")
                if len(stamp) >= 16:
                    when = f"{stamp[5:10]} {stamp[11:16]}"
                    d.text((CANVAS[0] - 8, y), when, font=small, fill=fill, anchor="ra")
        y = 156
        for key, action in LIBRARY_MENU:
            _hint(d, 8, y, key, action, small)
            y += 20

    elif state.screen is Screen.EPISODE:
        ep = episodes[state.cursor] if episodes else None
        d.text((8, 28), "episode", font=title, fill=FG)
        if ep is not None:
            d.text((8, 56), ep.filename, font=body, fill=FG)
            d.text((8, 80), ep.started_at.replace("T", " ")[:19], font=small, fill=MUTED)
            d.text((8, 100), f"{_fmt_s(ep.duration_s)}   {_fmt_size(ep.size_bytes)}", font=body, fill=FG)
            d.text((8, 128), "uploaded" if ep.uploaded else "local", font=small, fill=GREEN if ep.uploaded else AMBER)
        _hint(d, 8, 164, "Push", "play", small)
        _hint(d, 8, 182, "Up", "upload", small)
        _hint(d, 8, 200, "Key2", "delete", small)
        _hint(d, 8, 218, "Key3 / Left", "back", small)

    elif state.screen is Screen.CONFIRM_DELETE:
        d.text((8, 40), "delete?", font=title, fill=RED)
        for i, name in enumerate(state.pending_delete[:6]):
            d.text((8, 72 + i * 18), name, font=small, fill=FG)
        _hint(d, 8, 198, "Key2", "delete", small)
        _hint(d, 8, 216, "Key3 / Left", "back", small)

    elif state.screen is Screen.UPLOAD:
        d.text((8, 40), "uploading", font=title, fill=FG)
        d.text((8, 80), state.status or "sending…", font=body, fill=AMBER)
        _hint(d, 8, 214, "Key3 / Left", "back", small)

    elif state.screen is Screen.SETTINGS:
        d.text((8, 28), "settings", font=title, fill=FG)
        signed = state.username or ""
        if signed and state.project:
            sign_value = f"{signed} / {state.project}"
        elif signed:
            sign_value = signed
        else:
            sign_value = hud.setup_url
        # Sign-in is the only two-line row: its URL is wider than the glass
        # once fonts are scaled up for 128px. The other rows share the line.
        rows = (
            (54, 76, "IP", hud.wifi or "no address", False),
            (80, 120, "sign in", sign_value or "not signed in", True),
            (124, 148, "IMU", "live", False),
            (152, 176, "Stream video", f"tcp :{hud.stream_port}", False),
        )
        for i, (y, bottom, label, value, stacked) in enumerate(rows):
            if i == state.settings_cursor:
                d.rectangle((4, y - 2, CANVAS[0] - 4, bottom), fill=CARD)
            label_fill = FG if i == state.settings_cursor else MUTED
            d.text((8, y), label, font=body, fill=label_fill)
            if label == "sign in" and not signed:
                value_fill = AMBER
            elif i == state.settings_cursor:
                value_fill = GREEN
            else:
                value_fill = MUTED
            if stacked:
                d.text((8, y + 22), value, font=small, fill=value_fill)
            else:
                d.text((CANVAS[0] - 8, y), value, font=small, fill=value_fill, anchor="ra")
        if state.settings_cursor == IMU_ROW:
            _hint(d, 8, 196, "Right", "live view", small)
        elif state.settings_cursor == STREAM_ROW:
            _hint(d, 8, 196, "Right", "stream", small)
        _hint(d, 8, 214, "Key3 / Left", "back", small)

    elif state.screen is Screen.STREAM:
        d.text((8, 28), "streaming", font=title, fill=FG)
        d.text((8, 64), f"tcp :{hud.stream_port}", font=body, fill=GREEN)
        if state.status:
            d.text((8, 96), state.status, font=small, fill=AMBER)
        _hint(d, 8, 214, "Key3 / Left", "back", small)

    elif state.screen is Screen.IMU:
        d.text((8, 28), "IMU", font=title, fill=FG)
        if state.imu_acc is None or state.imu_gyr is None:
            d.text((8, 64), "waiting", font=body, fill=MUTED)
        else:
            d.text((8, 54), "acc  z    x    y", font=small, fill=MUTED)
            d.text((8, 72), _fmt_xyz(state.imu_acc, 1), font=body, fill=FG)
            d.text((8, 104), "gyr  z    x    y", font=small, fill=MUTED)
            d.text((8, 122), _fmt_xyz(state.imu_gyr, 2), font=body, fill=FG)
            if state.imu_temp is not None:
                d.text((8, 158), f"{state.imu_temp:.0f} C", font=small, fill=MUTED)
        _hint(d, 8, 214, "Key3 / Left", "back", small)

    elif state.screen is Screen.PLAYBACK:
        if preview is not None:
            img.paste(preview.resize((CANVAS[0], CANVAS[1] - 22)), (0, 22))
        _draw_play(d, 12, 36)
        clock = _fmt_s(hud.play_s)
        timer_font = _font(36)
        d.text((13, 71), clock, font=timer_font, fill=(0, 0, 0))
        d.text((12, 70), clock, font=timer_font, fill=FG)
        d.rectangle((0, 208, CANVAS[0], CANVAS[1]), fill=CARD)
        _hint(d, 8, 216, "Key3 / Left", "back", small)

    elif state.screen is Screen.SHUTDOWN:
        d.text((40, 100), "shutting down", font=title, fill=AMBER)

    return img if size == CANVAS else img.resize(size, Image.LANCZOS)


class MockDisplay:
    def __init__(self):
        self.last: Image.Image | None = None

    def show(self, image: Image.Image) -> None:
        self.last = image

    def close(self) -> None:
        return None


def _wake_st7735(device) -> None:
    """Re-init after a timed reset. The glass stays white if commands follow
    SWRESET or SLPOUT inside the 120 ms the datasheet requires.
    """
    device.command(0x01)
    time.sleep(0.12)
    device.command(0x11)
    time.sleep(0.12)
    device.command(0xB1, 0x01, 0x2C, 0x2D)
    device.command(0xB2, 0x01, 0x2C, 0x2D)
    device.command(0xB3, 0x01, 0x2C, 0x2D, 0x01, 0x2C, 0x2D)
    device.command(0xB4, 0x07)
    device.command(0xC0, 0xA2, 0x02, 0x84)
    device.command(0xC1, 0xC5)
    device.command(0xC2, 0x0A, 0x00)
    device.command(0xC3, 0x8A, 0x2A)
    device.command(0xC4, 0x8A, 0xEE)
    device.command(0xC5, 0x0E)
    device.command(0x36, 0x68)  # 0x60 plus BGR
    device.command(0x20)
    device.command(0x3A, 0x06)
    device.command(0x13)
    device.command(
        0xE0,
        0x0F, 0x1A, 0x0F, 0x18, 0x2F, 0x28, 0x20, 0x22,
        0x1F, 0x1B, 0x23, 0x37, 0x00, 0x07, 0x02, 0x10,
    )
    device.command(
        0xE1,
        0x0F, 0x1B, 0x0F, 0x17, 0x33, 0x2C, 0x29, 0x2E,
        0x30, 0x30, 0x39, 0x3F, 0x00, 0x07, 0x03, 0x10,
    )
    device.show()


def _open_st7735(cfg, *, reset: bool):
    from luma.core.interface.serial import spi
    from luma.lcd.device import st7735

    # Pulsing RST blanks the glass back to white. Skip it when a splash
    # already woke the panel and the image should stay up.
    serial = spi(
        port=cfg.spi_port,
        device=cfg.spi_device,
        gpio_DC=cfg.gpio_dc,
        gpio_RST=cfg.gpio_rst if reset else None,
        reset_hold_time=0.1 if reset else 0,
        reset_release_time=0.12 if reset else 0,
    )

    class Panel(st7735):
        def command(self, cmd, *args):
            if not reset and cmd == 0x01:
                return
            super().command(cmd, *args)

    return Panel(
        serial,
        width=cfg.width,
        height=cfg.height,
        rotate=cfg.rotate,
        h_offset=1,
        v_offset=2,
        bgr=True,
        gpio_LIGHT=cfg.gpio_bl,
    )


class St7735Display:
    """Waveshare 1.44 inch LCD HAT: ST7735S, 128x128, SPI0 CE0.

    The controller RAM is 132x162. Waveshare starts the glass at the second
    column and two rows down; the panel wires its subpixels BGR.
    """

    def __init__(self, cfg, *, reset: bool = True, persist: bool = False):
        self._device = _open_st7735(cfg, reset=reset)
        if reset:
            _wake_st7735(self._device)
        self._device.persist = persist
        self.show(loading_frame(size=(cfg.width, cfg.height)))

    def show(self, image: Image.Image) -> None:
        self._device.display(image.convert("RGB"))

    def close(self) -> None:
        self._device.backlight(False)
