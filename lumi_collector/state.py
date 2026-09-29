"""UI state machine. No GPIO, no I/O — the app applies the returned actions."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Screen(str, Enum):
    HOME = "home"
    RECORDING = "recording"
    LIBRARY = "library"
    EPISODE = "episode"
    CONFIRM_DELETE = "confirm_delete"
    UPLOAD = "upload"
    SETTINGS = "settings"
    PLAYBACK = "playback"
    IMU = "imu"
    SHUTDOWN = "shutdown"


class Event(str, Enum):
    KEY1 = "key1"
    KEY2 = "key2"
    KEY3 = "key3"
    KEY3_LONG = "key3_long"
    JOY_UP = "joy_up"
    JOY_DOWN = "joy_down"
    JOY_LEFT = "joy_left"
    JOY_RIGHT = "joy_right"
    JOY_PRESS = "joy_press"
    RECORD_SEALED = "record_sealed"
    UPLOAD_DONE = "upload_done"
    UPLOAD_ERROR = "upload_error"


class Action(str, Enum):
    START_RECORD = "start_record"
    STOP_RECORD = "stop_record"
    DELETE = "delete"
    UPLOAD = "upload"
    PLAY = "play"
    SHUTDOWN = "shutdown"


@dataclass
class UiState:
    screen: Screen = Screen.HOME
    cursor: int = 0
    selected: set[str] = field(default_factory=set)
    pending_delete: list[str] = field(default_factory=list)
    status: str = ""
    rec_s: float = 0.0
    username: str = ""
    project: str = ""
    settings_cursor: int = 0
    confirm_from: Screen = Screen.HOME
    imu_acc: tuple[float, float, float] | None = None
    imu_gyr: tuple[float, float, float] | None = None
    imu_temp: float | None = None

    def clamp(self, n_items: int) -> None:
        if n_items <= 0:
            self.cursor = 0
            return
        self.cursor = max(0, min(self.cursor, n_items - 1))


def _ask_delete(state: UiState, targets: list[str]) -> None:
    state.confirm_from = state.screen
    state.pending_delete = list(targets)
    state.screen = Screen.CONFIRM_DELETE


def handle(state: UiState, event: Event, filenames: list[str]) -> list[Action]:
    """Advance UI. ``filenames`` is the visible catalog, oldest first."""
    n = len(filenames)
    state.clamp(n)

    if event is Event.KEY3_LONG:
        if state.screen is Screen.HOME:
            state.screen = Screen.SHUTDOWN
            return [Action.SHUTDOWN]
        event = Event.KEY3

    if state.screen is Screen.RECORDING:
        if event in {Event.KEY1, Event.JOY_PRESS, Event.KEY3, Event.JOY_LEFT}:
            return [Action.STOP_RECORD]
        if event is Event.RECORD_SEALED:
            state.screen = Screen.HOME
            state.rec_s = 0.0
            state.status = "saved"
        return []

    if state.screen is Screen.HOME:
        if event is Event.KEY1 or event is Event.JOY_PRESS:
            state.screen = Screen.RECORDING
            state.rec_s = 0.0
            state.status = ""
            return [Action.START_RECORD]
        if event in {Event.KEY2, Event.JOY_DOWN} and filenames:
            _ask_delete(state, [filenames[-1]])
        elif event is Event.JOY_UP:
            state.screen = Screen.LIBRARY
            state.cursor = max(0, n - 1)
        elif event in {Event.KEY3, Event.JOY_LEFT}:
            state.screen = Screen.SETTINGS
        return []

    if state.screen is Screen.LIBRARY:
        if event is Event.JOY_UP:
            state.cursor = max(0, state.cursor - 1)
        elif event is Event.JOY_DOWN:
            state.cursor = min(max(n - 1, 0), state.cursor + 1)
        elif event is Event.KEY1 and filenames:
            name = filenames[state.cursor]
            if name in state.selected:
                state.selected.discard(name)
            else:
                state.selected.add(name)
        elif event is Event.JOY_PRESS and filenames:
            state.screen = Screen.EPISODE
        elif event is Event.KEY2:
            targets = sorted(state.selected) if state.selected else (
                [filenames[state.cursor]] if filenames else []
            )
            if targets:
                _ask_delete(state, targets)
        elif event in {Event.JOY_LEFT, Event.KEY3}:
            state.screen = Screen.HOME
        return []

    if state.screen is Screen.EPISODE:
        if event in {Event.JOY_LEFT, Event.KEY3}:
            state.screen = Screen.LIBRARY
        elif event is Event.KEY2 and filenames:
            _ask_delete(state, [filenames[state.cursor]])
        elif event is Event.JOY_PRESS and filenames:
            state.screen = Screen.PLAYBACK
            return [Action.PLAY]
        elif event is Event.JOY_UP and filenames:
            state.screen = Screen.UPLOAD
            return [Action.UPLOAD]
        return []

    if state.screen is Screen.CONFIRM_DELETE:
        if event is Event.KEY2:
            state.selected.difference_update(state.pending_delete)
            # CollectorApp reads pending_delete to unlink the files.
            state.screen = Screen.HOME if state.confirm_from is Screen.HOME else Screen.LIBRARY
            state.status = "deleted"
            return [Action.DELETE]
        if event in {Event.KEY3, Event.JOY_LEFT}:
            state.pending_delete = []
            state.screen = state.confirm_from
        return []

    if state.screen is Screen.UPLOAD:
        if event is Event.UPLOAD_DONE:
            state.status = "uploaded"
            state.screen = Screen.LIBRARY
            state.selected.clear()
        elif event is Event.UPLOAD_ERROR:
            state.status = "upload failed"
            state.screen = Screen.LIBRARY
        elif event in {Event.KEY3, Event.JOY_LEFT}:
            state.screen = Screen.LIBRARY
        return []

    if state.screen is Screen.PLAYBACK:
        if event in {Event.KEY3, Event.JOY_LEFT, Event.JOY_PRESS}:
            state.screen = Screen.EPISODE
        return []

    if state.screen is Screen.SETTINGS:
        if event is Event.JOY_UP:
            state.settings_cursor = max(0, state.settings_cursor - 1)
        elif event is Event.JOY_DOWN:
            state.settings_cursor = min(2, state.settings_cursor + 1)
        elif event in {Event.JOY_RIGHT, Event.JOY_PRESS} and state.settings_cursor == 2:
            state.screen = Screen.IMU
        elif event is Event.KEY3 or event is Event.JOY_LEFT:
            state.screen = Screen.HOME
        return []

    if state.screen is Screen.IMU:
        if event is Event.KEY3 or event is Event.JOY_LEFT:
            state.screen = Screen.SETTINGS
        return []

    return []
