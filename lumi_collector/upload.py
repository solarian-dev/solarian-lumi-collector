from __future__ import annotations

import json
import zipfile
from pathlib import Path

from lumi_collector.auth import PrimaryKeyClient
from lumi_collector.catalog import Catalog, Episode


def zip_episodes(
    raw_videos: Path,
    episodes: list[Episode],
    dest: Path,
    *,
    camera_serial: str,
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "camera_serial": camera_serial,
        "episodes": [
            {
                "filename": e.filename,
                "started_at": e.started_at,
                "duration_s": e.duration_s,
                "size_bytes": e.size_bytes,
            }
            for e in episodes
        ],
    }
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("session.json", json.dumps(manifest, indent=2))
        for episode in episodes:
            src = raw_videos / episode.filename
            if src.is_file():
                zf.write(src, arcname=f"raw_videos/{episode.filename}")
    return dest


def upload_selected(
    client: PrimaryKeyClient,
    catalog: Catalog,
    raw_videos: Path,
    work_dir: Path,
    filenames: list[str],
    *,
    project_id: str,
) -> dict:
    chosen = [e for e in catalog.visible() if e.filename in set(filenames)]
    if not chosen:
        raise RuntimeError("no episodes selected")
    zip_path = work_dir / "upload.zarr.zip"
    zip_episodes(raw_videos, chosen, zip_path, camera_serial=catalog.camera_serial)
    name = f"lumi-{chosen[0].stem}-{chosen[-1].stem}"
    result = client.upload_zip(
        zip_path,
        project_id=project_id,
        name=name,
        description=f"{len(chosen)} LUMI collector episodes",
    )
    zip_path.unlink(missing_ok=True)
    return result
