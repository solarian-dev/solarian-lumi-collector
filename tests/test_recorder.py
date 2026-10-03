import json
import signal
import shutil
import subprocess
import sys
import threading
from dataclasses import replace
from datetime import datetime, timezone

import numpy as np
import pytest

from lumi_collector import seal as seal_module
from lumi_collector.app import CollectorApp
from lumi_collector.catalog import empty_catalog, load_catalog
from lumi_collector.config import CollectorConfig
from lumi_collector.display import MockDisplay
from lumi_collector.imu import open_imu
from lumi_collector.recorder import MockCamera, Recorder, cap_exposure, frame_gaps, frame_slots
from lumi_collector.seal import SealJob, write_job
from lumi_collector.state import Screen


class InProcessSeal:
    """Stands in for the seal subprocess; records the signals the recorder sends it."""

    started: list["InProcessSeal"] = []

    def __init__(self, job_path):
        self.job_path = job_path
        self.returncode = None
        self.signals = []
        InProcessSeal.started.append(self)

    def send_signal(self, sig):
        self.signals.append(sig)

    def communicate(self):
        seal_module.main([str(self.job_path)])
        self.returncode = 0
        return None, ""


@pytest.fixture(autouse=True)
def in_process_seal(monkeypatch):
    InProcessSeal.started = []
    monkeypatch.setattr("lumi_collector.recorder.start_seal_process", InProcessSeal)


def _write_mp4(_h264_paths, out_mp4, _samples, **kwargs):
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    out_mp4.write_bytes(b"mp4")


