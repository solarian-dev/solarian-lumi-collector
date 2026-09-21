"""Remux H.264 + silent AAC + GPMF into a GoPro-shaped MP4.

UMI ``00_process_videos.py`` reads ``QuickTime:CameraSerialNumber`` via
exiftool and ``creation_time`` + ``timecode`` via PyAV.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from lumi_collector.gpmf import ImuSample, build_payloads, write_gpmd


def timecode_from_datetime(started: datetime, fps: int) -> str:
    midnight = started.replace(hour=0, minute=0, second=0, microsecond=0)
    seconds = (started - midnight).total_seconds()
    frames = int(round((seconds % 1.0) * fps))
    whole = int(seconds)
    h, rem = divmod(whole, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}:{frames:02d}"


def creation_time_iso(started: datetime) -> str:
    return started.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def mux_gopro_mp4(
    h264_path: Path,
    out_mp4: Path,
    samples: list[ImuSample],
    *,
    serial: str,
    started: datetime,
    fps: int,
    ffmpeg: str = "ffmpeg",
    exiftool: str = "exiftool",
) -> None:
    work = out_mp4.parent
    work.mkdir(parents=True, exist_ok=True)
    gpmd_path = work / (out_mp4.stem + ".gpmd")
    write_gpmd(gpmd_path, build_payloads(samples, serial=serial))

    tc = timecode_from_datetime(started, fps)
    created = creation_time_iso(started)
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-fflags",
        "+genpts",
        "-r",
        str(fps),
        "-i",
        str(h264_path),
        "-f",
        "lavfi",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-f",
        "data",
        "-i",
        str(gpmd_path),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-map",
        "2:0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-c:2",
        "copy",
        "-tag:2",
        "gpmd",
        "-shortest",
        "-metadata:s:v:0",
        f"creation_time={created}",
        "-metadata:s:v:0",
        f"timecode={tc}",
        "-metadata:s:2",
        "handler_name=GoPro MET",
        "-timecode",
        tc,
        "-movflags",
        "+faststart",
        str(out_mp4),
    ]
    subprocess.run(cmd, check=True)
    gpmd_path.unlink(missing_ok=True)
    _stamp_serial(out_mp4, serial, exiftool)


def _stamp_serial(mp4: Path, serial: str, exiftool: str) -> None:
    if shutil.which(exiftool) is None:
        return
    subprocess.run(
        [
            exiftool,
            "-overwrite_original",
            f"-QuickTime:CameraSerialNumber={serial}",
            str(mp4),
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
