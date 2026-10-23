import json
import zipfile
from pathlib import Path

import pytest

from lumi_collector import archive
from lumi_collector.catalog import add_episode, mark_deleted, save_catalog
from lumi_collector.datasets import create_dataset, load_dataset


def _dataset(tmp_path: Path):
    dataset = create_dataset(tmp_path / "datasets", "cups", "LUMI1")
    for name in ("GX010001.MP4", "GX010002.MP4", "GX010003.MP4"):
        (dataset.raw_videos_dir / name).write_bytes(b"mp4")
        (dataset.raw_videos_dir / name).with_suffix(".imu.json").write_text("{}")
        add_episode(dataset.catalog, filename=name, duration_s=2.0, size_bytes=3)
    mark_deleted(dataset.catalog, "GX010002.MP4")
    save_catalog(dataset.catalog_path, dataset.catalog)
    return dataset


def test_pack_layout_skips_deleted(tmp_path: Path):
    dataset = _dataset(tmp_path)
    (dataset.root / "camera_calibration.json").write_text("{}")

    dest = archive.pack(dataset, archive.default_path(dataset))

    assert dest == tmp_path / "datasets" / "cups.lumi.zip"
    with zipfile.ZipFile(dest) as zf:
        names = set(zf.namelist())
        catalog = json.loads(zf.read("catalog.json"))
        assert all(info.compress_type == zipfile.ZIP_STORED for info in zf.infolist())
    assert names == {
        "catalog.json",
        "camera_calibration.json",
        "raw_videos/GX010001.MP4",
        "raw_videos/GX010001.imu.json",
        "raw_videos/GX010003.MP4",
        "raw_videos/GX010003.imu.json",
    }
    assert catalog["camera_serial"] == "LUMI1"
    assert [e["filename"] for e in catalog["episodes"]] == ["GX010001.MP4", "GX010003.MP4"]


def test_pack_selected_from_disk(tmp_path: Path):
    _dataset(tmp_path)
    dataset = load_dataset(tmp_path / "datasets" / "cups", camera_serial="")

    dest = archive.pack(dataset, tmp_path / "out.lumi.zip", ["GX010003.MP4"])

    with zipfile.ZipFile(dest) as zf:
        assert set(zf.namelist()) == {
            "catalog.json", "raw_videos/GX010003.MP4", "raw_videos/GX010003.imu.json",
        }


def test_pack_nothing_raises(tmp_path: Path):
    dataset = create_dataset(tmp_path / "datasets", "empty", "LUMI1")
    with pytest.raises(RuntimeError):
        archive.pack(dataset, tmp_path / "x.lumi.zip")
