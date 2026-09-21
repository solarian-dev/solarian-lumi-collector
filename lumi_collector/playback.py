from __future__ import annotations

from pathlib import Path

from PIL import Image


def first_frame(mp4: Path, size: tuple[int, int] = (240, 180)) -> Image.Image | None:
    try:
        import av
    except Exception:
        return None
    if not mp4.is_file():
        return None
    try:
        with av.open(str(mp4)) as container:
            stream = container.streams.video[0]
            stream.thread_type = "AUTO"
            for frame in container.decode(stream):
                arr = frame.to_ndarray(format="rgb24")
                img = Image.fromarray(arr)
                return img.resize(size)
    except Exception:
        return None
    return None
