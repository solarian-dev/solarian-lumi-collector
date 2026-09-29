import threading
from dataclasses import replace

from lumi_collector.app import CollectorApp
from lumi_collector.catalog import empty_catalog, load_catalog
from lumi_collector.config import CollectorConfig
from lumi_collector.display import MockDisplay
from lumi_collector.imu import open_imu
from lumi_collector.recorder import MockCamera, Recorder
from lumi_collector.state import Screen


def _block_mux(monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    def blocked(_h264_path, out_mp4, _samples, **kwargs):
        entered.set()
        if not release.wait(5):
            raise TimeoutError("mux was not released")
        out_mp4.parent.mkdir(parents=True, exist_ok=True)
        out_mp4.write_bytes(b"mp4")

    monkeypatch.setattr("lumi_collector.recorder.mux_gopro_mp4", blocked)
    return entered, release


def test_stop_returns_while_mux_blocks(tmp_path, monkeypatch):
    entered, release = _block_mux(monkeypatch)
    cfg = CollectorConfig(data_dir=tmp_path, camera_serial="LUMI1")
    camera = MockCamera(cfg)
    imu = open_imu(replace(cfg.imu, driver="mock"))
    catalog = empty_catalog("LUMI1")
    rec = Recorder(cfg, camera, imu, catalog, threading.Lock(), "LUMI1")
    stopped = threading.Thread(target=rec.stop)
    started_stop = False
    try:
        first = rec.start()
        saved = load_catalog(cfg.catalog_path, "LUMI1")
        assert saved.next_index == 2
        assert saved.episodes == []
        stopped.start()
        started_stop = True
        assert entered.wait(2)
        stopped.join(0.5)
        assert not stopped.is_alive()
        assert rec.recording is False
        assert camera.closed is False
        second = rec.start()
        assert first == "GX010001.MP4"
        assert second == "GX010002.MP4"
    finally:
        release.set()
        if started_stop:
            stopped.join(2)
        rec.close()
        imu.close()
    assert camera.closed is True
    assert [e.filename for e in catalog.episodes] == ["GX010001.MP4", "GX010002.MP4"]


def test_key1_starts_next_take_while_seal_runs(tmp_path, monkeypatch):
    entered, release = _block_mux(monkeypatch)
    cfg = CollectorConfig(data_dir=tmp_path, camera_serial="LUMI1")
    app = CollectorApp(cfg, MockDisplay(), mock=True)
    stop_press = threading.Thread(target=lambda: app.tick({"key1": True}))
    started_stop = False
    try:
        app.tick({"key1": True})
        assert app.state.screen is Screen.RECORDING
        app.tick({"key1": False})
        stop_press.start()
        started_stop = True
        assert entered.wait(2)
        stop_press.join(0.5)
        assert not stop_press.is_alive()
        assert app.state.screen is Screen.HOME
        assert app.recorder.camera.closed is False
        app.tick({"key1": False})
        app.tick({"key1": True})
        assert app.state.screen is Screen.RECORDING
        assert app.recorder.recording
        stems = sorted(p.stem for p in cfg.work_dir.glob("*.h264"))
        assert stems == ["GX010001", "GX010002"]
    finally:
        release.set()
        if started_stop:
            stop_press.join(2)
        app.recorder.close()
        app.imu.close()
