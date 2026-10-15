from pathlib import Path

import pytest

from lumi_collector.catalog import add_episode, empty_catalog, save_catalog
from lumi_collector.datasets import (
    LEGACY_DATASET,
    MAX_NAME,
    create_dataset,
    delete_dataset,
    list_datasets,
    migrate_legacy,
)


def test_create_list_and_delete(tmp_path: Path):
    root = tmp_path / "datasets"
    assert list_datasets(root, "LUMI1") == []
    cups = create_dataset(root, "cups", "LUMI1")
    create_dataset(root, "a-b_1", "LUMI1")
    (root / ".hidden").mkdir()
    assert cups.raw_videos_dir.is_dir()
    assert cups.catalog.camera_serial == "LUMI1"
    assert [d.name for d in list_datasets(root, "LUMI1")] == ["a-b_1", "cups"]
    with pytest.raises(FileExistsError):
        create_dataset(root, "cups", "LUMI1")
    delete_dataset(cups)
    assert [d.name for d in list_datasets(root, "LUMI1")] == ["a-b_1"]


@pytest.mark.parametrize("name", ["", "Cups", "a/b", "..", "a" * (MAX_NAME + 1)])
def test_create_rejects_names_off_the_keyboard(tmp_path: Path, name):
    with pytest.raises(ValueError):
        create_dataset(tmp_path, name, "LUMI1")


def test_listing_counts_episodes_from_each_catalog(tmp_path: Path):
    ds = create_dataset(tmp_path, "cups", "LUMI1")
    add_episode(ds.catalog, filename="GX010001.MP4", duration_s=1, size_bytes=1)
    save_catalog(ds.catalog_path, ds.catalog)
    [loaded] = list_datasets(tmp_path, "LUMI1")
    assert len(loaded.catalog.visible()) == 1


def test_migrate_moves_legacy_recordings_into_default(tmp_path: Path):
    raw = tmp_path / "raw_videos"
    raw.mkdir()
    (raw / "GX010001.MP4").write_bytes(b"mp4")
    catalog = empty_catalog("LUMI1")
    add_episode(catalog, filename="GX010001.MP4", duration_s=1, size_bytes=3)
    save_catalog(tmp_path / "catalog.json", catalog)

    migrate_legacy(tmp_path, tmp_path / "datasets")
    assert not raw.exists()
    assert not (tmp_path / "catalog.json").exists()
    [default] = list_datasets(tmp_path / "datasets", "LUMI1")
    assert default.name == LEGACY_DATASET
    assert (default.raw_videos_dir / "GX010001.MP4").read_bytes() == b"mp4"
    assert [e.filename for e in default.catalog.visible()] == ["GX010001.MP4"]

    migrate_legacy(tmp_path, tmp_path / "datasets")
    assert len(list_datasets(tmp_path / "datasets", "LUMI1")) == 1


def test_migrate_does_nothing_on_a_fresh_card(tmp_path: Path):
    migrate_legacy(tmp_path, tmp_path / "datasets")
    assert not (tmp_path / "datasets").exists()
