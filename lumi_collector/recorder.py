from __future__ import annotations

import json
import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from lumi_collector.catalog import Catalog, add_episode, next_gopro_name, save_catalog
from lumi_collector.config import CollectorConfig
from lumi_collector.gpmf import ImuSample, telemetry_json
from lumi_collector.imu import Imu
from lumi_collector.mux import mux_gopro_mp4


class Camera(Protocol):
    def start(self, h264_path: Path) -> None: ...
    def stop(self) -> None: ...
    def close(self) -> None: ...
    def preview_rgb(self): ...


class MockCamera:
    def __init__(self, cfg: CollectorConfig):
        self.cfg = cfg
        self.h264_path: Path | None = None
        self.closed = False

    def start(self, h264_path: Path) -> None:
        self.h264_path = h264_path
        h264_path.parent.mkdir(parents=True, exist_ok=True)
        h264_path.write_bytes(b"")

    def stop(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    def preview_rgb(self):
        return None


class Picamera2Camera:
    def __init__(self, cfg: CollectorConfig):
        self.cfg = cfg
        self._picam = None

    def start(self, h264_path: Path) -> None:
        from picamera2 import Picamera2
        from picamera2.encoders import H264Encoder
        from picamera2.outputs import FileOutput

        h264_path.parent.mkdir(parents=True, exist_ok=True)
        picam = self._picam
        video = self.cfg.video
        # Held open across takes. close() is the only release.
        if picam is None:
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
            self._picam = picam
        encoder = H264Encoder(bitrate=video.bitrate, framerate=video.fps)
        picam.start_encoder(encoder, FileOutput(str(h264_path)))
        if picam.started:
            return
        try:
            picam.start()
        except Exception:
            picam.stop_encoder()
            raise

    def stop(self) -> None:
        picam = self._picam
        if picam is None:
            return
        picam.stop_encoder()

    def close(self) -> None:
        picam = self._picam
        if picam is None:
            return
        picam.close()
        self._picam = None

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


@dataclass
class _SealJob:
    filename: str
    started: datetime
    h264_path: Path
    samples: list[ImuSample]
    duration_s: float


class Recorder:
    def __init__(
        self,
        cfg: CollectorConfig,
        camera: Camera,
        imu: Imu,
        catalog: Catalog,
        catalog_lock: threading.Lock,
        serial: str,
        on_failed: Callable[[], None] | None = None,
    ):
        self.cfg = cfg
        self.camera = camera
        self.imu = imu
        self._catalog = catalog
        self._catalog_lock = catalog_lock
        self._serial = serial
        self._on_failed = on_failed
        self._lock = threading.Lock()
        self._live: LiveRecording | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._closed = False
        self._seal_queue: queue.Queue[_SealJob | None] = queue.Queue()
        self._seal_thread = threading.Thread(target=self._seal_loop, name="lumi-seal")
        self._seal_thread.start()

    @property
    def recording(self) -> bool:
        return self._live is not None

    def elapsed_s(self) -> float:
        live = self._live
        if live is None:
            return 0.0
        return (datetime.now(timezone.utc) - live.started).total_seconds()

    def start(self) -> str:
        if self._live is not None:
            raise RuntimeError("already recording")
        with self._catalog_lock:
            name = next_gopro_name(self._catalog)
            save_catalog(self.cfg.catalog_path, self._catalog)
        work = self.cfg.work_dir
        work.mkdir(parents=True, exist_ok=True)
        h264_path = work / f"{Path(name).stem}.h264"
        started = datetime.now(timezone.utc)
        self.camera.start(h264_path)
        # Per take, so a straggler IMU thread cannot be cleared back into its loop.
        stop = threading.Event()
        self._stop = stop
        live = LiveRecording(filename=name, started=started, h264_path=h264_path, samples=[])
        self._live = live
        self._thread = threading.Thread(target=self._imu_loop, args=(live, stop), daemon=True)
        self._thread.start()
        return name

    def stop(self) -> None:
        live = self._live
        if live is None:
            raise RuntimeError("not recording")
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self.camera.stop()
        with self._lock:
            samples = list(live.samples)
        duration = (datetime.now(timezone.utc) - live.started).total_seconds()
        self._live = None
        self._thread = None
        self._seal_queue.put(
            _SealJob(
                filename=live.filename,
                started=live.started,
                h264_path=live.h264_path,
                samples=samples,
                duration_s=duration,
            )
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._live is not None:
                self.stop()
        finally:
            try:
                self.camera.close()
            finally:
                self._seal_queue.put(None)
                self._seal_thread.join()

    def _seal_loop(self) -> None:
        while True:
            job = self._seal_queue.get()
            if job is None:
                return
            try:
                self._seal(job)
            except Exception:
                logging.exception("recording seal failed")
                if self._on_failed is not None:
                    self._on_failed()

    def _seal(self, job: _SealJob) -> None:
        out = self.cfg.raw_videos_dir / job.filename
        out.parent.mkdir(parents=True, exist_ok=True)
        mux_gopro_mp4(
            job.h264_path,
            out,
            job.samples,
            serial=self._serial,
            started=job.started,
            fps=self.cfg.video.fps,
            silence_aac=self.cfg.work_dir / "silence.m4a",
        )
        sidecar = out.with_suffix(".imu.json")
        sidecar.write_text(
            json.dumps(
                {
                    "1": {
                        "streams": {
                            "ACCL": {
                                "samples": telemetry_json(
                                    job.samples, start=job.started, stream_name="ACCL"
                                )
                            },
                            "GYRO": {
                                "samples": telemetry_json(
                                    job.samples, start=job.started, stream_name="GYRO"
                                )
                            },
                        }
                    }
                }
            )
        )
        with self._catalog_lock:
            add_episode(
                self._catalog,
                filename=job.filename,
                duration_s=job.duration_s,
                size_bytes=out.stat().st_size,
                started_at=job.started.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            )
            save_catalog(self.cfg.catalog_path, self._catalog)
        job.h264_path.unlink(missing_ok=True)

    def _imu_loop(self, live: LiveRecording, stop: threading.Event) -> None:
        period = 1.0 / float(self.cfg.imu.rate_hz)
        next_t = time.monotonic()
        while not stop.is_set():
            sample = self.imu.read()
            if sample is not None:
                with self._lock:
                    live.samples.append(sample)
            next_t += period
            delay = next_t - time.monotonic()
            if delay > 0:
                time.sleep(delay)
