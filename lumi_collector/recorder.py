from __future__ import annotations

import io
import logging
import os
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections import Counter, deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

import numpy as np

from lumi_collector.catalog import add_episode, next_gopro_name, save_catalog
from lumi_collector.config import CollectorConfig
from lumi_collector.datasets import Dataset
from lumi_collector.imu import Imu
from lumi_collector.mux import drop_cache
from lumi_collector.seal import SealJob, sample_array, write_job

# A camera that has sent nothing for this long hit libcamera's frontend timeout (1 s), after
# which it sends nothing until reopened.
STALL_S = 2.0
# A frame step over this many periods means the camera dropped frames.
GAP_PERIODS = 1.5
# About 17 s of a stuck SD card at 15 Mbit/s. Past it the camera waits on the card again.
WRITE_BUFFER_BYTES = 32 * 1024 * 1024
# How often the writer drops a take's pages from the page cache once they are on the card.
CACHE_DROP_BYTES = 4 * 1024 * 1024


def frame_slots(times_s: list[float], fps: float) -> list[int]:
    """Each frame's place on the nominal fps grid; a dropped frame leaves a slot empty.

    Rounded step by step, so the sensor's clock running a little off fps
    never adds up to a phantom drop over a long take.
    """
    slots = [0] if times_s else []
    for prev, cur in zip(times_s, times_s[1:]):
        slots.append(slots[-1] + max(1, round((cur - prev) * fps)))
    return slots


def frame_gaps(slots: list[int]) -> list[dict]:
    """``after_frame`` k is followed by ``missing`` empty slots."""
    return [
        {"after_frame": k, "missing": b - a - 1}
        for k, (a, b) in enumerate(zip(slots, slots[1:]))
        if b - a > 1
    ]


def cap_exposure(tuning: dict, max_exposure_us: int, max_gain: float, fps: float) -> dict:
    """Limit every auto-exposure mode of a libcamera tuning to ``max_exposure_us`` and ``max_gain``.

    The stock modes stretch the shutter to the whole frame (33 ms at 30 fps)
    indoors, and the motion of a handheld demo smears features ORB-SLAM3
    needs. The top gain grows by the shutter time given up, up to
    ``max_gain``: beyond it a dim room comes out darker rather than noisier.
    """
    frame_us = 1e6 / fps
    for algo in tuning["algorithms"]:
        agc = algo.get("rpi.agc")
        if agc is None:
            continue
        for channel in agc.get("channels", [agc]):
            for mode in channel["exposure_modes"].values():
                longest = max(mode["shutter"])
                mode["shutter"] = [min(s, max_exposure_us) for s in mode["shutter"]]
                mode["gain"][-1] *= max(1.0, min(longest, frame_us) / max_exposure_us)
                mode["gain"] = [min(g, max_gain) for g in mode["gain"]]
    return tuning


class QueuedFile(io.BufferedIOBase):
    """A file whose ``write`` returns at once while a thread of its own writes to disk.

    Picamera2 writes each encoded frame on the thread that hands the encoder its
    next input buffer, and the camera holds only about 200 ms of buffers. An SD
    card write can stall longer than that, which drops frames, and over 1 s
    times the camera out.
    """

    def __init__(self, path: Path, max_bytes: int = WRITE_BUFFER_BYTES):
        super().__init__()
        self._file = open(path, "wb")
        self._max_bytes = max_bytes
        self._chunks: deque[bytes] = deque()
        self._queued = 0
        self._ending = False
        self._error: OSError | None = None
        self._written = 0
        self._dropped = 0
        self._cond = threading.Condition()
        self.peak_bytes = 0
        self.waited = False
        self._thread = threading.Thread(target=self._drain, name="lumi-h264-writer", daemon=True)
        self._thread.start()

    def writable(self) -> bool:
        return True

    def write(self, data) -> int:
        chunk = bytes(data)
        with self._cond:
            if self._queued + len(chunk) > self._max_bytes and self._error is None:
                if not self.waited:
                    logging.warning("write buffer full (%d MB); the camera waits on the SD card", self._max_bytes >> 20)
                self.waited = True
                while self._queued + len(chunk) > self._max_bytes and self._error is None:
                    self._cond.wait()
            # After a failed write the take is lost; close() raises it.
            if self._error is None:
                self._chunks.append(chunk)
                self._queued += len(chunk)
                self.peak_bytes = max(self.peak_bytes, self._queued)
                self._cond.notify_all()
        return len(chunk)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        if self.closed:
            return
        with self._cond:
            self._ending = True
            self._cond.notify_all()
        self._thread.join()
        self._file.close()
        super().close()
        if self._error is not None:
            raise self._error

    def _drain(self) -> None:
        while True:
            with self._cond:
                while not self._chunks and not self._ending:
                    self._cond.wait()
                if not self._chunks:
                    return
                chunk = self._chunks.popleft()
            try:
                self._file.write(chunk)
                self._written += len(chunk)
                if self._written - self._dropped >= CACHE_DROP_BYTES:
                    self._dropped = self._written
                    drop_cache(self._file.fileno())
            except OSError as exc:
                with self._cond:
                    self._error = exc
                    self._chunks.clear()
                    self._queued = 0
                    self._cond.notify_all()
                return
            with self._cond:
                self._queued -= len(chunk)
                self._cond.notify_all()


