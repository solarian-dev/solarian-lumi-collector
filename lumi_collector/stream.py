"""Live H.264 listener for checking the lens from another machine.

``rpicam-vid --listen`` owns the camera until the stream screen closes.
``ffplay_argv`` is what ``solarian lumi --stream`` runs on the workstation.
"""

from __future__ import annotations

import logging
import subprocess
import threading

from lumi_collector.config import VideoConfig

DEFAULT_STREAM_HOST = "solarian-lumi.local"
DEFAULT_STREAM_PORT = 8888


def rpicam_vid_argv(video: VideoConfig) -> list[str]:
    return [
        "rpicam-vid",
        "-t", "0",
        "-n",
        "--listen",
        "--inline",
        "--tuning-file", video.tuning_file,
        "--mode", f"{video.sensor_width}:{video.sensor_height}",
        "--roi", "0,0,1,1",
        "--width", str(video.width),
        "--height", str(video.height),
        "--framerate", str(video.fps),
        "-o", f"tcp://0.0.0.0:{video.stream_port}",
    ]


def ffplay_argv(host: str = DEFAULT_STREAM_HOST, port: int = DEFAULT_STREAM_PORT) -> list[str]:
    return [
        "ffplay",
        "-fflags", "nobuffer",
        "-flags", "low_delay",
        "-f", "h264",
        "-vf", "scale=in_range=tv:out_range=pc",
        f"tcp://{host}:{port}",
    ]


class VideoStream:
    """Restart ``rpicam-vid`` while the stream screen is open.

    ``--listen`` ends when the player disconnects. Restarting lets the next
    ``ffplay`` connect. A fast exit waits briefly so a missing camera does not
    spin.
    """

    def __init__(self, video: VideoConfig, *, popen=subprocess.Popen):
        self._argv = rpicam_vid_argv(video)
        self._popen = popen
        self._proc: subprocess.Popen | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.error = ""

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self.error = ""
        self._thread = threading.Thread(target=self._run, name="lumi-stream", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._terminate()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2)
        self._thread = None

    def _terminate(self) -> None:
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            proc.kill()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                proc = self._popen(self._argv, stdout=subprocess.DEVNULL)
            except FileNotFoundError:
                self.error = "rpicam-vid missing"
                return
            self._proc = proc
            self.error = ""
            if self._stop.is_set():
                self._terminate()
                return
            code = proc.wait()
            self._proc = None
            if self._stop.is_set():
                return
            if code != 0:
                self.error = f"rpicam-vid exited {code}"
                logging.warning("rpicam-vid exited %s", code)
            else:
                self.error = ""
            if self._stop.wait(0.5):
                return


class NullStream:
    """Stand-in used by ``--mock`` so tests never spawn ``rpicam-vid``."""

    error = ""

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None
