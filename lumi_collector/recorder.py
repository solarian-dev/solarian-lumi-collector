from __future__ import annotations

import logging
import os
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

import numpy as np

from lumi_collector.catalog import Catalog, add_episode, next_gopro_name, save_catalog
from lumi_collector.config import CollectorConfig
from lumi_collector.imu import Imu
from lumi_collector.seal import SealJob, sample_array, write_job

# A take whose last frame is older than this when it stops had a camera that stopped sending.
STALL_S = 2.0
# A frame step over this many periods means the camera dropped frames.
GAP_PERIODS = 1.5
# 10 Mbit/s is 75 MB a minute. A take starts on the RAM disk with this much
# memory available and moves to the SD card below the spill level.
RAM_START_MB = 120
RAM_SPILL_MB = 60
MEMORY_CHECK_S = 1.0


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


def cap_exposure(tuning: dict, max_exposure_us: int, fps: float) -> dict:
    """Limit every auto-exposure mode of a libcamera tuning to ``max_exposure_us``.

    The stock modes stretch the shutter to the whole frame (33 ms at 30 fps)
    indoors, and the motion of a handheld demo smears features ORB-SLAM3
    needs. The top gain grows by the shutter time given up, so the image
    keeps its brightness; past the sensor's analogue limit it is digital.
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
    return tuning


def mem_available_mb() -> float | None:
    try:
        with open("/proc/meminfo") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1024
    except OSError:
        return None
    return None


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
    def start(self, h264_path: Path, on_gap: Callable[[], None]) -> None: ...
    def spill(self, h264_path: Path) -> None: ...
    def stop(self) -> None: ...
    def close(self) -> None: ...
    def preview_rgb(self): ...
    def frame_times_s(self) -> list[float]: ...


class MockCamera:
    def __init__(self, cfg: CollectorConfig):
        self.cfg = cfg
        self.h264_path: Path | None = None
        self.closed = False

    def start(self, h264_path: Path, on_gap: Callable[[], None]) -> None:
        self.h264_path = h264_path
        h264_path.parent.mkdir(parents=True, exist_ok=True)
        h264_path.write_bytes(b"")

    def spill(self, h264_path: Path) -> None:
        self.h264_path = h264_path
        h264_path.write_bytes(b"")

    def stop(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    def preview_rgb(self):
        return None

    def frame_times_s(self) -> list[float]:
        return []


class Picamera2Camera:
    def __init__(self, cfg: CollectorConfig):
        self.cfg = cfg
        self._picam = None
        self._encoder = None
        self._written_us: list[int] = []
        self._spill_to: Path | None = None

    def start(self, h264_path: Path, on_gap: Callable[[], None]) -> None:
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
                tuning_data = Picamera2.load_tuning_file(str(tuning))
                if video.max_exposure_us:
                    tuning_data = cap_exposure(tuning_data, video.max_exposure_us, video.fps)
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
                controls={"FrameRate": video.fps},
            )
            picam.configure(config)
            self._picam = picam
        written = self._written_us = []
        self._spill_to = None
        gap_us = GAP_PERIODS * 1e6 / video.fps
        camera = self

        class TimedFileOutput(FileOutput):
            # Only frames that reach the file: FileOutput skips to the first keyframe.
            def _write(self, frame, timestamp=None):
                spill = camera._spill_to
                if spill is not None:
                    # Swapped here, between two frames on the output thread. The
                    # fileoutput setter would wait for the next keyframe.
                    camera._spill_to = None
                    self._fileoutput.close()
                    self._fileoutput = open(spill, "wb")
                super()._write(frame, timestamp)
                if written and timestamp - written[-1] > gap_us:
                    on_gap()
                written.append(timestamp)

        encoder = self._encoder = H264Encoder(bitrate=video.bitrate, framerate=video.fps)
        picam.start_encoder(encoder, TimedFileOutput(str(h264_path)))
        if picam.started:
            return
        try:
            picam.start()
        except Exception:
            picam.stop_encoder()
            raise

    def spill(self, h264_path: Path) -> None:
        self._spill_to = h264_path

    def stop(self) -> None:
        picam = self._picam
        if picam is None:
            return
        picam.stop_encoder()
        frames = self.frame_times_s()
        # After "Camera frontend has timed out" libcamera sends nothing until
        # the camera is reopened; close it so the next take does that.
        if not frames or time.monotonic() - frames[-1] > STALL_S:
            logging.warning("camera stalled (%d frames); reopening for the next take", len(frames))
            self.close()

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
    # Consecutive pieces of the stream: RAM disk first, then the SD card if memory ran low.
    h264_paths: list[Path] = field(default_factory=list)


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
        on_dropped: Callable[[str, int], None] | None = None,
    ):
        self.cfg = cfg
        self.camera = camera
        self.imu = imu
        self._catalog = catalog
        self._catalog_lock = catalog_lock
        self._catalog_dirty = False
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
        self._seal_queue: queue.Queue[SealJob | None] = queue.Queue()
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

    def _take_dir(self) -> Path:
        ram = self.cfg.ram_work_dir
        # The RAM disk's mount (/dev/shm) exists on the Pi, not on a dev machine.
        if ram is not None and ram.parent.is_dir():
            available = mem_available_mb()
            if available is None or available >= RAM_START_MB:
                return ram
            logging.warning("%.0f MB of memory available; recording to the SD card", available)
        return self.cfg.work_dir

    def start(self) -> str:
        if self._live is not None:
            raise RuntimeError("already recording")
        with self._catalog_lock:
            name = next_gopro_name(self._catalog)
            save_catalog(self.cfg.catalog_path, self._catalog)
            self._catalog_dirty = False
        work = self._take_dir()
        work.mkdir(parents=True, exist_ok=True)
        h264_path = work / f"{Path(name).stem}.h264"
        started = datetime.now(timezone.utc)
        # Before the camera, so the IMU covers the first frame.
        self.imu.start_stream()
        live = LiveRecording(filename=name, started=started, h264_paths=[h264_path])
        self._live = live
        self.camera.start(h264_path, self._on_gap)
        # Per take, so a straggler IMU thread cannot be cleared back into its loop.
        stop = threading.Event()
        self._stop = stop
        self._thread = threading.Thread(target=self._imu_loop, args=(stop, live), daemon=True)
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

    def stop(self) -> None:
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
            if self._catalog_dirty:
                save_catalog(self.cfg.catalog_path, self._catalog)
                self._catalog_dirty = False
        self._seal_queue.put(
            SealJob(
                filename=live.filename,
                started=live.started,
                h264_paths=list(live.h264_paths),
                out_mp4=self.cfg.raw_videos_dir / live.filename,
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

    def _seal(self, job: SealJob) -> None:
        job.out_mp4.parent.mkdir(parents=True, exist_ok=True)
        job_path = job.h264_paths[0].with_suffix(".job")
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
                self._catalog,
                filename=job.filename,
                duration_s=job.duration_s,
                size_bytes=job.out_mp4.stat().st_size,
                started_at=job.started.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                dropped_frames=sum(g["missing"] for g in job.dropped_frames),
            )
            # The JSON dump holds the GIL; during a take it waits for start or stop.
            if self.recording:
                self._catalog_dirty = True
            else:
                save_catalog(self.cfg.catalog_path, self._catalog)
        for path in job.h264_paths:
            path.unlink(missing_ok=True)
        logging.info("sealed %s in %.2f s", job.filename, time.monotonic() - t0)

    def _imu_loop(self, stop: threading.Event, live: LiveRecording) -> None:
        period = self.imu.poll_interval_s
        next_t = time.monotonic()
        next_memory_check = next_t + MEMORY_CHECK_S
        ram = self.cfg.ram_work_dir
        while not stop.is_set():
            with self._lock:
                self.imu.poll()
            now = time.monotonic()
            if ram is not None and now >= next_memory_check:
                next_memory_check = now + MEMORY_CHECK_S
                available = mem_available_mb()
                if available is not None and available < RAM_SPILL_MB and live.h264_paths[-1].parent == ram:
                    spill = self.cfg.work_dir / f"{Path(live.filename).stem}.part2.h264"
                    logging.warning("%s: %.0f MB of memory left; continuing on the SD card", live.filename, available)
                    live.h264_paths.append(spill)
                    self.camera.spill(spill)
            next_t += period
            delay = next_t - time.monotonic()
            if delay > 0:
                time.sleep(delay)