def _idle_priority() -> list[str]:
    """Run the save only on CPU and disk time the capture leaves unused, on the last core."""
    prefix: list[str] = []
    cores = os.cpu_count() or 1
    if cores >= 4 and shutil.which("taskset"):
        prefix += ["taskset", "-c", str(cores - 1)]
    if shutil.which("chrt"):
        prefix += ["chrt", "--idle", "0"]
    if shutil.which("ionice"):
        prefix += ["ionice", "-c", "3"]
    return prefix


def start_seal_process(job_path: Path) -> subprocess.Popen:
    cmd = [*_idle_priority(), sys.executable, "-m", "lumi_collector.seal", str(job_path)]
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)


class Camera(Protocol):
    def open(self) -> None: ...
    def start(self, h264_path: Path, on_gap: Callable[[], None]) -> None: ...
    def stop(self) -> None: ...
    def close(self) -> None: ...
    def stalled(self) -> bool: ...
    def frame_times_s(self) -> list[float]: ...


class MockCamera:
    def __init__(self, cfg: CollectorConfig):
        self.cfg = cfg
        self.h264_path: Path | None = None
        self.closed = False
        self._started_s: float | None = None
        self._stopped_s: float | None = None

    def open(self) -> None:
        self.closed = False

    def start(self, h264_path: Path, on_gap: Callable[[], None]) -> None:
        self.closed = False
        self.h264_path = h264_path
        h264_path.parent.mkdir(parents=True, exist_ok=True)
        h264_path.write_bytes(b"")
        self._started_s, self._stopped_s = time.monotonic(), None

    def stop(self) -> None:
        self._stopped_s = time.monotonic()

    def close(self) -> None:
        self.closed = True

    def stalled(self) -> bool:
        return False

    def frame_times_s(self) -> list[float]:
        """A frame when the take started, then one every frame period until it stopped."""
        if self._started_s is None:
            return []
        fps = self.cfg.video.fps
        end = self._stopped_s if self._stopped_s is not None else time.monotonic()
        return [self._started_s + k / fps for k in range(1 + int((end - self._started_s) * fps))]


class Picamera2Camera:
    def __init__(self, cfg: CollectorConfig):
        self.cfg = cfg
        self._picam = None
        self._encoder = None
        self._file: QueuedFile | None = None
        self._written_us: list[int] = []
        self._last_request_s = 0.0

    def open(self) -> None:
        """Start the sensor. It stays streaming between takes; close() is the only release."""
        if self._picam is not None:
            return
        from libcamera import controls as lc
        from picamera2 import Picamera2

        video = self.cfg.video
        tuning = Path(video.tuning_file)
        if tuning.is_file():
            tuning_data = Picamera2.load_tuning_file(str(tuning))
            if video.max_exposure_us:
                tuning_data = cap_exposure(tuning_data, video.max_exposure_us, video.max_gain, video.fps)
            picam = Picamera2(tuning=tuning_data)
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
            controls={
                "FrameRate": video.fps,
                "Sharpness": video.sharpness,
                "NoiseReductionMode": {
                    "off": lc.draft.NoiseReductionModeEnum.Off,
                    "fast": lc.draft.NoiseReductionModeEnum.Fast,
                }[video.denoise],
            },
        )
        try:
            picam.configure(config)
            # Every completed request, recording or not: what stalled() watches.
            picam.post_callback = self._request_done
            self._last_request_s = time.monotonic()
            picam.start()
        except Exception:
            picam.close()
            raise
        self._picam = picam

    def _request_done(self, _request) -> None:
        self._last_request_s = time.monotonic()

    def start(self, h264_path: Path, on_gap: Callable[[], None]) -> None:
        from picamera2.encoders import H264Encoder
        from picamera2.outputs import FileOutput

        self.open()
        h264_path.parent.mkdir(parents=True, exist_ok=True)
        video = self.cfg.video
        written = self._written_us = []
        gap_us = GAP_PERIODS * 1e6 / video.fps

        class TimedFileOutput(FileOutput):
            # Only frames that reach the file: FileOutput skips to the first keyframe.
            def _write(self, frame, timestamp=None):
                super()._write(frame, timestamp)
                if written and timestamp - written[-1] > gap_us:
                    on_gap()
                written.append(timestamp)

        file = self._file = QueuedFile(h264_path)
        # A keyframe a second; the driver's default is every 60 frames.
        encoder = self._encoder = H264Encoder(bitrate=video.bitrate, iperiod=video.fps, framerate=video.fps)
        try:
            self._picam.start_encoder(encoder, TimedFileOutput(file))
        except Exception:
            self._file = None
            file.close()
            raise

    def stop(self) -> None:
        if self._picam is not None:
            self._picam.stop_encoder()
        file, self._file = self._file, None
        if file is not None:
            # Drains what the card has not taken yet, so the .h264 is whole for the save.
            file.close()
            logging.info("write buffer peak %.1f MB%s", file.peak_bytes / 1e6, ", full" if file.waited else "")

    def close(self) -> None:
        picam = self._picam
        if picam is None:
            return
        picam.close()
        self._picam = None

    def stalled(self) -> bool:
        return self._picam is not None and time.monotonic() - self._last_request_s > STALL_S

    def frame_times_s(self) -> list[float]:
        """Sensor start-of-exposure of each written frame, time.monotonic seconds.

        The encoder hands outputs microseconds since its first frame; libcamera's
        SensorTimestamp is CLOCK_MONOTONIC.
        """
        first = self._encoder.firsttimestamp if self._encoder is not None else None
        if first is None:
            return []
        return [(first + us) / 1e6 for us in self._written_us]


