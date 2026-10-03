import subprocess
from datetime import datetime, timezone
from pathlib import Path

from lumi_collector.mux import (
    creation_time_iso,
    mux_gopro_mp4,
    slot_timestamps,
    timecode_from_datetime,
    write_gpmd_mov,
)


def test_timecode_and_creation_time():
    started = datetime(2026, 9, 21, 15, 4, 5, 200000, tzinfo=timezone.utc)
    assert timecode_from_datetime(started, 30) == "15:04:05:06"
    assert creation_time_iso(started) == "2026-09-21T15:04:05.200000Z"


def test_mux_copies_cached_silence(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        Path(cmd[-1]).write_bytes(b"x")

        class Proc:
            returncode = 0
            stderr = ""

        return Proc()

    monkeypatch.setattr(subprocess, "run", fake_run)
    h264 = tmp_path / "clip.h264"
    h264.write_bytes(b"\x00")
    silence = tmp_path / "silence.m4a"
    out = tmp_path / "raw" / "GX010001.MP4"
    mux_gopro_mp4(
        [h264],
        out,
        [],
        serial="LUMI1",
        started=datetime(2026, 9, 21, 15, 4, 5, tzinfo=timezone.utc),
        fps=30,
        silence_aac=silence,
    )
    assert calls[0][-1] == str(silence)
    assert "anullsrc=channel_layout=stereo:sample_rate=48000" in calls[0]
    # The seal process already runs at idle priority; no exiftool pass after the mux.
    assert len(calls) == 2
    mux = calls[1]
    assert mux[0] == "ffmpeg"
    assert mux[mux.index("-i") - 2 : mux.index("-i") + 2] == ["-f", "h264", "-i", str(h264)]
    assert mux[mux.index("-bsf:v") + 1] == "setts=pts=(N)*40000:dts=(N)*40000:duration=40000"
    loop_at = mux.index("-stream_loop")
    assert mux[loop_at + 1 : loop_at + 4] == ["-1", "-i", str(silence)]
    assert mux[mux.index("-c:a") + 1] == "copy"
    assert "+faststart" not in mux
    assert "-movflags" not in mux
    joined = " ".join(mux)
    assert "anullsrc" not in joined
    assert "-c:a aac" not in joined


def test_slot_timestamps_shift_packets_after_each_gap():
    assert slot_timestamps(None, 40000) == "(N)*40000"
    assert slot_timestamps([0, 1, 2, 6, 7, 9], 40000) == "(N+gte(N\\,3)*3+gte(N\\,5)*1)*40000"


def test_gpmd_mov_has_one_sample_per_payload(tmp_path: Path):
    payloads = [b"DEVC" + bytes(12), b"DEVC" + bytes(4)]
    path = tmp_path / "g.mov"
    write_gpmd_mov(path, payloads)
    data = path.read_bytes()
    off = int.from_bytes(data[data.find(b"stco") + 12 : data.find(b"stco") + 16], "big")
    assert data[off : off + sum(map(len, payloads))] == b"".join(payloads)
    mdhd = data.find(b"mdhd")
    assert int.from_bytes(data[mdhd + 20 : mdhd + 24], "big") == len(payloads)
