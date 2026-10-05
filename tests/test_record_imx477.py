import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "tools" / "record_imx477.py"
_spec = importlib.util.spec_from_file_location("record_imx477", _PATH)
rec = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rec)


def _flat_tuning():
    return {"algorithms": [
        {"rpi.black_level": {}},
        {"rpi.agc": {"exposure_modes": {
            "normal": {"shutter": [100, 10000, 30000, 60000, 66666], "gain": [1.0, 2.0, 4.0, 6.0, 8.0]},
            "short": {"shutter": [100, 5000, 10000, 20000, 33333], "gain": [1.0, 2.0, 4.0, 6.0, 8.0]},
        }}},
    ]}


def test_cap_rewrites_flat_layout():
    tuning = _flat_tuning()
    assert rec.cap_agc_exposure(tuning, 4000, 8.0) == 2
    for mode in tuning["algorithms"][1]["rpi.agc"]["exposure_modes"].values():
        assert max(mode["shutter"]) == 4000
        assert mode["gain"] == [1.0, 1.0, 8.0]
        assert len(mode["shutter"]) == len(mode["gain"])


def test_cap_rewrites_every_channel():
    modes = {"normal": {"shutter": [100, 66666], "gain": [1.0, 8.0]}}
    tuning = {"algorithms": [{"rpi.agc": {"channels": [
        {"exposure_modes": {k: dict(v) for k, v in modes.items()}},
        {"exposure_modes": {k: dict(v) for k, v in modes.items()}},
    ]}}]}
    assert rec.cap_agc_exposure(tuning, 2500, 12.0) == 2
    for ch in tuning["algorithms"][0]["rpi.agc"]["channels"]:
        assert ch["exposure_modes"]["normal"]["shutter"] == [100, 2500, 2500]


def test_cap_rejects_missing_agc_and_bad_limits():
    with pytest.raises(ValueError):
        rec.cap_agc_exposure({"algorithms": []}, 4000, 8.0)
    with pytest.raises(ValueError):
        rec.cap_agc_exposure(_flat_tuning(), 50, 8.0)
    with pytest.raises(ValueError):
        rec.cap_agc_exposure(_flat_tuning(), 4000, 0.5)


def test_frame_stats_counts_drops():
    period = 25_000_000
    ts = [i * period for i in range(10)]
    del ts[5]
    del ts[5]
    stats = rec.frame_stats(ts, 40.0)
    assert stats["frames"] == 8
    assert stats["dropped_frames"] == 2
    assert stats["max_gap_ms"] == pytest.approx(75.0)


def test_frame_stats_clean_run():
    ts = [i * 25_000_000 for i in range(41)]
    stats = rec.frame_stats(ts, 40.0)
    assert stats["dropped_frames"] == 0
    assert stats["measured_fps"] == pytest.approx(40.0)


def test_args_reject_exposure_longer_than_frame():
    with pytest.raises(SystemExit):
        rec.parse_args(["--max-exposure-us", "40000"])
    with pytest.raises(SystemExit):
        rec.parse_args(["--fps", "40", "--max-exposure-us", "30000"])
    with pytest.raises(SystemExit):
        rec.parse_args(["--fps", "60"])
    args = rec.parse_args([])
    assert args.fps == 30.0 and args.max_exposure_us == 4000 and args.iperiod == 30


def test_args_codec_defaults():
    assert rec.parse_args([]).codec == "mjpeg"
    assert rec.parse_args([]).bitrate == 50e6
    h264 = rec.parse_args(["--codec", "h264"])
    assert h264.bitrate == 15e6
    assert rec.CODECS["h264"]["size"] == rec.CODECS["h264"]["stream_size"] == (1440, 1080)
    assert rec.CODECS["mjpeg"]["size"] == (2028, 1520)
    assert rec.CODECS["mjpeg"]["stream_size"] == (1920, 1520)
    assert rec.parse_args(["--codec", "h264", "--bitrate", "20e6"]).bitrate == 20e6


def test_args_label_and_refs():
    args = rec.parse_args(["--label", "h264_tool", "--refs", "0"])
    assert args.label == "h264_tool" and args.refs == 0
    with pytest.raises(SystemExit):
        rec.parse_args(["--refs", "-1"])


def test_frame_rows_keep_only_written_frames():
    first_us = 18_774_276_888
    period_us = 33_333
    fields = (3994, 8.0, 1.0, 118.6, 3473, 2932)
    # The encoder saw five frames but only frames 0, 1 and 3 reached the file.
    metadata = {first_us + i * period_us: ((first_us + i * period_us) * 1000 + 123, fields) for i in range(5)}
    written = [(0, 9000, True), (period_us, 4000, False), (3 * period_us, 4100, False)]
    rows = rec.frame_rows(written, first_us, metadata)
    assert [r[0] for r in rows] == [0, 1, 2]
    assert [r[1] for r in rows] == [(first_us + k * period_us) * 1000 + 123 for k in (0, 1, 3)]
    assert [r[2:4] for r in rows] == [[9000, 1], [4000, 0], [4100, 0]]
    assert rows[2][4:] == list(fields)
    assert len(rows[0]) == len(rec.CSV_HEADER)


def test_frame_rows_without_metadata_keep_the_timestamp():
    rows = rec.frame_rows([(500, 10, True)], 1_000, {})
    assert rows[0][:4] == [0, 1_500_000, 10, 1]
    assert rows[0][4:] == [None] * len(rec.METADATA_FIELDS)


def test_ref_schedule_spreads_over_the_take():
    assert rec.ref_schedule(4, 20.0) == [2.5, 7.5, 12.5, 17.5]
    assert rec.ref_schedule(2, 0.0) == [2.5, 5.0]
    assert rec.ref_schedule(0, 20.0) == []


def test_args_reject_h264_over_encoder_limits():
    with pytest.raises(SystemExit):
        rec.parse_args(["--codec", "h264", "--bitrate", "40e6"])
    with pytest.raises(SystemExit):
        rec.parse_args(["--codec", "h264", "--iperiod", "0"])
