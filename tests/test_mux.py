from datetime import datetime, timezone

from lumi_collector.mux import creation_time_iso, timecode_from_datetime


def test_timecode_and_creation_time():
    started = datetime(2026, 9, 21, 15, 4, 5, 200000, tzinfo=timezone.utc)
    assert timecode_from_datetime(started, 30) == "15:04:05:06"
    assert creation_time_iso(started) == "2026-09-21T15:04:05.200000Z"
