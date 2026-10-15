from pathlib import Path

from lumi_collector.buttons import ButtonSource
from lumi_collector.catalog import Episode, empty_catalog
from lumi_collector.config import ButtonConfig
from lumi_collector.datasets import KEYBOARD_ROWS, MAX_NAME, Dataset
from lumi_collector.display import BG, Hud, loading_frame, render
from lumi_collector.state import STREAM_ROW, Action, Event, Screen, UiState, handle

DATASETS = ["cups", "default", "pnp"]


def names():
    return ["GX010001.MP4", "GX010002.MP4"]


def h(state, event, datasets=DATASETS):
    return handle(state, event, names(), datasets)


def opened():
    return UiState(screen=Screen.DATASET, dataset="cups")


def test_record_toggle():
    state = opened()
    assert h(state, Event.KEY1) == [Action.START_RECORD]
    assert state.screen is Screen.RECORDING
    assert h(state, Event.KEY1) == [Action.STOP_RECORD]
    h(state, Event.RECORD_SEALED)
    assert state.screen is Screen.DATASET
    h(state, Event.KEY1)
    assert h(state, Event.JOY_PRESS) == [Action.STOP_RECORD]
    h(state, Event.RECORD_SEALED)
    assert state.screen is Screen.DATASET
    assert h(state, Event.KEY1) == [Action.START_RECORD]
    assert state.screen is Screen.RECORDING


def test_delete_last_from_dataset_needs_confirm():
    state = opened()
    assert h(state, Event.KEY2) == []
    assert state.screen is Screen.CONFIRM_DELETE
    assert state.pending_delete == ["GX010002.MP4"]
    assert h(state, Event.JOY_LEFT) == []
    assert state.screen is Screen.DATASET
    assert state.pending_delete == []
    h(state, Event.JOY_DOWN)
    assert state.pending_delete == ["GX010002.MP4"]
    assert h(state, Event.JOY_PRESS) == []
    assert state.screen is Screen.CONFIRM_DELETE
    assert h(state, Event.KEY2) == [Action.DELETE]
    assert state.pending_delete == ["GX010002.MP4"]
    assert state.screen is Screen.DATASET


def test_dataset_menu():
    state = opened()
    assert h(state, Event.JOY_PRESS) == [Action.START_RECORD]
    assert state.screen is Screen.RECORDING
    assert h(state, Event.KEY3) == [Action.STOP_RECORD]

    state = opened()
    h(state, Event.JOY_UP)
    assert state.screen is Screen.LIBRARY
    assert state.cursor == 1

    state = opened()
    h(state, Event.JOY_LEFT)
    assert state.screen is Screen.HOME
    assert state.dataset == ""


def test_home_lists_datasets_and_opens_one():
    state = UiState()
    h(state, Event.JOY_DOWN)
    h(state, Event.JOY_DOWN)
    h(state, Event.JOY_DOWN)
    assert state.dataset_cursor == 2
    h(state, Event.JOY_UP)
    assert state.dataset_cursor == 1
    assert h(state, Event.JOY_PRESS) == [Action.OPEN_DATASET]
    assert state.screen is Screen.DATASET
    assert state.dataset == "default"
    h(state, Event.KEY3)
    assert state.screen is Screen.HOME
    assert state.dataset_cursor == 1


def test_home_with_no_datasets_does_not_open():
    state = UiState()
    assert h(state, Event.JOY_PRESS, []) == []
    assert h(state, Event.KEY2, []) == []
    assert state.screen is Screen.HOME


def test_home_settings_and_back():
    for event in (Event.JOY_LEFT, Event.KEY3):
        state = UiState()
        h(state, event)
        assert state.screen is Screen.SETTINGS
        h(state, Event.JOY_LEFT)
        assert state.screen is Screen.HOME


