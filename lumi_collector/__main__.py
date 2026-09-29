from __future__ import annotations

import argparse
from pathlib import Path

from lumi_collector.config import load_config
from lumi_collector.display import MockDisplay, St7735Display, loading_frame
from lumi_collector.splash import PANEL_READY


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lumi-collector")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("/home/pi/lumi/collector.yaml"),
    )
    parser.add_argument("--mock", action="store_true", help="No GPIO / camera / I2C")
    args = parser.parse_args(argv)
    cfg_path = args.config if args.config.is_file() else None
    cfg = load_config(cfg_path)
    # Splash already drew the loading frame. Resetting here would blank it.
    size = (cfg.display.width, cfg.display.height)
    if args.mock:
        display = MockDisplay()
    else:
        display = St7735Display(cfg.display, reset=not PANEL_READY.is_file())
    display.show(loading_frame(size=size))
    from lumi_collector.app import CollectorApp

    CollectorApp(cfg, display, mock=args.mock).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
