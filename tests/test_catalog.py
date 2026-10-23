from pathlib import Path

from lumi_collector.catalog import (
    add_episode,
    empty_catalog,
    load_catalog,
    mark_deleted,
    mark_uploaded,
    next_gopro_name,
    save_catalog,
)


def test_gopro_filenames_and_roundtrip(tmp_path: Path):
    cat = empty_catalog("UMI00ABCDEF")
    assert next_gopro_name(cat) == "GX010001.MP4"
    assert next_gopro_name(cat) == "GX010002.MP4"
    add_episode(cat, filename="GX010001.MP4", duration_s=12.4, size_bytes=4_000_000)
    path = tmp_path / "catalog.json"
    save_catalog(path, cat)
    loaded = load_catalog(path, "other")
    assert loaded.camera_serial == "UMI00ABCDEF"
    assert loaded.next_index == 3
    assert loaded.last().filename == "GX010001.MP4"


def test_delete_hides_and_upload_flags(tmp_path: Path):
    cat = empty_catalog("UMI1")
    add_episode(cat, filename="GX010001.MP4", duration_s=1, size_bytes=10)
    add_episode(cat, filename="GX010002.MP4", duration_s=2, size_bytes=20)
    mark_deleted(cat, "GX010001.MP4")
    assert [e.filename for e in cat.visible()] == ["GX010002.MP4"]
    mark_uploaded(cat, ["GX010002.MP4"])
    assert cat.visible()[0].uploaded is True
