import subprocess
import threading
import time

from lumi_collector.config import VideoConfig
from lumi_collector.stream import VideoStream, ffplay_argv, rpicam_vid_argv


def test_rpicam_vid_argv_matches_lens_check():
    assert rpicam_vid_argv(VideoConfig()) == [
        "rpicam-vid",
        "-t", "0",
        "-n",
        "--listen",
        "--inline",
        "--tuning-file", "/home/pi/imx477_fisheye.json",
        "--mode", "2028:1520",
        "--roi", "0,0,1,1",
        "--width", "1440",
        "--height", "1080",
        "--framerate", "30",
        "-o", "tcp://0.0.0.0:8888",
    ]


def test_ffplay_argv():
    assert ffplay_argv() == [
        "ffplay",
        "-fflags", "nobuffer",
        "-flags", "low_delay",
        "-f", "h264",
        "-vf", "scale=in_range=tv:out_range=pc",
        "tcp://solarian-lumi.local:8888",
    ]
    assert ffplay_argv("lumi-collector.local", 9000)[-1] == "tcp://lumi-collector.local:9000"


class _Proc:
    def __init__(self):
        self.returncode = None
        self.terminated = False
        self._done = threading.Event()

    def poll(self):
        return self.returncode if self._done.is_set() else None

    def wait(self, timeout=None):
        if not self._done.wait(timeout):
            raise subprocess.TimeoutExpired("rpicam-vid", timeout)
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15
        self._done.set()

    def kill(self):
        self.terminate()


def test_supervisor_stops_the_listener():
    procs: list[_Proc] = []

    def popen(argv, **kwargs):
        assert argv[0] == "rpicam-vid"
        proc = _Proc()
        procs.append(proc)
        return proc

    stream = VideoStream(VideoConfig(), popen=popen)
    stream.start()
    assert _wait_until(lambda: len(procs) == 1)
    stream.stop()
    assert procs[0].terminated
    assert stream._thread is None


def test_supervisor_restarts_after_exit():
    procs: list[_Proc] = []
    second = threading.Event()

    class Exits(_Proc):
        def wait(self, timeout=None):
            if len(procs) == 1 and not second.is_set():
                self.returncode = 1
                self._done.set()
            return super().wait(timeout)

    def popen(argv, **kwargs):
        proc = Exits()
        procs.append(proc)
        if len(procs) == 2:
            second.set()
        return proc

    stream = VideoStream(VideoConfig(), popen=popen)
    stream.start()
    assert _wait_until(lambda: stream.error == "rpicam-vid exited 1")
    assert second.wait(2)
    assert len(procs) >= 2
    stream.stop()
    assert procs[-1].terminated


def test_missing_rpicam_vid():
    def popen(argv, **kwargs):
        raise FileNotFoundError(argv[0])

    stream = VideoStream(VideoConfig(), popen=popen)
    stream.start()
    stream._thread.join(1)
    assert stream.error == "rpicam-vid missing"
    stream.stop()


def _wait_until(ready, timeout: float = 2) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ready():
            return True
        time.sleep(0.01)
    return False
