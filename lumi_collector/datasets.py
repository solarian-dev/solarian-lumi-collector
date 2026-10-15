"""Datasets are folders under ``datasets_dir``, each laid out like a UMI session."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from lumi_collector.catalog import Catalog, load_catalog

# Every character on the keyboard screen, so a typed name is always a safe folder name.
KEYBOARD_ROWS = ("1234567890", "qwertyuiop", "asdfghjkl-", "zxcvbnm_")
NAME_CHARS = frozenset("".join(KEYBOARD_ROWS))
MAX_NAME = 24
LEGACY_DATASET = "default"


# Compared by identity: a pending save holds the object and must update the one the UI shows.
@dataclass(eq=False)
class Dataset:
    name: str
    root: Path
    catalog: Catalog

    @property
    def catalog_path(self) -> Path:
        return self.root / "catalog.json"

    @property
    def raw_videos_dir(self) -> Path:
        return self.root / "raw_videos"


def load_dataset(root: Path, camera_serial: str) -> Dataset:
    return Dataset(root.name, root, load_catalog(root / "catalog.json", camera_serial))


def list_datasets(datasets_dir: Path, camera_serial: str) -> list[Dataset]:
    if not datasets_dir.is_dir():
        return []
    roots = sorted(p for p in datasets_dir.iterdir() if p.is_dir() and not p.name.startswith("."))
    return [load_dataset(root, camera_serial) for root in roots]


def create_dataset(datasets_dir: Path, name: str, camera_serial: str) -> Dataset:
    if not name or len(name) > MAX_NAME or not set(name) <= NAME_CHARS:
        raise ValueError(f"invalid dataset name {name!r}")
    root = datasets_dir / name
    datasets_dir.mkdir(parents=True, exist_ok=True)
    root.mkdir()
    (root / "raw_videos").mkdir()
    return load_dataset(root, camera_serial)


def delete_dataset(dataset: Dataset) -> None:
    shutil.rmtree(dataset.root)


def migrate_legacy(data_dir: Path, datasets_dir: Path) -> None:
    """Move recordings from before datasets into ``default``. Renames only; nothing is copied."""
    raw_videos = data_dir / "raw_videos"
    catalog = data_dir / "catalog.json"
    target = datasets_dir / LEGACY_DATASET
    if target.exists() or not (raw_videos.exists() or catalog.exists()):
        return
    target.mkdir(parents=True)
    if raw_videos.exists():
        raw_videos.rename(target / "raw_videos")
    else:
        (target / "raw_videos").mkdir()
    if catalog.exists():
        catalog.rename(target / "catalog.json")
