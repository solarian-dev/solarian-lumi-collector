from pathlib import Path

import pytest

from lumi_collector.config import load_config

REPO_CONFIG = Path(__file__).resolve().parents[1] / "config" / "collector.yaml"


def test_yaml_roundtrip(tmp_path: Path):
    path = tmp_path / "c.yaml"
    path.write_text("data_dir: /tmp/lumi\nimu:\n  driver: mock\n  rate_hz: 100\n")
    cfg = load_config(path)
    assert cfg.data_dir == Path("/tmp/lumi")
    assert cfg.imu.driver == "mock"
    assert cfg.imu.rate_hz == 100
    assert cfg.video.width == 1440
    assert cfg.video.height == 1080
    assert cfg.video.fps == 30
    assert cfg.video.sensor_width == 2028
    assert cfg.video.sensor_height == 1520
    assert cfg.video.stream_port == 8888


def test_capture_defaults_match_the_shipped_config():
    defaults = load_config(None).video
    shipped = load_config(REPO_CONFIG).video
    for video in (defaults, shipped):
        assert video.bitrate == 15_000_000
        assert video.max_exposure_us == 8000
        assert video.max_gain == 16.0
        assert video.sharpness == 3.0
        assert video.denoise == "off"


def test_capture_settings_from_yaml(tmp_path: Path):
    path = tmp_path / "c.yaml"
    path.write_text("video:\n  max_exposure_us: 0\n  max_gain: 8\n  sharpness: 1\n  denoise: fast\n")
    video = load_config(path).video
    assert (video.max_exposure_us, video.max_gain, video.sharpness, video.denoise) == (0, 8.0, 1.0, "fast")
    path.write_text("video:\n  denoise: high_quality\n")
    with pytest.raises(ValueError, match="video.denoise"):
        load_config(path)
