from __future__ import annotations

import logging
import socket
import threading
import time
from pathlib import Path

from lumi_collector.auth import PrimaryKeyClient, load_credentials
from lumi_collector.buttons import ButtonSource, GpioButtons
from lumi_collector.catalog import Catalog, load_catalog, mark_deleted, mark_uploaded, save_catalog
from lumi_collector.config import CollectorConfig, resolve_camera_serial
from lumi_collector.display import Hud, render
from lumi_collector.imu import open_imu
from lumi_collector.playback import ClipPlayer
from lumi_collector.power import PowerMonitor, PowerStatus, free_gb
from lumi_collector.recorder import MockCamera, Picamera2Camera, Recorder
from lumi_collector.setup_server import start_setup_server
from lumi_collector.state import Action, Event, Screen, UiState, handle
from lumi_collector.stream import NullStream, VideoStream
from lumi_collector.upload import upload_selected


def _local_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def _load_bmi_blob() -> bytes:
    path = Path(__file__).with_name("data").joinpath("bmi270_config.bin")
    return path.read_bytes() if path.is_file() else b""


class CollectorApp:
    def __init__(self, cfg: CollectorConfig, display, *, mock: bool = False):
        self.cfg = cfg
        self.mock = mock
        self.display = display
        self.serial = resolve_camera_serial(cfg)
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.raw_videos_dir.mkdir(parents=True, exist_ok=True)
        cfg.work_dir.mkdir(parents=True, exist_ok=True)
        self.catalog: Catalog = load_catalog(cfg.catalog_path, self.serial)
        self.catalog_lock = threading.Lock()
        self.state = UiState()
        self._dropped_status = ""
        self.buttons = ButtonSource(cfg.buttons)
        self.gpio = None if mock else GpioButtons(cfg.buttons)
        imu_cfg = cfg.imu
        if mock:
            from dataclasses import replace

            imu_cfg = replace(cfg.imu, driver="mock")
        self.imu = open_imu(imu_cfg, config_blob=_load_bmi_blob())
        camera = MockCamera(cfg) if mock else Picamera2Camera(cfg)
        self.recorder = Recorder(
            cfg,
            camera,
            self.imu,
            self.catalog,
            self.catalog_lock,
            self.serial,
            on_failed=self._seal_failed,
            on_dropped=self._frames_dropped,
        )
        self.power = PowerMonitor(cfg.power)
        self.player = ClipPlayer()
        self.stream = NullStream() if mock else VideoStream(cfg.video)
        self.preview = None
        self._stop = threading.Event()

    def run(self) -> None:
        if self.gpio is not None:
            self.gpio.open()
        self.power.open()
        server = start_setup_server(self.cfg)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self._refresh_account()
        try:
            while not self._stop.is_set() and self.state.screen is not Screen.SHUTDOWN:
                self.tick()
                time.sleep(0.05)
        finally:
            server.shutdown()
            if self.gpio is not None:
                self.gpio.close()
            self.player.stop()
            self.stream.stop()
            self.recorder.close()
            self.imu.close()
            self.display.close()

    def tick(self, pressed: dict[str, bool] | None = None) -> None:
        if pressed is None and self.gpio is not None:
            pressed = self.gpio.read()
        pressed = pressed or {}
        for event in self.buttons.poll(pressed):
            self._dispatch(event)
        recording = self.recorder.recording
        if self.recorder.check_camera() and recording:
            handle(self.state, Event.RECORD_SEALED, [e.filename for e in self.catalog.visible()])
            self.state.status = "camera stalled"
            self._dropped_status = ""
        if self.recorder.recording:
            self.state.rec_s = self.recorder.elapsed_s()
        if self.state.screen is Screen.PLAYBACK:
            frame = self.player.frame
            if frame is not None:
                self.preview = frame
        elif self.player.active:
            self.player.stop()
            self.preview = None
        if self.state.screen is Screen.IMU:
            self._read_imu()
        if self.state.screen is Screen.STREAM:
            self.state.status = self.stream.error
        self._draw()

    def _dispatch(self, event: Event) -> None:
        names = [e.filename for e in self.catalog.visible()]
        actions = handle(self.state, event, names)
        for action in actions:
            self._apply(action)

    def _apply(self, action: Action) -> None:
        if action is Action.START_RECORD:
            self.recorder.start()
        elif action is Action.STOP_RECORD:
            try:
                saved = self.recorder.stop()
            except Exception:
                logging.exception("stop recording failed")
                self.state.screen = Screen.HOME
                self.state.rec_s = 0.0
                self.state.status = "save failed"
            else:
                handle(
                    self.state,
                    Event.RECORD_SEALED,
                    [e.filename for e in self.catalog.visible()],
                )
                if not saved:
                    self.state.status = "camera stalled"
                elif self._dropped_status:
                    self.state.status = self._dropped_status
                    self._dropped_status = ""
        elif action is Action.DELETE:
            with self.catalog_lock:
                for name in list(self.state.pending_delete):
                    mark_deleted(self.catalog, name)
                    path = self.cfg.raw_videos_dir / name
                    path.unlink(missing_ok=True)
                    path.with_suffix(".imu.json").unlink(missing_ok=True)
                self.state.pending_delete = []
                save_catalog(self.cfg.catalog_path, self.catalog)
        elif action is Action.UPLOAD:
            threading.Thread(target=self._upload, daemon=True).start()
        elif action is Action.PLAY:
            names = [e.filename for e in self.catalog.visible()]
            if names:
                self.preview = None
                self.player.start(self.cfg.raw_videos_dir / names[self.state.cursor])
        elif action is Action.START_STREAM:
            # libcamera allows one client. Picamera2 stays open after a take;
            # close() is what releases it, and the next recording recreates it.
            if not self.recorder.recording:
                self.recorder.camera.close()
            self.stream.start()
        elif action is Action.STOP_STREAM:
            self.stream.stop()
            self.state.status = ""
        elif action is Action.SHUTDOWN:
            self._stop.set()
            self.stream.stop()
            self.recorder.close()
            self.power.shutdown()

    def _upload(self) -> None:
        creds = load_credentials(self.cfg.credentials_path)
        names = [e.filename for e in self.catalog.visible()]
        chosen = list(self.state.selected) or (
            [names[self.state.cursor]] if names else []
        )
        try:
            if creds is None or not creds.project_id:
                raise RuntimeError("sign in at setup page")
            client = PrimaryKeyClient(creds.api_url, creds)
            upload_selected(
                client,
                self.catalog,
                self.cfg.raw_videos_dir,
                self.cfg.work_dir,
                chosen,
                project_id=creds.project_id,
            )
            with self.catalog_lock:
                mark_uploaded(self.catalog, chosen)
                save_catalog(self.cfg.catalog_path, self.catalog)
            handle(self.state, Event.UPLOAD_DONE, names)
        except Exception as exc:
            self.state.status = str(exc)
            handle(self.state, Event.UPLOAD_ERROR, names)

    def _read_imu(self) -> None:
        try:
            sample = self.imu.read()
        except Exception:
            logging.exception("imu read failed")
            return
        if sample is None:
            return
        self.state.imu_acc = sample.acc_zxy
        self.state.imu_gyr = sample.gyr_zxy
        self.state.imu_temp = sample.temp_c

    def _frames_dropped(self, _filename: str, count: int) -> None:
        self._dropped_status = f"{count} frames dropped"

    def _seal_failed(self) -> None:
        if self.state.screen is not Screen.RECORDING:
            self.state.status = "save failed"

    def _refresh_account(self) -> None:
        creds = load_credentials(self.cfg.credentials_path)
        if creds is None:
            return
        self.state.username = creds.username
        self.state.project = creds.project_name

    def _draw(self) -> None:
        status = self.power.read() if not self.mock else PowerStatus(None, False)
        ip = _local_ip()
        hud = Hud(
            battery_pct=status.battery_pct,
            charging=status.charging,
            free_gb=free_gb(self.cfg.data_dir),
            rec_s=self.state.rec_s,
            play_s=self.player.position_s,
            setup_url=f"http://{ip}:{self.cfg.setup_port}",
            wifi=ip,
            stream_port=self.cfg.video.stream_port,
        )
        frame = render(
            self.state,
            self.catalog.visible(),
            hud,
            size=(self.cfg.display.width, self.cfg.display.height),
            preview=self.preview,
        )
        self.display.show(frame)
