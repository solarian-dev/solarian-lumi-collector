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

from lumi_collector.gpmf import ImuSample, telemetry_rows
from lumi_collector.mux import mux_gopro_mp4


@dataclass
class SealJob:
    filename: str
    started: datetime
    h264_path: Path
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


def write_job(job: SealJob, path: Path) -> None:
    path.write_bytes(pickle.dumps(job, protocol=pickle.HIGHEST_PROTOCOL))


def write_sidecar(path: Path, job: SealJob) -> None:
    """The .imu.json, a sample at a time: held whole, a 5 min take's samples take ~60 MB."""
    with open(path, "w") as out:
        out.write('{"1": {"streams": {')
        for i, name in enumerate(("ACCL", "GYRO")):
            out.write(f'{", " if i else ""}"{name}": {{"samples": [')
            for k, row in enumerate(telemetry_rows(job.samples, start=job.started, stream_name=name)):
                out.write(", " + json.dumps(row) if k else json.dumps(row))
            out.write("]}")
        frames = [{"t_s": t} for t in job.frame_times_s.tolist()]
        out.write(f'}}, "frames": {json.dumps(frames)}, "dropped_frames": {json.dumps(job.dropped_frames)}')
        out.write(f', "imu": {json.dumps(job.imu_info)}}}}}')


def seal(job: SealJob) -> None:
    mux_gopro_mp4(
        job.h264_path,
        job.out_mp4,
        job.samples,
        serial=job.serial,
        started=job.started,
        fps=job.fps,
        silence_aac=job.silence_aac,
        slots=job.slots,
    )
    write_sidecar(job.out_mp4.with_suffix(".imu.json"), job)


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    seal(pickle.loads(Path(args[0]).read_bytes()))


if __name__ == "__main__":
    main()
