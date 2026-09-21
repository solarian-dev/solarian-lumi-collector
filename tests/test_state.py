from lumi_collector.buttons import ButtonSource
from lumi_collector.config import ButtonConfig
from lumi_collector.display import Hud, render
from lumi_collector.state import Action, Event, Screen, UiState, handle


def names():
    return ["GX010001.MP4", "GX010002.MP4"]


def test_record_toggle():
    state = UiState()
    assert handle(state, Event.KEY1, names()) == [Action.START_RECORD]
    assert state.screen is Screen.RECORDING
    assert handle(state, Event.KEY1, names()) == [Action.STOP_RECORD]
    handle(state, Event.RECORD_SEALED, names())
    assert state.screen is Screen.HOME


def test_delete_last_from_home_needs_confirm():
    state = UiState()
    assert handle(state, Event.KEY2, names()) == []
    assert state.screen is Screen.CONFIRM_DELETE
    assert state.pending_delete == ["GX010002.MP4"]
    assert handle(state, Event.JOY_PRESS, names()) == [Action.DELETE]


def test_library_select_and_upload():
    state = UiState()
    handle(state, Event.JOY_PRESS, names())
    assert state.screen is Screen.LIBRARY
    handle(state, Event.JOY_PRESS, names())
    assert "GX010002.MP4" in state.selected
    handle(state, Event.JOY_RIGHT, names())
    assert handle(state, Event.JOY_PRESS, names()) == [Action.UPLOAD]
    assert state.screen is Screen.UPLOAD


def test_long_press_is_shutdown():
    state = UiState()
    assert handle(state, Event.KEY3_LONG, names()) == [Action.SHUTDOWN]
    assert state.screen is Screen.SHUTDOWN


def test_button_long_press_edge():
    src = ButtonSource(ButtonConfig(long_press_s=2.0))
    assert src.poll({"key3": True}, now=0.0) == []
    assert src.poll({"key3": True}, now=2.1)[0] is Event.KEY3_LONG
    assert src.poll({"key3": False}, now=2.2) == []
    assert src.poll({"key1": True}, now=3.0)[0] is Event.KEY1


def test_home_renders():
    from lumi_collector.catalog import Episode

    img = render(
        UiState(),
        [Episode("GX010001.MP4", "2026-09-21T12:00:00.000000Z", 12.0, 1_000_000)],
        Hud(battery_pct=80, charging=False, free_gb=12.5, rec_s=0, setup_url="http://x", wifi="1.2.3.4"),
    )
    assert img.size == (240, 240)
