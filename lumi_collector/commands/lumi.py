"""``solarian lumi --stream`` — play the collector's live camera; ``solarian lumi pack`` — make a .lumi.zip."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from solarian.cli import BaseCommand, CommandRegistry

from lumi_collector.stream import DEFAULT_STREAM_HOST, DEFAULT_STREAM_PORT, ffplay_argv


@CommandRegistry.register("lumi")
class LumiCommand(BaseCommand):
    help = "Play the LUMI collector's live camera stream, or pack a dataset into a .lumi.zip."
    category = "Utilities"
    uses_config = False
    description = """
    pack zips a collector dataset folder (catalog.json + raw_videos/ with the
    MP4s and their .imu.json sidecars, and camera_calibration.json if present)
    into <name>.lumi.zip. Deleted episodes are left out. Solarian Base uploads
    and solarian umi-slam both take this file.

    --stream: the collector must be on its Stream video screen, which listens with
    rpicam-vid. This opens that H.264 stream in ffplay so you can check the
    picture and adjust the lens.

    --policy shows the 224x224 image the policy would get instead: the frame
    remapped into GoPro Max Lens geometry with the camera's calibration, then
    cropped and masked like the UMI skill does. Use it to place the camera on
    the gripper. It needs solarian-umi-skill installed and a calibration from
    solarian umi-calibrate-lumi; run it from the project directory so
    conf/calibrations/cameras/<serial>.json is found.

    Requires ffmpeg (the ffplay binary) on this machine.
    """
    usage = ("--stream [--policy [--calibration PATH]] [--host HOST] [--port PORT]\n"
             "       pack <dataset_dir> [--output PATH]")
    options = {
        "pack <dataset_dir>": "Zip a collector dataset folder into a .lumi.zip.",
        "--output PATH": "With pack: the archive path (default: <dataset_dir>/../<name>.lumi.zip).",
        "--stream": "Open the live view. The collector must be on Stream video.",
        "--policy": "Show the remapped, masked 224x224 policy image. s saves a PNG, q quits.",
        "--calibration PATH": "Lumi calibration for --policy (default: the one conf/calibrations/cameras/<serial>.json).",
        "--host HOST": f"Collector hostname (default: {DEFAULT_STREAM_HOST}).",
        "--port PORT": f"Listener port (default: {DEFAULT_STREAM_PORT}).",
    }
    examples = (
        "solarian lumi --stream",
        "solarian lumi --stream --host lumi-collector.local",
        "solarian lumi --stream --policy",
        "solarian lumi --stream --policy --calibration conf/calibrations/cameras/LUMI07FC9785.json",
        "solarian lumi pack ~/lumi/datasets/cups",
    )

    def handle(self, cfg) -> None:
        if sys.argv[2:3] == ["pack"]:
            _pack(sys.argv[3:])
            return
        args = _parse_args()
        if not args.stream:
            raise SystemExit("pass --stream to open the live camera")
        if args.policy:
            from solarian.core.conf_paths import cameras_dir

            from lumi_collector import policy_view

            calibration = args.calibration or policy_view.find_calibration(cameras_dir())
            policy_view.run(args.host, args.port, calibration)
            return
        if shutil.which("ffplay") is None:
            raise SystemExit("ffplay not found; install ffmpeg")
        os.execvp("ffplay", ffplay_argv(args.host, args.port))


def _pack(argv: list[str]) -> None:
    from lumi_collector import archive
    from lumi_collector.datasets import load_dataset

    parser = argparse.ArgumentParser(prog="solarian lumi pack")
    parser.add_argument("dataset_dir", type=Path)
    parser.add_argument("--output", "-o", type=Path, default=None)
    args = parser.parse_args(argv)
    root = args.dataset_dir.expanduser().resolve()
    if not (root / "catalog.json").is_file():
        raise SystemExit(f"{root} is not a collector dataset: no catalog.json")
    dataset = load_dataset(root, camera_serial="")
    dest = archive.pack(dataset, args.output.expanduser() if args.output else archive.default_path(dataset))
    print(dest)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="solarian lumi", add_help=False)
    parser.add_argument("--stream", action="store_true")
    parser.add_argument("--policy", action="store_true")
    parser.add_argument("--calibration", type=Path, default=None)
    parser.add_argument("--host", default=DEFAULT_STREAM_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_STREAM_PORT)
    args, _ = parser.parse_known_args(sys.argv[2:])
    return args
