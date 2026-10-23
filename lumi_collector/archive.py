"""``<name>.lumi.zip``: a dataset folder packed for upload or ``solarian umi-slam``.

The archive holds the folder's own layout at its root::

    catalog.json                  only the episodes packed, none marked deleted
    raw_videos/GX01NNNN.MP4
    raw_videos/GX01NNNN.imu.json
    camera_calibration.json       when the dataset has one

Members are stored, not deflated: the MP4s are already compressed.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import asdict
from pathlib import Path

from lumi_collector.datasets import Dataset

SUFFIX = ".lumi.zip"
CALIBRATION_FILENAME = "camera_calibration.json"


def default_path(dataset: Dataset) -> Path:
    return dataset.root.parent / f"{dataset.name}{SUFFIX}"


def pack(dataset: Dataset, dest: Path, filenames: list[str] | None = None) -> Path:
    """Pack the visible episodes of ``dataset``, or only ``filenames`` of them, into ``dest``."""
    catalog = dataset.catalog
    episodes = catalog.visible()
    if filenames is not None:
        wanted = set(filenames)
        episodes = [e for e in episodes if e.filename in wanted]
    raw_videos = dataset.raw_videos_dir
    episodes = [e for e in episodes if (raw_videos / e.filename).is_file()]
    if not episodes:
        raise RuntimeError(f"no episodes to pack in {dataset.root}")

    payload = {
        "camera_serial": catalog.camera_serial,
        "next_index": catalog.next_index,
        "episodes": [asdict(e) for e in episodes],
    }
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("catalog.json", json.dumps(payload, indent=2))
        for episode in episodes:
            video = raw_videos / episode.filename
            zf.write(video, arcname=f"raw_videos/{video.name}")
            sidecar = video.with_suffix(".imu.json")
            if sidecar.is_file():
                zf.write(sidecar, arcname=f"raw_videos/{sidecar.name}")
        calibration = dataset.root / CALIBRATION_FILENAME
        if calibration.is_file():
            zf.write(calibration, arcname=CALIBRATION_FILENAME)
    return dest
