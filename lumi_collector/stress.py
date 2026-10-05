"""Back-to-back takes through the collector app, reporting every dropped frame.

Presses KEY1 in software and runs the app's 50 ms tick, so the LCD redraws,
IMU, saves and catalog all load the Pi as they do in use. Stop lumi-collector
first: libcamera allows one client. Takes go to their own data dir, so the
collector's catalog and videos are untouched. Run it as the service runs,
``taskset -c 0-2 nice -n -5``, so the save keeps core 3.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from dataclasses import replace
from pathlib import Path

from lumi_collector.app import CollectorApp
from lumi_collector.config import load_config
from lumi_collector.display import MockDisplay, St7735Display
from lumi_collector.recorder import frame_gaps, frame_slots

TICK_S = 0.05


def _run_for(app: CollectorApp, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.tick({})
        time.sleep(TICK_S)


def _press_key1(app: CollectorApp) -> None:
    app.tick({"key1": True})
    app.tick({"key1": False})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=Path("/home/pi/lumi/collector.yaml"))
    parser.add_argument("--data-dir", type=Path, default=Path("/home/pi/lumi_stress"))
    parser.add_argument("--takes", type=int, default=20)
    parser.add_argument("--length", type=float, default=6.0, help="Seconds per take.")
    parser.add_argument("--gap", type=float, default=1.5, help="Seconds between takes.")
    parser.add_argument("--first-length", type=float, default=None, help="A longer first take, seconds.")
    parser.add_argument("--mock", action="store_true", help="Mock camera, IMU and display (no Pi needed).")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    cfg = load_config(args.config)
    cfg = replace(cfg, data_dir=args.data_dir)
    display = MockDisplay() if args.mock else St7735Display(cfg.display, reset=False)
    app = CollectorApp(cfg, display, mock=args.mock)
    app.power.open()
    try:
        for i in range(args.takes):
            _press_key1(app)
            _run_for(app, args.first_length if i == 0 and args.first_length else args.length)
            _press_key1(app)
            _run_for(app, args.gap)
    finally:
        app.recorder.close()
        app.imu.close()
        display.close()

    dropped_total = 0
    for episode in app.catalog.episodes:
        sidecar = (cfg.raw_videos_dir / episode.filename).with_suffix(".imu.json")
        frames = [f["t_s"] for f in json.loads(sidecar.read_text())["1"]["frames"]]
        gaps = frame_gaps(frame_slots(frames, cfg.video.fps))
        dropped = sum(g["missing"] for g in gaps)
        dropped_total += dropped
        where = ", ".join(f"{g['missing']} after frame {g['after_frame']}" for g in gaps)
        print(f"{episode.filename}: {len(frames)} frames, {dropped} dropped{': ' + where if where else ''}")
    unsaved = args.takes - len(app.catalog.episodes)
    print(f"{args.takes} takes, {unsaved} not saved, {dropped_total} frames dropped")
    return 1 if dropped_total or unsaved else 0


if __name__ == "__main__":
    raise SystemExit(main())
