from datetime import datetime, timezone
from pathlib import Path

from lumi_collector.mux import creation_time_iso, timecode_from_datetime, write_gpmd_mov


def test_timecode_and_creation_time():
    started = datetime(2026, 9, 21, 15, 4, 5, 200000, tzinfo=timezone.utc)
    assert timecode_from_datetime(started, 30) == "15:04:05:06"
    assert creation_time_iso(started) == "2026-09-21T15:04:05.200000Z"


def test_gpmd_mov_has_one_sample_per_payload(tmp_path: Path):
    payloads = [b"DEVC" + bytes(12), b"DEVC" + bytes(4)]
    path = tmp_path / "g.mov"
    write_gpmd_mov(path, payloads)
    data = path.read_bytes()
    off = int.from_bytes(data[data.find(b"stco") + 12 : data.find(b"stco") + 16], "big")
    assert data[off : off + sum(map(len, payloads))] == b"".join(payloads)
    mdhd = data.find(b"mdhd")
    assert int.from_bytes(data[mdhd + 20 : mdhd + 24], "big") == len(payloads)
