"""Remux H.264 + silent AAC + GPMF into a GoPro-shaped MP4.

UMI ``00_process_videos.py`` reads ``QuickTime:CameraSerialNumber`` via
exiftool and ``creation_time`` + ``timecode`` via PyAV.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from lumi_collector.gpmf import ImuSample, build_payloads


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


# Raw H.264 demuxer timebase. Packets have no PTS; without stamped
# timestamps ffmpeg 7 drops the other tracks under -shortest.
_H264_TIMEBASE = 1_200_000


def _box(typ: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + typ + payload


def write_gpmd_mov(path: Path, payloads: list[bytes]) -> None:
    """One QuickTime sample per GPMF payload, 1 s apart, tagged ``gpmd``.

    ffmpeg 7 will not tag a raw data file as ``gpmd`` inside an MP4 (the
    tag is rejected unless the codec id is already bin_data). A MOV written
    here is a source ffmpeg can copy. The MP4 muxer has the same rejection,
    so the final file is QuickTime with a ``.MP4`` name, which is what a
    Hero file is.
    """
    if not payloads:
        raise ValueError("no GPMF payloads")
    n = len(payloads)
    duration_ms = n * 1000
    blob = b"".join(payloads)
    ftyp = _box(b"ftyp", b"qt  " + struct.pack(">I", 0x200) + b"qt  ")
    mdat = _box(b"mdat", blob)
    data_offset = len(ftyp) + 8

    matrix = bytes.fromhex(
        "00010000000000000000000000000000"
        "00010000000000000000000000000000"
        "40000000"
    )
    mvhd = _box(
        b"mvhd",
        struct.pack(">IIIIII", 0, 0, 0, 1000, duration_ms, 0x00010000)
        + struct.pack(">HH", 0x0100, 0)
        + b"\x00" * 8
        + matrix
        + b"\x00" * 24
        + struct.pack(">I", 2),
    )
    tkhd = _box(
        b"tkhd",
        struct.pack(">IIIIII", 2, 0, 0, 1, 0, duration_ms)
        + b"\x00" * 8
        + struct.pack(">HHHH", 0, 0, 0x0100, 0)
        + matrix
        + struct.pack(">II", 0, 0),
    )
    elst = _box(
        b"elst",
        struct.pack(">IIIiI", 0, 1, duration_ms, 0, 0x00010000),
    )
    mdhd = _box(
        b"mdhd",
        struct.pack(">IIIII", 0, 0, 0, 1, n) + struct.pack(">HH", 0x7FFF, 0),
    )
    hdlr = _box(
        b"hdlr",
        bytes.fromhex("000000006d686c726d657461")
        + b"\x00" * 12
        + b"\x09GoPro MET",
    )
    gmhd = _box(
        b"gmhd",
        _box(b"gmin", bytes.fromhex("00000000004080008000800000000000"))
        + _box(
            b"text",
            bytes.fromhex("000100000000000000000000000000000001000000000000000000000000000040000000"),
        )
        + _box(b"gpmd", b"\x00\x00\x00\x00"),
    )
    dref = _box(
        b"dref",
        struct.pack(">II", 0, 1) + _box(b"url ", struct.pack(">I", 1)),
    )
    stsd = _box(
        b"stsd",
        bytes.fromhex("00000000000000010000001467706d64000000000000000100000000"),
    )
    stts = _box(b"stts", struct.pack(">IIII", 0, 1, n, 1))
    stsc = _box(b"stsc", struct.pack(">IIIII", 0, 1, 1, n, 1))
    stsz = _box(
        b"stsz",
        struct.pack(">III", 0, 0, n) + b"".join(struct.pack(">I", len(p)) for p in payloads),
    )
    stco = _box(b"stco", struct.pack(">III", 0, 1, data_offset))
    minf = _box(
        b"minf",
        gmhd
        + _box(
            b"hdlr",
            bytes.fromhex("0000000064686c7275726c20") + b"\x00" * 12 + b"\x0bDataHandler",
        )
        + _box(b"dinf", dref)
        + _box(b"stbl", stsd + stts + stsc + stsz + stco),
    )
    trak = _box(b"trak", tkhd + _box(b"edts", elst) + _box(b"mdia", mdhd + hdlr + minf))
    moov = _box(b"moov", mvhd + trak)
    path.write_bytes(ftyp + mdat + moov)


def _ensure_silence_aac(path: Path, ffmpeg: str) -> None:
    """One cached second of AAC. Mux loops it; encoding per clip stalls the card."""
    if path.is_file() and path.stat().st_size > 0:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-t",
            "1",
            "-c:a",
            "aac",
            "-b:a",
            "64k",
            str(path),
        ],
        check=False,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc.returncode != 0 or not path.is_file():
        path.unlink(missing_ok=True)
        detail = (proc.stderr or "").strip()
        raise RuntimeError(detail or f"ffmpeg exited {proc.returncode}")


def mux_gopro_mp4(
    h264_path: Path,
    out_mp4: Path,
    samples: list[ImuSample],
    *,
    serial: str,
    started: datetime,
    fps: int,
    silence_aac: Path,
    ffmpeg: str = "ffmpeg",
    exiftool: str = "exiftool",
    payloads: list[bytes] | None = None,
) -> None:
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    _ensure_silence_aac(silence_aac, ffmpeg)
    if payloads is None:
        payloads = build_payloads(samples, serial=serial)
    mov_path = out_mp4.with_suffix(".gpmd.mov") if payloads else None
    if mov_path is not None:
        write_gpmd_mov(mov_path, payloads)

    tc = timecode_from_datetime(started, fps)
    created = creation_time_iso(started)
    tick = max(1, round(_H264_TIMEBASE / fps))
    # moov stays after mdat. UMI reads the file locally; +faststart would rewrite it.
    # nice so a seal overlapping the next take yields the CPU to the encoder.
    cmd = [
        "nice",
        "-n",
        "10",
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(h264_path),
        "-stream_loop",
        "-1",
        "-i",
        str(silence_aac),
    ]
    if mov_path is not None:
        cmd += ["-discard", "none", "-i", str(mov_path)]
    cmd += [
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
    ]
    if mov_path is not None:
        cmd += ["-map", "2:0", "-c:d", "copy", "-metadata:s:2", "handler_name=GoPro MET"]
    cmd += [
        "-c:v",
        "copy",
        "-bsf:v",
        f"setts=pts=N*{tick}:dts=N*{tick}:duration={tick}",
        "-c:a",
        "copy",
        "-shortest",
        "-metadata:s:v:0",
        f"creation_time={created}",
        "-metadata:s:v:0",
        f"timecode={tc}",
        "-timecode",
        tc,
        "-f",
        "mov",
        str(out_mp4),
    ]
    try:
        proc = subprocess.run(cmd, check=False, stderr=subprocess.PIPE, text=True)
        if proc.returncode != 0:
            out_mp4.unlink(missing_ok=True)
            detail = (proc.stderr or "").strip()
            raise RuntimeError(detail or f"ffmpeg exited {proc.returncode}")
    finally:
        if mov_path is not None:
            mov_path.unlink(missing_ok=True)
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
