"""UI state machine. No GPIO, no I/O — the app applies the returned actions."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from lumi_collector.datasets import KEYBOARD_ROWS, MAX_NAME


class Screen(str, Enum):
    HOME = "home"
    DATASET = "dataset"
    NEW_DATASET = "new_dataset"
    RECORDING = "recording"
    LIBRARY = "library"
    EPISODE = "episode"
    CONFIRM_DELETE = "confirm_delete"
    UPLOAD = "upload"
    SETTINGS = "settings"
    PLAYBACK = "playback"
    IMU = "imu"
    STREAM = "stream"
    SHUTDOWN = "shutdown"


IMU_ROW = 2
STREAM_ROW = 3


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
    START_STREAM = "start_stream"
    STOP_STREAM = "stop_stream"
    OPEN_DATASET = "open_dataset"
    CREATE_DATASET = "create_dataset"
    DELETE_DATASETS = "delete_datasets"
    SHUTDOWN = "shutdown"


@dataclass
class UiState:
    screen: Screen = Screen.HOME
    cursor: int = 0
    selected: set[str] = field(default_factory=set)
    # Episode filenames, or dataset names when confirm_from is HOME.
    pending_delete: list[str] = field(default_factory=list)
    dataset: str = ""
    dataset_cursor: int = 0
    dataset_selected: set[str] = field(default_factory=set)
    typed: str = ""
    key_row: int = 0
    key_col: int = 0
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


def handle(state: UiState, event: Event, filenames: list[str], datasets: list[str]) -> list[Action]:
    """Advance UI. ``filenames`` is the open dataset's visible catalog, oldest first;
    ``datasets`` the dataset names in list order.
    """
    n = len(filenames)
    state.clamp(n)
    state.dataset_cursor = max(0, min(state.dataset_cursor, len(datasets) - 1))

    if event is Event.KEY3_LONG:
        if state.screen in {Screen.HOME, Screen.DATASET}:
            state.screen = Screen.SHUTDOWN
            return [Action.SHUTDOWN]
        event = Event.KEY3

    if state.screen is Screen.RECORDING:
        if event in {Event.KEY1, Event.JOY_PRESS, Event.KEY3, Event.JOY_LEFT}:
            return [Action.STOP_RECORD]
        if event is Event.RECORD_SEALED:
            state.screen = Screen.DATASET
            state.rec_s = 0.0
            state.status = "saved"
        return []

    if state.screen is Screen.HOME:
        if event is Event.JOY_UP:
            state.dataset_cursor = max(0, state.dataset_cursor - 1)
        elif event is Event.JOY_DOWN:
            state.dataset_cursor = min(max(len(datasets) - 1, 0), state.dataset_cursor + 1)
        elif event is Event.KEY1 and datasets:
            name = datasets[state.dataset_cursor]
            if name in state.dataset_selected:
                state.dataset_selected.discard(name)
            else:
                state.dataset_selected.add(name)
        elif event is Event.KEY2 and datasets:
            targets = sorted(state.dataset_selected) or [datasets[state.dataset_cursor]]
            _ask_delete(state, targets)
        elif event is Event.JOY_PRESS and datasets:
            state.dataset = datasets[state.dataset_cursor]
            state.screen = Screen.DATASET
            state.cursor = 0
            state.selected.clear()
            state.status = ""
            return [Action.OPEN_DATASET]
        elif event is Event.JOY_RIGHT:
            state.screen = Screen.NEW_DATASET
            state.typed = ""
            state.key_row = state.key_col = 0
            state.status = ""
        elif event in {Event.KEY3, Event.JOY_LEFT}:
            state.status = ""
            state.screen = Screen.SETTINGS
        return []

    if state.screen is Screen.NEW_DATASET:
        rows = len(KEYBOARD_ROWS)
        if event in {Event.JOY_UP, Event.JOY_DOWN}:
            state.key_row = (state.key_row + (-1 if event is Event.JOY_UP else 1)) % rows
            state.key_col = min(state.key_col, len(KEYBOARD_ROWS[state.key_row]) - 1)
        elif event in {Event.JOY_LEFT, Event.JOY_RIGHT}:
            width = len(KEYBOARD_ROWS[state.key_row])
            state.key_col = (state.key_col + (-1 if event is Event.JOY_LEFT else 1)) % width
        elif event is Event.JOY_PRESS:
            if len(state.typed) < MAX_NAME:
                state.typed += KEYBOARD_ROWS[state.key_row][state.key_col]
            state.status = ""
        elif event is Event.KEY2:
            state.typed = state.typed[:-1]
            state.status = ""
        elif event is Event.KEY1:
            # Mac and Windows see the folders over Samba, where case does not tell names apart.
            if not state.typed:
                state.status = "type a name"
            elif state.typed in {name.lower() for name in datasets}:
                state.status = "exists"
            else:
                state.screen = Screen.HOME
                state.status = ""
                return [Action.CREATE_DATASET]
        elif event is Event.KEY3:
            state.screen = Screen.HOME
            state.status = ""
        return []

    if state.screen is Screen.DATASET:
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
            state.screen = Screen.HOME
            state.dataset = ""
            state.status = ""
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
            state.screen = Screen.DATASET
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
            # CollectorApp reads pending_delete to remove the files.
            state.status = "deleted"
            if state.confirm_from is Screen.HOME:
                state.dataset_selected.difference_update(state.pending_delete)
                state.screen = Screen.HOME
                return [Action.DELETE_DATASETS]
            state.selected.difference_update(state.pending_delete)
            state.screen = Screen.DATASET if state.confirm_from is Screen.DATASET else Screen.LIBRARY
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
            state.settings_cursor = min(STREAM_ROW, state.settings_cursor + 1)
        elif event in {Event.JOY_RIGHT, Event.JOY_PRESS} and state.settings_cursor == IMU_ROW:
            state.screen = Screen.IMU
        elif event in {Event.JOY_RIGHT, Event.JOY_PRESS} and state.settings_cursor == STREAM_ROW:
            state.status = ""
            state.screen = Screen.STREAM
            return [Action.START_STREAM]
        elif event is Event.KEY3 or event is Event.JOY_LEFT:
            state.screen = Screen.HOME
        return []

    if state.screen is Screen.IMU:
        if event is Event.KEY3 or event is Event.JOY_LEFT:
            state.screen = Screen.SETTINGS
        return []

    if state.screen is Screen.STREAM:
        if event is Event.KEY3 or event is Event.JOY_LEFT:
            state.screen = Screen.SETTINGS
            return [Action.STOP_STREAM]
        return []

    return []