@dataclass
class LiveRecording:
    filename: str
    started: datetime
    h264_path: Path
    dataset: Dataset


class Recorder:
    def __init__(
        self,
        cfg: CollectorConfig,
        camera: Camera,
        imu: Imu,
        catalog_lock: threading.Lock,
        serial: str,
        on_failed: Callable[[], None] | None = None,
        on_dropped: Callable[[str, int], None] | None = None,
    ):
        self.cfg = cfg
        self.camera = camera
        self.imu = imu
        self._catalog_lock = catalog_lock
        # Catalogs whose save waits for the take to start or stop, by dataset root.
        self._dirty: dict[Path, Dataset] = {}
        # Takes stopped but not yet saved, by dataset root. Guarded by the catalog lock.
        self._pending: Counter[Path] = Counter()
        self._serial = serial
        self._on_failed = on_failed
        self._on_dropped = on_dropped
        self._lock = threading.Lock()
        self._live: LiveRecording | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._closed = False
        # Set while a take that already dropped a frame records: the save waits.
        self._hold_seal = threading.Event()
        self._seal_proc: subprocess.Popen | None = None
        self._seal_proc_lock = threading.Lock()
        self._seal_queue: queue.Queue[tuple[SealJob, Dataset] | None] = queue.Queue()
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

    def busy(self, dataset: Dataset) -> bool:
        """True while a take of ``dataset`` records or waits to be saved."""
        live = self._live
        if live is not None and live.dataset.root == dataset.root:
            return True
        with self._catalog_lock:
            return self._pending[dataset.root] > 0

    def _save_dirty(self) -> None:
        """Caller holds the catalog lock."""
        for dataset in self._dirty.values():
            save_catalog(dataset.catalog_path, dataset.catalog)
        self._dirty.clear()

    def start(self, dataset: Dataset) -> str:
        if self._live is not None:
            raise RuntimeError("already recording")
        with self._catalog_lock:
            name = next_gopro_name(dataset.catalog)
            self._dirty[dataset.root] = dataset
            self._save_dirty()
        self.cfg.work_dir.mkdir(parents=True, exist_ok=True)
        h264_path = self.cfg.work_dir / f"{Path(name).stem}.h264"
        started = datetime.now(timezone.utc)
        # Before the camera, so the IMU covers the first frame.
        self.imu.start_stream()
        self._live = LiveRecording(filename=name, started=started, h264_path=h264_path, dataset=dataset)
        self.camera.start(h264_path, self._on_gap)
        # Per take, so a straggler IMU thread cannot be cleared back into its loop.
        stop = threading.Event()
        self._stop = stop
        self._thread = threading.Thread(target=self._imu_loop, args=(stop,), daemon=True)
        self._thread.start()
        return name

    def _on_gap(self) -> None:
        """Camera output thread: a frame arrived late. Freeze the save until the take ends."""
        if self._hold_seal.is_set():
            return
        self._hold_seal.set()
        with self._seal_proc_lock:
            if self._seal_proc is not None:
                self._seal_proc.send_signal(signal.SIGSTOP)

    def _release_seal(self) -> None:
        self._hold_seal.clear()
        with self._seal_proc_lock:
            if self._seal_proc is not None:
                self._seal_proc.send_signal(signal.SIGCONT)

    def check_camera(self) -> bool:
        """Reopen a camera that stopped sending; True if it had. A take it was recording ends there.

        The take is not resumed: a hole of seconds in a demo is no use to SLAM.
        """
        if not self.camera.stalled():
            return False
        logging.warning("the camera sent nothing for %.0f s; reopening it", STALL_S)
        if self._live is not None:
            self.stop()
        self.camera.close()
        try:
            self.camera.open()
        except Exception:
            logging.exception("reopening the camera failed; the next take tries again")
        return True

    def stop(self) -> bool:
        """End the take and queue its save. False if the camera sent no frame: nothing is saved."""
        live = self._live
        if live is None:
            raise RuntimeError("not recording")
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self.camera.stop()
        self._release_seal()
        with self._lock:
            self.imu.poll()
            samples = self.imu.finish_stream()
            imu_info = self.imu.stream_info()
        origin = imu_info["clock_origin_monotonic_s"]
        frame_times = [t - origin for t in self.camera.frame_times_s()]
        fps = self.cfg.video.fps
        slots = frame_slots(frame_times, fps)
        gaps = frame_gaps(slots)
        dropped = sum(g["missing"] for g in gaps)
        if dropped:
            where = ", ".join(f"{g['missing']} after frame {g['after_frame']}" for g in gaps)
            logging.warning("%s: %d frames dropped (%s); repeated in the video timing", live.filename, dropped, where)
            if self._on_dropped is not None:
                self._on_dropped(live.filename, dropped)
        duration = (datetime.now(timezone.utc) - live.started).total_seconds()
        self._live = None
        self._thread = None
        with self._catalog_lock:
            self._save_dirty()
        if not frame_times:
            logging.warning("%s: the camera sent no frames; nothing to save", live.filename)
            live.h264_path.unlink(missing_ok=True)
            return False
        with self._catalog_lock:
            self._pending[live.dataset.root] += 1
        job = SealJob(
            filename=live.filename,
            started=live.started,
            h264_path=live.h264_path,
            out_mp4=live.dataset.raw_videos_dir / live.filename,
            serial=self._serial,
            fps=fps,
            silence_aac=self.cfg.work_dir / "silence.m4a",
            samples=sample_array(samples),
            frame_times_s=np.asarray(frame_times, dtype=float),
            slots=slots,
            dropped_frames=gaps,
            imu_info=imu_info,
            duration_s=duration,
        )
        self._seal_queue.put((job, live.dataset))
        return True

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
            item = self._seal_queue.get()
            if item is None:
                return
            job, dataset = item
            try:
                self._seal(job, dataset)
            except Exception:
                logging.exception("recording seal failed")
                if self._on_failed is not None:
                    self._on_failed()
            finally:
                with self._catalog_lock:
                    self._pending[dataset.root] -= 1

    def _seal(self, job: SealJob, dataset: Dataset) -> None:
        job.out_mp4.parent.mkdir(parents=True, exist_ok=True)
        job_path = job.h264_path.with_suffix(".job")
        t0 = time.monotonic()
        write_job(job, job_path)
        with self._seal_proc_lock:
            proc = self._seal_proc = start_seal_process(job_path)
            if self._hold_seal.is_set():
                proc.send_signal(signal.SIGSTOP)
        try:
            _, err = proc.communicate()
        finally:
            with self._seal_proc_lock:
                self._seal_proc = None
            job_path.unlink(missing_ok=True)
        if proc.returncode != 0:
            raise RuntimeError((err or "").strip() or f"seal exited {proc.returncode}")
        with self._catalog_lock:
            add_episode(
                dataset.catalog,
                filename=job.filename,
                duration_s=job.duration_s,
                size_bytes=job.out_mp4.stat().st_size,
                started_at=job.started.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                dropped_frames=sum(g["missing"] for g in job.dropped_frames),
            )
            # The JSON dump holds the GIL; during a take it waits for start or stop.
            if self.recording:
                self._dirty[dataset.root] = dataset
            else:
                save_catalog(dataset.catalog_path, dataset.catalog)
        job.h264_path.unlink(missing_ok=True)
        logging.info("sealed %s in %.2f s", job.filename, time.monotonic() - t0)

    def _imu_loop(self, stop: threading.Event) -> None:
        period = self.imu.poll_interval_s
        next_t = time.monotonic()
        while not stop.is_set():
            with self._lock:
                self.imu.poll()
            next_t += period
            delay = next_t - time.monotonic()
            if delay > 0:
                time.sleep(delay)
