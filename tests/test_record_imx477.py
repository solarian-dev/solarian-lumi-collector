import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "tools" / "pi_camera_recorder" / "record_imx477.py"
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
        rec.parse_args(["--max-exposure-us", "30000"])
    with pytest.raises(SystemExit):
        rec.parse_args(["--fps", "60"])
    args = rec.parse_args([])
    assert args.fps == 40.0 and args.max_exposure_us == 4000


def test_args_codec_defaults():
    assert rec.parse_args([]).codec == "mjpeg"
    assert rec.parse_args([]).bitrate == 50e6
    h264 = rec.parse_args(["--codec", "h264"])
    assert h264.bitrate == 15e6
    assert rec.CODECS["h264"]["size"] == (1440, 1080)
    assert rec.parse_args(["--codec", "h264", "--bitrate", "20e6"]).bitrate == 20e6


def test_args_reject_h264_over_encoder_limits():
    with pytest.raises(SystemExit):
        rec.parse_args(["--codec", "h264", "--bitrate", "40e6"])
    with pytest.raises(SystemExit):
        rec.parse_args(["--codec", "h264", "--iperiod", "0"])