def test_home_multi_select_delete():
    state = UiState()
    h(state, Event.KEY1)
    h(state, Event.JOY_DOWN)
    h(state, Event.JOY_DOWN)
    h(state, Event.KEY1)
    assert state.dataset_selected == {"cups", "pnp"}
    h(state, Event.KEY2)
    assert state.screen is Screen.CONFIRM_DELETE
    assert state.pending_delete == ["cups", "pnp"]
    h(state, Event.KEY3)
    assert state.screen is Screen.HOME
    assert state.pending_delete == []
    h(state, Event.KEY2)
    assert h(state, Event.KEY2) == [Action.DELETE_DATASETS]
    assert state.screen is Screen.HOME
    assert state.dataset_selected == set()


def test_home_delete_without_selection_takes_the_cursor():
    state = UiState(dataset_cursor=1)
    h(state, Event.KEY2)
    assert state.pending_delete == ["default"]


def _type(state, ch):
    for r, row in enumerate(KEYBOARD_ROWS):
        if ch in row:
            state.key_row, state.key_col = r, row.index(ch)
    h(state, Event.JOY_PRESS)


def test_create_dataset_on_the_keyboard():
    state = UiState()
    h(state, Event.JOY_RIGHT)
    assert state.screen is Screen.NEW_DATASET
    assert (state.key_row, state.key_col) == (0, 0)
    for ch in "towels":
        _type(state, ch)
    _type(state, "x")
    h(state, Event.KEY2)
    assert state.typed == "towels"
    assert h(state, Event.KEY1) == [Action.CREATE_DATASET]
    assert state.screen is Screen.HOME


def test_create_rejects_empty_and_existing_names():
    state = UiState(screen=Screen.NEW_DATASET)
    assert h(state, Event.KEY1) == []
    assert state.status == "type a name"
    for ch in "cups":
        _type(state, ch)
    assert state.status == ""
    assert h(state, Event.KEY1, ["CUPS"]) == []
    assert state.screen is Screen.NEW_DATASET
    assert state.status == "exists"
    h(state, Event.KEY3)
    assert state.screen is Screen.HOME


def test_keyboard_wraps_and_caps_the_name():
    state = UiState(screen=Screen.NEW_DATASET)
    h(state, Event.JOY_LEFT)
    assert state.key_col == len(KEYBOARD_ROWS[0]) - 1
    h(state, Event.JOY_RIGHT)
    assert state.key_col == 0
    h(state, Event.JOY_UP)
    assert state.key_row == len(KEYBOARD_ROWS) - 1
    h(state, Event.JOY_DOWN)
    assert state.key_row == 0
    state.key_col = 9
    h(state, Event.JOY_UP)
    assert state.key_col == len(KEYBOARD_ROWS[-1]) - 1
    for _ in range(MAX_NAME + 3):
        h(state, Event.JOY_PRESS)
    assert len(state.typed) == MAX_NAME
    assert h(state, Event.KEY3_LONG) == []
    assert state.screen is Screen.HOME


def test_library_select_enter_and_upload():
    state = opened()
    h(state, Event.JOY_UP)
    assert state.screen is Screen.LIBRARY
    assert h(state, Event.KEY1) == []
    assert state.screen is Screen.LIBRARY
    assert "GX010002.MP4" in state.selected
    h(state, Event.JOY_PRESS)
    assert state.screen is Screen.EPISODE
    assert h(state, Event.JOY_PRESS) == [Action.PLAY]
    assert state.screen is Screen.PLAYBACK
    h(state, Event.KEY3)
    assert state.screen is Screen.EPISODE
    assert h(state, Event.JOY_UP) == [Action.UPLOAD]
    assert state.screen is Screen.UPLOAD
    h(state, Event.JOY_LEFT)
    assert state.screen is Screen.LIBRARY


def test_library_delete_and_back():
    state = opened()
    h(state, Event.JOY_UP)
    h(state, Event.KEY2)
    assert state.screen is Screen.CONFIRM_DELETE
    assert state.pending_delete == ["GX010002.MP4"]
    h(state, Event.KEY3)
    assert state.screen is Screen.LIBRARY
    h(state, Event.KEY3)
    assert state.screen is Screen.DATASET


