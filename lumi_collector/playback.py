from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from PIL import Image


class ClipPlayer:
    """Decode a clip on a background thread and keep the latest frame.

    1080p software decode on a Zero 2 is a bit slower than realtime, so frames
    are shown as they finish and the clip loops until ``stop``.
    """

    def __init__(self):
        self.frame: Image.Image | None = None
        self.position_s = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._gen = 0

    @property
    def active(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self, path: Path) -> None:
        self.stop()
        self._stop = threading.Event()
        self._gen += 1
        gen = self._gen
        self.frame = None
        self.position_s = 0.0
        self._thread = threading.Thread(
            target=self._loop, args=(path, gen, self._stop), daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._gen += 1

    def _loop(self, path: Path, gen: int, stop: threading.Event) -> None:
        while not stop.is_set() and gen == self._gen:
            if not self._once(path, gen, stop):
                return

    def _once(self, path: Path, gen: int, stop: threading.Event) -> bool:
        try:
            import av
        except Exception:
            logging.exception("playback import failed")
            return False
        try:
            with av.open(str(path)) as container:
                stream = container.streams.video[0]
                stream.thread_type = "AUTO"
                if stream.codec_context is not None:
                    stream.codec_context.thread_count = 4
                start = time.monotonic()
                origin = None
                for frame in container.decode(stream):
                    if stop.is_set() or gen != self._gen:
                        return False
                    stamp = frame.time if frame.time is not None else 0.0
                    if origin is None:
                        origin = stamp
                    image = frame.reformat(width=160, height=120, format="rgb24").to_image()
                    if gen == self._gen:
                        self.position_s = max(0.0, stamp - origin)
                        self.frame = image
                    delay = start + (stamp - origin) - time.monotonic()
                    if delay > 0 and stop.wait(delay):
                        return False
            return not stop.is_set()
        except Exception:
            logging.exception("playback failed")
            return False


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
