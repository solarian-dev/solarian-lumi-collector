"""``solarian lumi --stream`` — play the collector's live camera."""

from __future__ import annotations

import argparse
import os
import shutil
import sys

from solarian.cli import BaseCommand, CommandRegistry

from lumi_collector.stream import DEFAULT_STREAM_HOST, DEFAULT_STREAM_PORT, ffplay_argv


@CommandRegistry.register("lumi")
class LumiCommand(BaseCommand):
    help = "Play the LUMI collector's live camera stream."
    category = "Utilities"
    uses_config = False
    description = """
    The collector must be on its Stream video screen, which listens with
    rpicam-vid. This opens that H.264 stream in ffplay so you can check the
    picture and adjust the lens.

    Requires ffmpeg (the ffplay binary) on this machine.
    """
    usage = "--stream [--host HOST] [--port PORT]"
    options = {
        "--stream": "Open the live view. The collector must be on Stream video.",
        "--host HOST": f"Collector hostname (default: {DEFAULT_STREAM_HOST}).",
        "--port PORT": f"Listener port (default: {DEFAULT_STREAM_PORT}).",
    }
    examples = (
        "solarian lumi --stream",
        "solarian lumi --stream --host lumi-collector.local",
    )

    def handle(self, cfg) -> None:
        args = _parse_args()
        if not args.stream:
            raise SystemExit("pass --stream to open the live camera")
        if shutil.which("ffplay") is None:
            raise SystemExit("ffplay not found; install ffmpeg")
        os.execvp("ffplay", ffplay_argv(args.host, args.port))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="solarian lumi", add_help=False)
    parser.add_argument("--stream", action="store_true")
    parser.add_argument("--host", default=DEFAULT_STREAM_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_STREAM_PORT)
    args, _ = parser.parse_known_args(sys.argv[2:])
    return args
