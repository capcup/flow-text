"""Tests der Menüleisten-Oberfläche mit einem rumps-Fake.

rumps braucht Cocoa und läuft nur auf macOS. Der Fake bildet die drei
benutzten Bausteine nach (``App``, ``MenuItem``, ``Timer``) — damit ist die
gesamte Menülogik hier prüfbar.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any, ClassVar

import pytest

from flowtext import menubar
from flowtext.app import FlowTextApp
from flowtext.config import Config
from flowtext.state import State

# --------------------------------------------------------------------- Fakes


class FakeMenuItem:
    def __init__(self, title: str, callback: Any = None) -> None:
        self.title = title
        self.callback = callback

    def set_callback(self, callback: Any) -> None:
        self.callback = callback


class FakeRumpsApp:
    def __init__(self, name: str, title: str = "", quit_button: Any = None) -> None:
        self.name = name
        self.title = title
        self.menu: list[Any] = []
        self.ran = False

    def run(self) -> None:
        self.ran = True


class FakeTimer:
    instances: ClassVar[list[FakeTimer]] = []

    def __init__(self, callback: Any, interval: float) -> None:
        self.callback = callback
        self.interval = interval
        self.started = False
        FakeTimer.instances.append(self)

    def start(self) -> None:
        self.started = True

    def tick(self) -> None:
        self.callback(self)


class FakeRecorder:
    def __init__(self) -> None:
        self.is_recording = False
        self.elapsed = 0.0
        self.aborted = False

    def start(self) -> None:
        self.is_recording = True

    def stop(self) -> Any:
        self.is_recording = False
        return None

    def abort(self) -> None:
        self.aborted = True

    def save(self, recording: Any, path: Path) -> Path:
        return path


class FakeHotkey:
    dropped_triggers = 0
    slowest_callback_ms = 0.1

    def describe(self) -> str:
        return "Doppel-Tap auf cmd"

    def start(self) -> None: ...

    def stop(self, timeout: float = 2.0) -> None: ...


class FakePipeline:
    def warmup(self) -> None: ...

    def shutdown(self) -> None: ...

    def run(self, path: Path) -> str:
        return ""


@pytest.fixture
def quitted() -> list[bool]:
    return []


@pytest.fixture
def rumps_module(monkeypatch, quitted):
    FakeTimer.instances.clear()
    module = types.ModuleType("rumps")
    module.App = FakeRumpsApp  # type: ignore[attr-defined]
    module.MenuItem = FakeMenuItem  # type: ignore[attr-defined]
    module.Timer = FakeTimer  # type: ignore[attr-defined]
    module.quit_application = lambda: quitted.append(True)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "rumps", module)
    return module


@pytest.fixture
def menu(rumps_module, monkeypatch, tmp_path):
    from flowtext import app as app_module

    monkeypatch.setattr(app_module, "temp_dir", lambda: tmp_path / "tmp")
    cfg = Config()
    cfg.feedback.sound = False
    flow = FlowTextApp(
        cfg,
        recorder=FakeRecorder(),  # type: ignore[arg-type]
        hotkey=FakeHotkey(),  # type: ignore[arg-type]
        pipeline=FakePipeline(),  # type: ignore[arg-type]
    )
    return menubar.MenubarApp(cfg, flow)


def _run_ui(menu) -> None:
    """``menu.run()`` für Tests, die danach ticken.

    Der echte ``rumps.run()`` blockiert bis zum Beenden. Der Fake kehrt
    sofort zurück, wodurch das ``finally: shutdown()`` in ``MenubarApp.run``
    greift und das Stop-Event setzt. Ohne Zurücksetzen würde jeder folgende
    Tick die App sofort beenden.
    """
    menu.run()
    menu.app._stop.clear()


# ------------------------------------------------------------------ Anzeige


class TestDisplay:
    @pytest.mark.parametrize(
        ("state", "icon", "label"),
        [
            (State.IDLE, "🎙", "Bereit"),
            (State.RECORDING, "🔴", "Nimmt auf …"),
            (State.PROCESSING, "⏳", "Verarbeitet …"),
            (State.ERROR, "⚠️", "Fehler — siehe Log"),
        ],
    )
    def test_every_state_has_its_own_icon(self, state: State, icon: str, label: str) -> None:
        assert menubar.icon_for(state, paused=False) == icon
        assert menubar.label_for(state, paused=False) == label

    def test_pause_overrides_every_state(self) -> None:
        for state in State:
            assert menubar.icon_for(state, paused=True) == menubar.PAUSED_ICON
            assert menubar.label_for(state, paused=True) == "Pausiert"

    def test_icons_are_distinct(self) -> None:
        """Sonst sieht man den Zustandswechsel nicht."""
        assert len(set(menubar.ICONS.values())) == len(menubar.ICONS)


# -------------------------------------------------------------------- Ablauf


class TestMenubarApp:
    def test_run_starts_the_app_and_the_timer(self, menu) -> None:
        menu.run()
        assert menu._ui.ran
        assert FakeTimer.instances[-1].started
        assert FakeTimer.instances[-1].interval == menubar.TICK_SECONDS

    def test_menu_shows_the_trigger(self, menu) -> None:
        menu.run()
        titles = [item.title for item in menu._ui.menu if item is not None]
        assert any("Doppel-Tap auf cmd" in t for t in titles)

    def test_status_items_are_not_clickable(self, menu) -> None:
        menu.run()
        assert menu._items["status"].callback is None

    def test_tick_transfers_state_to_the_ui(self, menu) -> None:
        """Regression: der Zustand kommt aus dem Controller-Thread und darf
        Cocoa nur über den Timer im Hauptthread erreichen."""
        _run_ui(menu)
        menu.app.state.transition(State.RECORDING)
        assert menu._ui.title == "🎙"  # noch nicht übertragen
        FakeTimer.instances[-1].tick()
        assert menu._ui.title == "🔴"
        assert menu._items["status"].title == "Nimmt auf …"

    def test_pause_toggles_and_relabels(self, menu) -> None:
        _run_ui(menu)
        menu._toggle_pause(None)
        assert menu.app.paused
        FakeTimer.instances[-1].tick()
        assert menu._ui.title == menubar.PAUSED_ICON
        assert menu._items["pause"].title == "Fortsetzen"

        menu._toggle_pause(None)
        assert not menu.app.paused
        FakeTimer.instances[-1].tick()
        assert menu._items["pause"].title == "Pausieren"

    def test_quit_stops_the_app(self, menu, quitted) -> None:
        menu.run()
        menu._quit(None)
        assert quitted == [True]

    def test_open_config_uses_the_loaded_file(self, menu, monkeypatch, tmp_path) -> None:
        opened: list[str] = []
        monkeypatch.setattr(menubar, "_open", opened.append)
        config_file = tmp_path / "config.toml"
        config_file.write_text("", encoding="utf-8")
        menu.cfg.source = str(config_file)
        menu.run()
        menu._open_config(None)
        assert opened == [str(config_file)]

    def test_open_config_without_file_only_warns(self, menu, monkeypatch, caplog) -> None:
        opened: list[str] = []
        monkeypatch.setattr(menubar, "_open", opened.append)
        monkeypatch.setattr(menubar, "candidate_paths", list)
        menu.cfg.source = "defaults"
        menu.run()
        with caplog.at_level("WARNING", logger="flowtext.menubar"):
            menu._open_config(None)
        assert opened == []
        assert "config.toml" in caplog.text


# --------------------------------------------------------------------- Pause


class TestPause:
    def test_paused_app_ignores_triggers(self, menu) -> None:
        flow = menu.app
        flow.set_paused(True)
        flow._on_trigger()
        assert flow.state.current is State.IDLE

    def test_pausing_while_recording_discards_the_take(self, menu) -> None:
        flow = menu.app
        flow._on_trigger()
        assert flow.state.current is State.RECORDING
        flow.set_paused(True)
        assert flow.state.current is State.IDLE
        assert flow.recorder.aborted  # type: ignore[union-attr]

    def test_resuming_works_again(self, menu) -> None:
        flow = menu.app
        flow.set_paused(True)
        flow.set_paused(False)
        flow._on_trigger()
        assert flow.state.current is State.RECORDING

    def test_setting_the_same_value_is_a_noop(self, menu) -> None:
        flow = menu.app
        flow._on_trigger()
        flow.set_paused(False)  # war schon False
        assert flow.state.current is State.RECORDING


# ------------------------------------------------------------- Verfügbarkeit


def test_available_detects_loaded_module(rumps_module) -> None:
    assert menubar.available() is True


def test_available_false_without_rumps(monkeypatch) -> None:
    monkeypatch.delitem(sys.modules, "rumps", raising=False)
    monkeypatch.setattr(menubar.importlib.util, "find_spec", lambda name: None)
    assert menubar.available() is False


# ------------------------------------------------------- Berechtigungen im Menü


class TestPermissionState:
    """Regression: eine frisch gebaute FlowText.app hat noch keine Rechte.

    Beendet sie sich deswegen, kann sie die Berechtigung nie anfordern —
    und es erscheint nie ein Icon. Sie muss also starten und den Zustand
    anzeigen.
    """

    def _with_permissions(self, monkeypatch, ok: bool) -> None:
        from flowtext.permissions import HIDAccess, Permissions

        state = Permissions(
            accessibility=ok,
            input_monitoring=HIDAccess.GRANTED if ok else HIDAccess.DENIED,
        )
        monkeypatch.setattr(menubar.permissions, "check", lambda: state)

    def test_locked_icon_wins_over_every_state(self) -> None:
        for state in State:
            assert menubar.icon_for(state, paused=False, permissions_ok=False) == (
                menubar.LOCKED_ICON
            )
            assert menubar.icon_for(state, paused=True, permissions_ok=False) == (
                menubar.LOCKED_ICON
            )
            assert (
                menubar.label_for(state, paused=True, permissions_ok=False)
                == "Berechtigungen fehlen"
            )

    def test_starts_and_shows_the_lock(self, rumps_module, monkeypatch, tmp_path) -> None:
        from flowtext import app as app_module

        monkeypatch.setattr(app_module, "temp_dir", lambda: tmp_path / "tmp")
        self._with_permissions(monkeypatch, ok=False)

        cfg = Config()
        cfg.feedback.sound = False
        flow = FlowTextApp(
            cfg,
            recorder=FakeRecorder(),  # type: ignore[arg-type]
            hotkey=FakeHotkey(),  # type: ignore[arg-type]
            pipeline=FakePipeline(),  # type: ignore[arg-type]
        )
        menu = menubar.MenubarApp(cfg, flow)
        menu.run()  # darf NICHT vorher abbrechen
        assert menu._ui.title == menubar.LOCKED_ICON
        assert menu._items["status"].title == "Berechtigungen fehlen"

    def test_lock_disappears_once_granted(self, rumps_module, monkeypatch, tmp_path) -> None:
        from flowtext import app as app_module

        monkeypatch.setattr(app_module, "temp_dir", lambda: tmp_path / "tmp")
        self._with_permissions(monkeypatch, ok=False)

        cfg = Config()
        cfg.feedback.sound = False
        flow = FlowTextApp(
            cfg,
            recorder=FakeRecorder(),  # type: ignore[arg-type]
            hotkey=FakeHotkey(),  # type: ignore[arg-type]
            pipeline=FakePipeline(),  # type: ignore[arg-type]
        )
        menu = menubar.MenubarApp(cfg, flow)
        _run_ui(menu)
        timer = FakeTimer.instances[-1]

        self._with_permissions(monkeypatch, ok=True)
        for _ in range(menubar.PERMISSION_RECHECK_TICKS):
            timer.tick()
        assert menu._ui.title == "🎙"

    def test_menu_offers_both_settings_panes(self, menu) -> None:
        menu.run()
        titles = [item.title for item in menu._ui.menu if item is not None]
        assert any("Bedienungshilfen" in t for t in titles)
        assert any("Eingabeüberwachung" in t for t in titles)

    def test_settings_links_target_the_privacy_panes(self, menu, monkeypatch) -> None:
        opened: list[str] = []
        monkeypatch.setattr(menubar, "_open", opened.append)
        menu.run()
        menu._open_accessibility(None)
        menu._open_input_monitoring(None)
        assert "Privacy_Accessibility" in opened[0]
        assert "Privacy_ListenEvent" in opened[1]


def test_menubar_does_not_block_on_the_model(rumps_module, monkeypatch, tmp_path) -> None:
    """Regression: lud start() das Modell synchron, erschien das Icon erst
    danach — beim ersten Start also erst nach dem kompletten Download."""
    import threading

    from flowtext import app as app_module

    monkeypatch.setattr(app_module, "temp_dir", lambda: tmp_path / "tmp")
    blocked = threading.Event()

    class SlowPipeline(FakePipeline):
        def warmup(self) -> None:
            blocked.wait(5.0)

    cfg = Config()
    cfg.feedback.sound = False
    flow = FlowTextApp(
        cfg,
        recorder=FakeRecorder(),  # type: ignore[arg-type]
        hotkey=FakeHotkey(),  # type: ignore[arg-type]
        pipeline=SlowPipeline(),  # type: ignore[arg-type]
    )
    menu = menubar.MenubarApp(cfg, flow)
    menu.run()  # würde ohne den Fix 5 s hängen
    assert menu._ui.ran
    blocked.set()


def test_sigterm_quits_the_menubar_app(rumps_module, monkeypatch, tmp_path, quitted) -> None:
    """Regression: der Signal-Handler setzt nur ein Event. Blockiert rumps
    den Hauptthread und schaut niemand hin, verpufft SIGTERM — pkill wirkt
    dann folgenlos."""
    from flowtext import app as app_module

    monkeypatch.setattr(app_module, "temp_dir", lambda: tmp_path / "tmp")
    cfg = Config()
    cfg.feedback.sound = False
    flow = FlowTextApp(
        cfg,
        recorder=FakeRecorder(),  # type: ignore[arg-type]
        hotkey=FakeHotkey(),  # type: ignore[arg-type]
        pipeline=FakePipeline(),  # type: ignore[arg-type]
    )
    menu = menubar.MenubarApp(cfg, flow)
    _run_ui(menu)
    timer = FakeTimer.instances[-1]

    timer.tick()
    assert quitted == []  # noch kein Stopp angefordert

    flow.request_stop()
    assert flow.stop_requested
    timer.tick()
    assert quitted == [True]
