from __future__ import annotations

from pathlib import Path

from lumi_collector import archive
from lumi_collector.auth import SolarianBaseClient
from lumi_collector.datasets import Dataset


def upload_selected(
    client: SolarianBaseClient,
    dataset: Dataset,
    work_dir: Path,
    filenames: list[str],
    *,
    project_id: str,
) -> dict:
    chosen = [e for e in dataset.catalog.visible() if e.filename in set(filenames)]
    if not chosen:
        raise RuntimeError("no episodes selected")
    name = f"lumi-{dataset.name}-{chosen[0].stem}-{chosen[-1].stem}"
    zip_path = archive.pack(
        dataset, work_dir / f"{name}{archive.SUFFIX}", [e.filename for e in chosen]
    )
    result = client.upload_zip(
        zip_path,
        project_id=project_id,
        name=name,
        description=f"{len(chosen)} LUMI collector episodes",
    )
    zip_path.unlink(missing_ok=True)
    return result