def _block_mux(monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    def blocked(h264_paths, out_mp4, samples, **kwargs):
        entered.set()
        if not release.wait(5):
            raise TimeoutError("mux was not released")
        _write_mp4(h264_paths, out_mp4, samples)

    monkeypatch.setattr("lumi_collector.seal.mux_gopro_mp4", blocked)
    return entered, release


def test_frame_slots_leave_dropped_frames_empty_without_phantoms():
    period = 1 / 30
    # A sensor running 0.05% slow for a minute never rounds into a phantom slot.
    times = [k * period * 1.0005 for k in range(1800)]
    assert frame_gaps(frame_slots(times, 30)) == []
    times = [0.0, period, 2 * period, 6 * period, 7 * period, 9 * period]
    slots = frame_slots(times, 30)
    assert slots == [0, 1, 2, 6, 7, 9]
    assert frame_gaps(slots) == [{"after_frame": 2, "missing": 3}, {"after_frame": 4, "missing": 1}]


def test_exposure_cap_limits_the_shutter_and_raises_gain_to_keep_brightness():
    def modes():
        return {
            "normal": {"shutter": [100, 10000, 30000, 60000, 66666], "gain": [1.0, 2.0, 4.0, 6.0, 16.0]},
            "short": {"shutter": [100, 5000, 10000, 20000, 33333], "gain": [1.0, 2.0, 4.0, 6.0, 8.0]},
        }

    tuning = {"algorithms": [{"rpi.awb": {}}, {"rpi.agc": {"channels": [{"exposure_modes": modes()}, {"exposure_modes": modes()}]}}]}
    capped = cap_exposure(tuning, 8000, 30)
    for channel in capped["algorithms"][1]["rpi.agc"]["channels"]:
        normal, short = channel["exposure_modes"]["normal"], channel["exposure_modes"]["short"]
        assert normal["shutter"] == [100, 8000, 8000, 8000, 8000]
        assert short["shutter"] == [100, 5000, 8000, 8000, 8000]
        # Up to the 33.3 ms frame, the longest exposure the stock mode reached at 30 fps.
        assert normal["gain"][-1] == pytest.approx(16.0 * 33333.3 / 8000, rel=1e-4)
        assert short["gain"][-1] == pytest.approx(8.0 * 33333 / 8000)


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


def test_takes_record_to_the_ram_dir_and_dropped_frames_are_reported(tmp_path, monkeypatch):
    monkeypatch.setattr("lumi_collector.seal.mux_gopro_mp4", _write_mp4)
    ram = tmp_path / "ram"
    cfg = CollectorConfig(data_dir=tmp_path / "data", ram_work_dir=ram, camera_serial="LUMI1")
    imu = open_imu(replace(cfg.imu, driver="mock"))
    origin = imu.stream_info()["clock_origin_monotonic_s"]
    period = 1 / 30

    class TimedCamera(MockCamera):
        def frame_times_s(self):
            return [origin + 0.5 + period * k for k in (0, 1, 2, 6, 7)]

    dropped = []
    catalog = empty_catalog("LUMI1")
    rec = Recorder(
        cfg, TimedCamera(cfg), imu, catalog, threading.Lock(), "LUMI1",
        on_dropped=lambda name, count: dropped.append((name, count)),
    )
    try:
        name = rec.start()
        assert rec.camera.h264_path.parent == ram
        rec.stop()
    finally:
        rec.close()
        imu.close()
    take = json.loads((cfg.raw_videos_dir / name).with_suffix(".imu.json").read_text())["1"]
    assert [f["t_s"] for f in take["frames"]] == pytest.approx([0.5 + period * k for k in (0, 1, 2, 6, 7)])
    assert take["dropped_frames"] == [{"after_frame": 2, "missing": 3}]
    assert take["imu"]["timing"] == "read"
    assert len(take["streams"]["GYRO"]["samples"]) >= 1
    assert dropped == [(name, 3)]
    assert catalog.episodes[0].dropped_frames == 3
    assert load_catalog(cfg.catalog_path, "LUMI1").episodes[0].dropped_frames == 3
    assert list(ram.iterdir()) == []


def test_a_gap_during_a_take_freezes_the_save_until_the_take_stops(tmp_path, monkeypatch):
    entered, release = _block_mux(monkeypatch)
    cfg = CollectorConfig(data_dir=tmp_path, camera_serial="LUMI1")
    imu = open_imu(replace(cfg.imu, driver="mock"))
    rec = Recorder(cfg, MockCamera(cfg), imu, empty_catalog("LUMI1"), threading.Lock(), "LUMI1")
    try:
        rec.start()
        rec.stop()
        assert entered.wait(2)
        rec.start()
        rec._on_gap()
        rec._on_gap()
        saving = InProcessSeal.started[0]
        assert saving.signals == [signal.SIGSTOP]
        rec.stop()
        assert saving.signals == [signal.SIGSTOP, signal.SIGCONT]
    finally:
        release.set()
        rec.close()
        imu.close()


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


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_seal_process_writes_an_mp4_that_keeps_dropped_frames_in_real_time(tmp_path):
    import av

    stream = tmp_path / "take.h264"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=30",
         "-frames:v", "12", "-c:v", "libx264", "-bsf:v", "h264_mp4toannexb", "-f", "h264", str(stream)],
        check=True,
    )
    data = stream.read_bytes()
    # A take that moved to the SD card mid-take: two byte-consecutive pieces.
    parts = [tmp_path / "take.h264.0", tmp_path / "take.h264.1"]
    parts[0].write_bytes(data[: len(data) // 2])
    parts[1].write_bytes(data[len(data) // 2 :])
    slots = frame_slots([k / 30 for k in [*range(5), *range(8, 15)]], 30)
    samples = np.array([[k / 200, 0, 9.8, 0, 0, 0, 0, 36.0] for k in range(100)])
    job = SealJob(
        filename="GX010001.MP4",
        started=datetime(2026, 10, 2, 15, 0, tzinfo=timezone.utc),
        h264_paths=parts,
        out_mp4=tmp_path / "raw" / "GX010001.MP4",
        serial="LUMI1",
        fps=30,
        silence_aac=tmp_path / "silence.m4a",
        samples=samples,
        frame_times_s=np.array([s / 30 for s in slots]),
        slots=slots,
        dropped_frames=frame_gaps(slots),
        imu_info={"timing": "fifo"},
        duration_s=0.5,
    )
    job_path = tmp_path / "take.job"
    write_job(job, job_path)
    subprocess.run([sys.executable, "-m", "lumi_collector.seal", str(job_path)], check=True)

    with av.open(str(job.out_mp4)) as container:
        video = container.streams.video[0]
        pts = [float(p.pts * video.time_base) for p in container.demux(video) if p.size]
    assert sorted(round(t * 30) for t in pts) == slots
    take = json.loads(job.out_mp4.with_suffix(".imu.json").read_text())["1"]
    assert take["dropped_frames"] == [{"after_frame": 4, "missing": 3}]
    assert len(take["frames"]) == 12
