from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class Episode:
    filename: str
    started_at: str
    duration_s: float
    size_bytes: int
    uploaded: bool = False
    deleted: bool = False

    @property
    def stem(self) -> str:
        return Path(self.filename).stem


@dataclass
class Catalog:
    camera_serial: str
    next_index: int
    episodes: list[Episode]

    def visible(self) -> list[Episode]:
        return [e for e in self.episodes if not e.deleted]

    def last(self) -> Episode | None:
        vis = self.visible()
        return vis[-1] if vis else None


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def empty_catalog(camera_serial: str) -> Catalog:
    return Catalog(camera_serial=camera_serial, next_index=1, episodes=[])


def load_catalog(path: Path, camera_serial: str) -> Catalog:
    if not path.is_file():
        return empty_catalog(camera_serial)
    raw = json.loads(path.read_text())
    episodes = [Episode(**row) for row in raw.get("episodes") or []]
    return Catalog(
        camera_serial=str(raw.get("camera_serial") or camera_serial),
        next_index=int(raw.get("next_index") or 1),
        episodes=episodes,
    )


def save_catalog(path: Path, catalog: Catalog) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "camera_serial": catalog.camera_serial,
        "next_index": catalog.next_index,
        "episodes": [asdict(e) for e in catalog.episodes],
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(path)


def next_gopro_name(catalog: Catalog) -> str:
    """GoPro Hero 10 chapter names: GX01NNNN.MP4."""
    name = f"GX01{catalog.next_index:04d}.MP4"
    catalog.next_index += 1
    return name


def add_episode(
    catalog: Catalog,
    *,
    filename: str,
    duration_s: float,
    size_bytes: int,
    started_at: str | None = None,
) -> Episode:
    episode = Episode(
        filename=filename,
        started_at=started_at or _now_iso(),
        duration_s=float(duration_s),
        size_bytes=int(size_bytes),
    )
    catalog.episodes.append(episode)
    return episode


def mark_deleted(catalog: Catalog, filename: str) -> Episode | None:
    for episode in catalog.episodes:
        if episode.filename == filename and not episode.deleted:
            episode.deleted = True
            return episode
    return None


def mark_uploaded(catalog: Catalog, filenames: list[str]) -> None:
    wanted = set(filenames)
    for episode in catalog.episodes:
        if episode.filename in wanted:
            episode.uploaded = True
