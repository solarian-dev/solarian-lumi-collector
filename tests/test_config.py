from pathlib import Path

from lumi_collector.config import load_config


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