def test_long_press_is_shutdown():
    for state in (UiState(), opened()):
        assert h(state, Event.KEY3_LONG) == [Action.SHUTDOWN]
        assert state.screen is Screen.SHUTDOWN


def test_long_press_off_home_is_back():
    state = opened()
    h(state, Event.JOY_UP)
    assert state.screen is Screen.LIBRARY
    assert h(state, Event.KEY3_LONG) == []
    assert state.screen is Screen.DATASET


def test_settings_opens_stream():
    state = UiState()
    h(state, Event.KEY3)
    for _ in range(STREAM_ROW):
        h(state, Event.JOY_DOWN)
    assert state.settings_cursor == STREAM_ROW
    h(state, Event.JOY_DOWN)
    assert state.settings_cursor == STREAM_ROW
    assert h(state, Event.JOY_PRESS) == [Action.START_STREAM]
    assert state.screen is Screen.STREAM
    assert h(state, Event.JOY_LEFT) == [Action.STOP_STREAM]
    assert state.screen is Screen.SETTINGS
    assert h(state, Event.JOY_RIGHT) == [Action.START_STREAM]
    assert h(state, Event.KEY3) == [Action.STOP_STREAM]
    assert state.screen is Screen.SETTINGS


def test_settings_opens_live_imu():
    state = UiState()
    h(state, Event.KEY3)
    assert state.screen is Screen.SETTINGS
    h(state, Event.JOY_DOWN)
    h(state, Event.JOY_DOWN)
    assert state.settings_cursor == 2
    assert h(state, Event.JOY_RIGHT) == []
    assert state.screen is Screen.IMU
    h(state, Event.KEY3)
    assert state.screen is Screen.SETTINGS


def test_button_long_press_edge():
    src = ButtonSource(ButtonConfig(long_press_s=2.0))
    assert src.poll({"key3": True}, now=0.0) == []
    assert src.poll({"key3": True}, now=2.1)[0] is Event.KEY3_LONG
    assert src.poll({"key3": False}, now=2.2) == []
    assert src.poll({"key1": True}, now=3.0)[0] is Event.KEY1


def test_loading_frame_replaces_white():
    img = loading_frame()
    assert img.size == (240, 240)
    assert img.getpixel((0, 0)) == BG
    colors = {color for _, color in img.getcolors(maxcolors=img.width * img.height)}
    assert len(colors) > 1
    small = loading_frame(size=(128, 128))
    assert small.size == (128, 128)
    assert small.getpixel((0, 0)) != (255, 255, 255)


def _hud(**kwargs):
    return Hud(**{"battery_pct": 50, "charging": False, "free_gb": 1, "rec_s": 0, "setup_url": "", "wifi": "", **kwargs})


def test_stream_screen_renders():
    state = UiState(screen=Screen.STREAM, status="rpicam-vid exited 1")
    assert render(state, [], [], _hud()).size == (240, 240)
    settings = UiState(screen=Screen.SETTINGS, settings_cursor=STREAM_ROW)
    assert render(settings, [], [], _hud()).size == (240, 240)


def test_every_dataset_screen_renders():
    datasets = [Dataset(name, Path("/tmp") / name, empty_catalog("X")) for name in [*DATASETS, "a" * MAX_NAME]]
    episodes = [Episode("GX010001.MP4", "2026-09-21T12:00:00.000000Z", 12.0, 1_000_000)]
    states = (
        UiState(),
        UiState(dataset_cursor=3, dataset_selected={"cups"}),
        UiState(screen=Screen.NEW_DATASET, typed="a" * MAX_NAME, status="exists"),
        UiState(screen=Screen.DATASET, dataset="a" * MAX_NAME),
        UiState(screen=Screen.CONFIRM_DELETE, confirm_from=Screen.HOME, pending_delete=["cups"]),
    )
    for state in states:
        assert render(state, datasets, episodes, _hud()).size == (240, 240)
        assert render(state, [], [], _hud(battery_pct=None), size=(128, 128)).size == (128, 128)
