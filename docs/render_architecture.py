"""Render docs/architecture.png — two-panel hardware + software poster."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1600, 900
OUT = Path(__file__).with_name("architecture.png")

WHITE = (255, 255, 255)
INK = (24, 28, 34)
MUTED = (90, 98, 108)
LINE = (180, 186, 194)
BLUE = (36, 99, 180)
GREEN_PCB = (46, 140, 74)
DARK_PCB = (28, 92, 52)
LCD = (18, 20, 24)
YELLOW = (255, 236, 170)
YELLOW_EDGE = (210, 180, 90)
CLOUD = (70, 130, 200)
RED = (210, 48, 52)
WIRE = {
    "csi": (40, 40, 44),
    "spi": (40, 120, 200),
    "5v": (200, 40, 40),
    "3v3": (40, 90, 200),
    "gnd": (30, 30, 30),
    "sda": (40, 160, 70),
    "scl": (230, 170, 30),
}


def font(size: int, bold: bool = False):
    candidates = (
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    )
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def rounded(draw, box, fill, radius=10, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def label(draw, xy, text, size=13, fill=INK, bold=False, anchor="lt"):
    draw.text(xy, text, font=font(size, bold=bold), fill=fill, anchor=anchor)


def pcb(draw, box, holes=True, fill=GREEN_PCB):
    rounded(draw, box, fill=fill, radius=8, outline=DARK_PCB, width=2)
    if not holes:
        return
    x0, y0, x1, y1 = box
    r = 3
    for x, y in ((x0 + 10, y0 + 10), (x1 - 10, y0 + 10), (x0 + 10, y1 - 10), (x1 - 10, y1 - 10)):
        draw.ellipse((x - r, y - r, x + r, y + r), outline=(220, 220, 220), width=1)


def header_pins(draw, x, y, cols, rows=2):
    for r in range(rows):
        for c in range(cols):
            px = x + c * 6
            py = y + r * 6
            draw.rectangle((px, py, px + 3, py + 3), fill=(200, 180, 60))


def wire(draw, a, b, color, width=3):
    draw.line([a, b], fill=color, width=width)


def box_node(draw, xywh, title):
    x, y, w, h = xywh
    rounded(draw, (x, y, x + w, y + h), YELLOW, radius=8, outline=YELLOW_EDGE, width=1)
    label(draw, (x + w / 2, y + h / 2), title, size=13, bold=True, anchor="mm")


def arrow(draw, a, b):
    draw.line([a, b], fill=MUTED, width=2)
    bx, by = b
    ax, ay = a
    if abs(bx - ax) >= abs(by - ay):
        s = 1 if bx >= ax else -1
        draw.polygon([(bx, by), (bx - 7 * s, by - 4), (bx - 7 * s, by + 4)], fill=MUTED)
    else:
        s = 1 if by >= ay else -1
        draw.polygon([(bx, by), (bx - 4, by - 7 * s), (bx + 4, by - 7 * s)], fill=MUTED)


def main() -> None:
    img = Image.new("RGB", (W, H), WHITE)
    d = ImageDraw.Draw(img)

    label(d, (W / 2, 28), "LUMI Collector", size=28, bold=True, anchor="mt")
    label(
        d,
        (W / 2, 58),
        "Off-the-shelf wrist recorder  ·  H.264 + silent AAC + GPMF IMU  ·  PrimaryKey / UMI SLAM",
        size=14,
        fill=MUTED,
        anchor="mt",
    )
    d.line((40, 78, W - 40, 78), fill=LINE, width=1)
    d.line((W * 0.5, 88, W * 0.5, H - 28), fill=LINE, width=1)

    label(d, (40, 92), "1. Hardware / wiring", size=18, bold=True)
    label(d, (W * 0.5 + 24, 92), "2. Software on the Pi", size=18, bold=True)

    # --- camera ---
    pcb(d, (70, 130, 210, 250))
    d.ellipse((112, 148, 168, 204), fill=(20, 20, 24), outline=INK, width=2)
    d.ellipse((128, 164, 152, 188), fill=(80, 80, 90))
    label(d, (140, 258), "IMX477 HQ", size=13, bold=True, anchor="mt")
    label(d, (70, 118), "Arducam + CS fisheye", size=12, fill=MUTED)

    # --- LCD HAT ---
    rounded(d, (300, 128, 500, 268), (230, 234, 240), radius=8, outline=LINE)
    rounded(d, (330, 142, 430, 242), LCD, radius=6)
    d.ellipse((352, 158, 366, 172), fill=RED)
    label(d, (380, 178), "REC", size=12, fill=RED, bold=True, anchor="mm")
    label(d, (380, 200), "00:02:13", size=14, fill=(230, 232, 236), bold=True, anchor="mm")
    for i, name in enumerate(("KEY1 REC", "KEY2 DEL", "KEY3 PWR")):
        rounded(d, (444, 150 + i * 28, 490, 170 + i * 28), (50, 54, 60), radius=4)
        label(d, (496, 160 + i * 28), name, size=11, fill=MUTED, anchor="lm")
    d.ellipse((312, 210, 328, 226), outline=INK, width=2)
    label(d, (400, 278), "Waveshare 1.44\"  128×128", size=12, fill=MUTED, anchor="mt")

    # --- Pi Zero ---
    pcb(d, (160, 360, 470, 500), fill=(52, 150, 80))
    header_pins(d, 178, 378, 20)
    rounded(d, (420, 430, 450, 458), (30, 30, 34), radius=3)
    rounded(d, (178, 470, 210, 490), (30, 30, 34), radius=3)
    label(d, (315, 448), "Pi Zero 2 W", size=16, bold=True, fill=WHITE, anchor="mm")
    label(d, (160, 508), "microSD  /home/pi/lumi/raw_videos", size=12, fill=MUTED)
    label(d, (160, 526), "Charge the UPS, not the Pi PWR jack", size=12, fill=MUTED)

    # CSI ribbon
    wire(d, (140, 250), (140, 320), WIRE["csi"], 8)
    wire(d, (140, 320), (200, 320), WIRE["csi"], 8)
    wire(d, (200, 320), (200, 360), WIRE["csi"], 8)
    label(d, (150, 312), "CSI 22-pin", size=11, fill=MUTED)

    # SPI from HAT to Pi header
    wire(d, (400, 268), (400, 330), WIRE["spi"], 3)
    wire(d, (400, 330), (280, 330), WIRE["spi"], 3)
    wire(d, (280, 330), (280, 360), WIRE["spi"], 3)
    label(d, (340, 322), "SPI + GPIO", size=11, fill=BLUE)

    # Wi-Fi
    label(d, (500, 390), "Wi-Fi", size=12, bold=True, fill=CLOUD)
    label(d, (500, 408), "PrimaryKey", size=12, fill=CLOUD)
    label(d, (500, 424), "setup :8080", size=11, fill=MUTED)

    # --- UPS ---
    pcb(d, (70, 600, 220, 720), fill=(40, 80, 140), holes=True)
    label(d, (145, 648), "UPS-Lite", size=14, bold=True, fill=WHITE, anchor="mm")
    label(d, (145, 670), "1000 mAh", size=12, fill=(210, 220, 230), anchor="mm")
    label(d, (70, 732), "I2C 0x36  ·  GPIO4 charge", size=11, fill=MUTED)
    wire(d, (180, 600), (180, 500), WIRE["5v"], 3)
    wire(d, (196, 600), (196, 500), WIRE["gnd"], 3)
    label(d, (188, 560), "5V pogo", size=11, fill=RED, anchor="mm")

    # --- GY-521 MPU-6050 ---
    pcb(d, (300, 620, 430, 720), fill=(36, 90, 160))
    label(d, (365, 658), "MPU-6050", size=14, bold=True, fill=WHITE, anchor="mm")
    label(d, (365, 678), "GY-521  200 Hz", size=12, fill=(210, 220, 230), anchor="mm")
    label(d, (300, 732), "I2C 0x68  ·  stacking header", size=11, fill=MUTED)
    wire(d, (320, 620), (220, 500), WIRE["sda"], 3)
    wire(d, (340, 620), (236, 500), WIRE["scl"], 3)
    wire(d, (360, 620), (252, 500), WIRE["3v3"], 2)
    wire(d, (380, 620), (268, 500), WIRE["gnd"], 2)
    label(d, (300, 580), "3V3  GND  SDA pin 3  SCL pin 5", size=11, fill=MUTED)

    label(
        d,
        (40, H - 36),
        "No microphone. mux.py writes silent AAC so the MP4 matches GoPro: H.264 1080p30 + gpmd ACCL/GYRO (ZXY).",
        size=12,
        fill=MUTED,
    )

    # --- software panel ---
    sx = W * 0.5 + 24
    rounded(d, (sx, 118, W - 40, 150), (230, 240, 252), radius=6)
    label(d, (sx + 12, 134), "Raspberry Pi OS Lite  64-bit  Trixie  ·  systemd  lumi-collector.service", size=13, fill=BLUE, anchor="lm")

    nodes = {
        "main": (sx + 20, 170, 150, 40),
        "app": (sx + 20, 230, 150, 40),
        "btn": (sx + 200, 170, 140, 36),
        "disp": (sx + 360, 170, 140, 36),
        "rec": (sx + 200, 230, 140, 36),
        "imu": (sx + 360, 230, 140, 36),
        "mux": (sx + 200, 300, 140, 36),
        "gpmf": (sx + 360, 300, 140, 36),
        "cat": (sx + 200, 370, 140, 36),
        "up": (sx + 200, 440, 140, 36),
        "auth": (sx + 360, 370, 140, 36),
        "setup": (sx + 360, 440, 140, 36),
    }
    titles = {
        "main": "__main__.py",
        "app": "app.py",
        "btn": "buttons.py",
        "disp": "display.py",
        "rec": "recorder.py",
        "imu": "imu.py",
        "mux": "mux.py",
        "gpmf": "gpmf.py",
        "cat": "catalog.py",
        "up": "upload.py",
        "auth": "auth.py",
        "setup": "setup_server.py",
    }
    for key, box in nodes.items():
        box_node(d, box, titles[key])

    def mid(key, edge):
        x, y, w, h = nodes[key]
        return {
            "b": (x + w / 2, y + h),
            "t": (x + w / 2, y),
            "r": (x + w, y + h / 2),
            "l": (x, y + h / 2),
        }[edge]

    arrow(d, mid("main", "b"), mid("app", "t"))
    arrow(d, mid("app", "r"), mid("rec", "l"))
    arrow(d, mid("app", "t"), (mid("app", "t")[0], mid("btn", "b")[1] + 8))
    # simpler extra arrows
    arrow(d, (nodes["app"][0] + nodes["app"][2], nodes["app"][1] + 10), mid("disp", "l"))
    arrow(d, mid("rec", "r"), mid("imu", "l"))
    arrow(d, mid("rec", "b"), mid("mux", "t"))
    arrow(d, mid("mux", "r"), mid("gpmf", "l"))
    arrow(d, mid("mux", "b"), mid("cat", "t"))
    arrow(d, mid("cat", "b"), mid("up", "t"))
    arrow(d, mid("up", "r"), mid("setup", "l"))
    arrow(d, mid("cat", "r"), mid("auth", "l"))

    rounded(d, (sx + 200, 500, sx + 500, 548), (236, 244, 255), radius=8, outline=CLOUD)
    label(d, (sx + 350, 516), "GX010001.MP4  →  PrimaryKey project", size=13, bold=True, fill=BLUE, anchor="mm")
    label(d, (sx + 350, 534), "POST /api/datasets/upload/   JWT from http://pi:8080", size=11, fill=MUTED, anchor="mm")
    arrow(d, mid("up", "b"), (sx + 350, 500))

    # file tree
    tree_x = sx + 20
    tree_y = 500
    label(d, (tree_x, tree_y), "on disk", size=12, bold=True, fill=MUTED)
    for i, line in enumerate(
        (
            "lumi_collector/",
            "config/collector.yaml",
            "deploy/setup.sh",
            "/home/pi/lumi/",
            "  raw_videos/GX01NNNN.MP4",
            "  catalog.json",
        )
    ):
        label(d, (tree_x, tree_y + 20 + i * 16), line, size=12, fill=INK)

    # UI chips
    chips = (("Home", "last clip + battery"), ("Recording", "KEY1 stop"), ("Library", "select / delete"), ("Settings", "setup URL"))
    cy = 780
    for i, (title, sub) in enumerate(chips):
        x = sx + 20 + i * 180
        rounded(d, (x, cy, x + 168, cy + 70), LCD, radius=8)
        label(d, (x + 84, cy + 24), title, size=13, bold=True, fill=WHITE, anchor="mm")
        label(d, (x + 84, cy + 48), sub, size=11, fill=(170, 176, 184), anchor="mm")

    img.save(OUT, "PNG")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
