"""Write a finished take's MP4 and .imu.json, in a process of its own.

The collector only pickles the job and starts this at idle CPU and I/O
priority on its own core. Building the GPMF payloads and dumping the sidecar
JSON hold the GIL for hundreds of milliseconds; in the collector's process
that starved Picamera2's output thread and dropped frames from the take
being recorded.
"""

from __future__ import annotations

import json
import pickle
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from lumi_collector.gpmf import ImuSample, telemetry_json
from lumi_collector.mux import mux_gopro_mp4


@dataclass
class SealJob:
    filename: str
    started: datetime
    h264_paths: list[Path]
    out_mp4: Path
    serial: str
    fps: int
    silence_aac: Path
    # (N, 8): t_s, acc ZXY, gyr ZXY, temp_c. An array pickles in one copy.
    samples: np.ndarray
    # Frame sensor times on the IMU sample clock, seconds.
    frame_times_s: np.ndarray
    slots: list[int]
    dropped_frames: list[dict]
    imu_info: dict
    duration_s: float


def sample_array(samples: list[ImuSample]) -> np.ndarray:
    rows = [(s.t_s, *s.acc_zxy, *s.gyr_zxy, s.temp_c) for s in samples]
    return np.array(rows, dtype=np.float64).reshape(-1, 8)


def _samples(array: np.ndarray) -> list[ImuSample]:
    return [ImuSample(t_s=r[0], acc_zxy=(r[1], r[2], r[3]), gyr_zxy=(r[4], r[5], r[6]), temp_c=r[7]) for r in array.tolist()]


def write_job(job: SealJob, path: Path) -> None:
    path.write_bytes(pickle.dumps(job, protocol=pickle.HIGHEST_PROTOCOL))


def seal(job: SealJob) -> None:
    samples = _samples(job.samples)
    mux_gopro_mp4(
        job.h264_paths,
        job.out_mp4,
        samples,
        serial=job.serial,
        started=job.started,
        fps=job.fps,
        silence_aac=job.silence_aac,
        slots=job.slots,
    )
    take = {
        "streams": {
            "ACCL": {"samples": telemetry_json(samples, start=job.started, stream_name="ACCL")},
            "GYRO": {"samples": telemetry_json(samples, start=job.started, stream_name="GYRO")},
        },
        "frames": [{"t_s": t} for t in job.frame_times_s.tolist()],
        "dropped_frames": job.dropped_frames,
        "imu": job.imu_info,
    }
    job.out_mp4.with_suffix(".imu.json").write_text(json.dumps({"1": take}))


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    seal(pickle.loads(Path(args[0]).read_bytes()))


if __name__ == "__main__":
    main()
