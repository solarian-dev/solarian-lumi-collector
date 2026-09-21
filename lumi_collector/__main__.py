from __future__ import annotations

import argparse
from pathlib import Path

from lumi_collector.app import CollectorApp
from lumi_collector.config import load_config


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
    CollectorApp(cfg, mock=args.mock).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
