from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from lumi_collector.catalog import Catalog, add_episode, next_gopro_name
from lumi_collector.config import CollectorConfig
from lumi_collector.gpmf import ImuSample, telemetry_json
from lumi_collector.imu import Imu
from lumi_collector.mux import mux_gopro_mp4


class Camera(Protocol):
    def start(self, h264_path: Path) -> None: ...
    def stop(self) -> None: ...
    def preview_rgb(self): ...


class MockCamera:
    def __init__(self, cfg: CollectorConfig):
        self.cfg = cfg
        self.h264_path: Path | None = None

    def start(self, h264_path: Path) -> None:
        self.h264_path = h264_path
        h264_path.parent.mkdir(parents=True, exist_ok=True)
        h264_path.write_bytes(b"")

    def stop(self) -> None:
        return None

    def preview_rgb(self):
        return None


class Picamera2Camera:
    def __init__(self, cfg: CollectorConfig):
        self.cfg = cfg
        self._picam = None
        self._encoder = None

    def start(self, h264_path: Path) -> None:
        from picamera2 import Picamera2
        from picamera2.encoders import H264Encoder
        from picamera2.outputs import FileOutput

        h264_path.parent.mkdir(parents=True, exist_ok=True)
        video = self.cfg.video
        tuning = Path(video.tuning_file)
        if tuning.is_file():
            picam = Picamera2(tuning=Picamera2.load_tuning_file(str(tuning)))
        else:
            picam = Picamera2()
        # Read the whole 2028x1520 mode and scale it. A centered 4:3 crop of
        # that mode is the full frame, so the fisheye circle stays in view.
        config = picam.create_video_configuration(
            main={"size": (video.width, video.height), "format": "YUV420"},
            lores={"size": video.lores_size, "format": "YUV420"},
            sensor={
                "output_size": (video.sensor_width, video.sensor_height),
                "bit_depth": video.sensor_bit_depth,
            },
            controls={"FrameRate": video.fps},
        )
        picam.configure(config)
        encoder = H264Encoder(bitrate=video.bitrate, framerate=video.fps)
        picam.start_recording(encoder, FileOutput(str(h264_path)))
        self._picam = picam
        self._encoder = encoder

    def stop(self) -> None:
        picam = self._picam
        if picam is None:
            return
        picam.stop_recording()
        picam.close()
        self._picam = None
        self._encoder = None

    def preview_rgb(self):
        picam = self._picam
        if picam is None:
            return None
        return picam.capture_array("lores")


@dataclass
class LiveRecording:
    filename: str
    started: datetime
    h264_path: Path
    samples: list[ImuSample]


class Recorder:
    def __init__(self, cfg: CollectorConfig, camera: Camera, imu: Imu):
        self.cfg = cfg
        self.camera = camera
        self.imu = imu
        self._lock = threading.Lock()
        self._live: LiveRecording | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def recording(self) -> bool:
        return self._live is not None

    def elapsed_s(self) -> float:
        live = self._live
        if live is None:
            return 0.0
        return (datetime.now(timezone.utc) - live.started).total_seconds()

    def start(self, catalog: Catalog) -> str:
        if self._live is not None:
            raise RuntimeError("already recording")
        name = next_gopro_name(catalog)
        work = self.cfg.work_dir
        work.mkdir(parents=True, exist_ok=True)
        h264_path = work / f"{Path(name).stem}.h264"
        started = datetime.now(timezone.utc)
        self.camera.start(h264_path)
        self._stop.clear()
        live = LiveRecording(filename=name, started=started, h264_path=h264_path, samples=[])
        self._live = live
        self._thread = threading.Thread(target=self._imu_loop, args=(live,), daemon=True)
        self._thread.start()
        return name

    def stop(self, catalog: Catalog, serial: str) -> Path:
        live = self._live
        if live is None:
            raise RuntimeError("not recording")
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self.camera.stop()
        duration = (datetime.now(timezone.utc) - live.started).total_seconds()
        out = self.cfg.raw_videos_dir / live.filename
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            mux_gopro_mp4(
                live.h264_path,
                out,
                live.samples,
                serial=serial,
                started=live.started,
                fps=self.cfg.video.fps,
            )
            sidecar = out.with_suffix(".imu.json")
            sidecar.write_text(
                json.dumps(
                    {
                        "1": {
                            "streams": {
                                "ACCL": {
                                    "samples": telemetry_json(
                                        live.samples, start=live.started, stream_name="ACCL"
                                    )
                                },
                                "GYRO": {
                                    "samples": telemetry_json(
                                        live.samples, start=live.started, stream_name="GYRO"
                                    )
                                },
                            }
                        }
                    }
                )
            )
            add_episode(
                catalog,
                filename=live.filename,
                duration_s=duration,
                size_bytes=out.stat().st_size if out.is_file() else 0,
                started_at=live.started.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            )
            live.h264_path.unlink(missing_ok=True)
        finally:
            self._live = None
            self._thread = None
        return out

    def _imu_loop(self, live: LiveRecording) -> None:
        period = 1.0 / float(self.cfg.imu.rate_hz)
        next_t = time.monotonic()
        while not self._stop.is_set():
            sample = self.imu.read()
            if sample is not None:
                with self._lock:
                    live.samples.append(sample)
            next_t += period
            delay = next_t - time.monotonic()
            if delay > 0:
                time.sleep(delay)
