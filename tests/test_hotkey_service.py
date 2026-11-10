"""Tests des HotkeyService mit einem Fake-Listener (kein Event-Tap nötig)."""

from __future__ import annotations

import queue
import threading
import time
from typing import Any, ClassVar

import pytest

from flowtext.config import HotkeyConfig
from flowtext.hotkey import service as service_module
from flowtext.hotkey.detectors import Signal
from flowtext.hotkey.service import TRIGGER, HotkeyService


class FakeListener:
    """Imitiert ``pynput.keyboard.Listener`` inklusive Lebenszyklus."""

    instances: ClassVar[list[FakeListener]] = []

    def __init__(self, on_press: Any, on_release: Any) -> None:
        self.on_press = on_press
        self.on_release = on_release
        self.running = False
        self._done = threading.Event()
        FakeListener.instances.append(self)

    def start(self) -> None:
        self.running = True

    def wait(self) -> None:
        pass

    def join(self, timeout: float | None = None) -> None:
        self._done.wait(timeout)

    def stop(self) -> None:
        self.running = False
        self._done.set()

    # Testhilfe
    def tap(self, key: Any, press_at: float, release_at: float) -> None:
        self.on_press(key)
        self.on_release(key)


class FakeKey:
    def __init__(self, vk: int, name: str) -> None:
        self.value = type("V", (), {"vk": vk})()
        self.name = name


CMD_R = FakeKey(0x36, "cmd_r")
CMD_L = FakeKey(0x37, "cmd")


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch):
    FakeListener.instances.clear()
    monkeypatch.setattr("flowtext.hotkey.keynames.sys.platform", "darwin")

    cfg = HotkeyConfig(mode="double_tap", tap_key="cmd_r", early_start=False)
    events: queue.Queue[str] = queue.Queue()
    svc = HotkeyService(cfg, events)
    monkeypatch.setattr(svc, "_make_listener", lambda: FakeListener(svc._on_press, svc._on_release))
    yield svc, events
    svc.stop(timeout=1.0)


def _wait_for_listener(timeout: float = 2.0) -> FakeListener:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if FakeListener.instances:
            return FakeListener.instances[-1]
        time.sleep(0.01)
    raise AssertionError("Listener wurde nicht erzeugt")


def test_double_tap_cmd_r_produces_trigger(service) -> None:
    """Der zentrale Fall: zweimal rechte Command → genau ein Auslöser."""
    svc, events = service
    svc.start()
    listener = _wait_for_listener()

    listener.on_press(CMD_R)
    listener.on_release(CMD_R)
    listener.on_press(CMD_R)
    listener.on_release(CMD_R)

    assert events.get(timeout=1.0) == TRIGGER
    assert events.empty()
    assert svc.trigger_count == 1


def test_early_start_emits_start_then_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Früher Start: der zweite Druck meldet START, das Loslassen CONFIRM."""
    FakeListener.instances.clear()
    monkeypatch.setattr("flowtext.hotkey.keynames.sys.platform", "darwin")
    events: queue.Queue[str] = queue.Queue()
    svc = HotkeyService(HotkeyConfig(mode="double_tap", tap_key="cmd_r", early_start=True), events)
    monkeypatch.setattr(svc, "_make_listener", lambda: FakeListener(svc._on_press, svc._on_release))
    try:
        svc.start()
        listener = _wait_for_listener()
        listener.on_press(CMD_R)
        listener.on_release(CMD_R)
        listener.on_press(CMD_R)
        assert events.get(timeout=1.0) == Signal.START
        assert events.empty()
        listener.on_release(CMD_R)
        assert events.get(timeout=1.0) == Signal.CONFIRM
        assert svc.trigger_count == 1  # nur CONFIRM zählt als Auslösung
    finally:
        svc.stop(timeout=1.0)


def test_early_start_cancel_when_key_is_held(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeListener.instances.clear()
    monkeypatch.setattr("flowtext.hotkey.keynames.sys.platform", "darwin")
    events: queue.Queue[str] = queue.Queue()
    cfg = HotkeyConfig(mode="double_tap", tap_key="cmd_r", early_start=True, max_tap_duration=0.05)
    svc = HotkeyService(cfg, events)
    monkeypatch.setattr(svc, "_make_listener", lambda: FakeListener(svc._on_press, svc._on_release))
    try:
        svc.start()
        listener = _wait_for_listener()
        listener.on_press(CMD_R)
        listener.on_release(CMD_R)
        listener.on_press(CMD_R)
        assert events.get(timeout=1.0) == Signal.START
        time.sleep(0.1)  # länger gehalten als max_tap_duration
        listener.on_release(CMD_R)
        assert events.get(timeout=1.0) == Signal.CANCEL
        assert svc.trigger_count == 0
    finally:
        svc.stop(timeout=1.0)


def test_single_tap_produces_nothing(service) -> None:
    svc, events = service
    svc.start()
    listener = _wait_for_listener()
    listener.on_press(CMD_R)
    listener.on_release(CMD_R)
    time.sleep(0.05)
    assert events.empty()


def test_left_command_does_not_trigger(service) -> None:
    svc, events = service
    svc.start()
    listener = _wait_for_listener()
    for _ in range(2):
        listener.on_press(CMD_L)
        listener.on_release(CMD_L)
    time.sleep(0.05)
    assert events.empty()


def test_events_are_counted(service) -> None:
    """Grundlage für den Watchdog: kommt überhaupt etwas an?"""
    svc, _ = service
    svc.start()
    listener = _wait_for_listener()
    assert svc.event_count == 0
    listener.on_press(CMD_R)
    listener.on_release(CMD_R)
    assert svc.event_count == 2


def test_callback_stays_fast(service) -> None:
    """Über 1000 ms würde macOS den Event-Tap dauerhaft abschalten."""
    svc, _ = service
    svc.start()
    listener = _wait_for_listener()
    for _ in range(200):
        listener.on_press(CMD_R)
        listener.on_release(CMD_R)
    assert svc.slowest_callback_ms < 5.0


def test_dead_listener_is_restarted(service) -> None:
    """Regression: pynput beendet den Listener bei jeder Callback-Exception."""
    svc, events = service
    svc.start()
    first = _wait_for_listener()
    first.stop()  # simuliert den stillen Tod

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and len(FakeListener.instances) < 2:
        time.sleep(0.05)
    assert len(FakeListener.instances) >= 2, "Supervisor hat nicht neu gestartet"

    second = FakeListener.instances[-1]
    for _ in range(2):
        second.on_press(CMD_R)
        second.on_release(CMD_R)
    assert events.get(timeout=1.0) == TRIGGER


def test_callback_exception_does_not_escape(service, monkeypatch) -> None:
    """Eine Exception im Detektor darf den Listener nicht töten."""
    svc, _ = service
    svc.start()
    listener = _wait_for_listener()

    def boom(name: str, now: float) -> bool:
        raise RuntimeError("kaputt")

    monkeypatch.setattr(svc._detector, "on_press", boom)
    listener.on_press(CMD_R)  # darf nicht werfen
    assert listener.running


def _permissions(monkeypatch, ok: bool) -> None:
    from flowtext import permissions

    state = permissions.Permissions(
        accessibility=ok,
        input_monitoring=permissions.HIDAccess.GRANTED if ok else permissions.HIDAccess.DENIED,
    )
    monkeypatch.setattr(permissions, "check", lambda: state)


def test_watchdog_warns_when_no_events(monkeypatch, caplog) -> None:
    """Ohne Ereignisse UND ohne Rechte muss FlowText Alarm schlagen."""
    monkeypatch.setattr(service_module, "_NO_EVENT_WARNING_SECONDS", 0.05)
    _permissions(monkeypatch, ok=False)
    FakeListener.instances.clear()
    svc = HotkeyService(HotkeyConfig(), queue.Queue())
    monkeypatch.setattr(svc, "_make_listener", lambda: FakeListener(svc._on_press, svc._on_release))
    with caplog.at_level("ERROR", logger="flowtext.hotkey.service"):
        svc.start()
        time.sleep(0.3)
        svc.stop(timeout=1.0)
    assert any("KEIN einziges Tastaturereignis" in r.message for r in caplog.records)


def test_watchdog_stays_calm_when_permissions_are_fine(monkeypatch, caplog) -> None:
    """Regression: Stille beweist nichts — vielleicht hat niemand getippt.
    Sind die Rechte erteilt, darf das kein Fehler sein."""
    monkeypatch.setattr(service_module, "_NO_EVENT_WARNING_SECONDS", 0.05)
    _permissions(monkeypatch, ok=True)
    FakeListener.instances.clear()
    svc = HotkeyService(HotkeyConfig(), queue.Queue())
    monkeypatch.setattr(svc, "_make_listener", lambda: FakeListener(svc._on_press, svc._on_release))
    with caplog.at_level("INFO", logger="flowtext.hotkey.service"):
        svc.start()
        time.sleep(0.3)
        svc.stop(timeout=1.0)
    assert not any(r.levelname == "ERROR" for r in caplog.records)
    assert "normal" in caplog.text


def test_watchdog_silent_when_events_arrive(monkeypatch, caplog) -> None:
    monkeypatch.setattr(service_module, "_NO_EVENT_WARNING_SECONDS", 0.15)
    _permissions(monkeypatch, ok=False)
    FakeListener.instances.clear()
    svc = HotkeyService(HotkeyConfig(), queue.Queue())
    monkeypatch.setattr(svc, "_make_listener", lambda: FakeListener(svc._on_press, svc._on_release))
    with caplog.at_level("ERROR", logger="flowtext.hotkey.service"):
        svc.start()
        _wait_for_listener().on_press(CMD_R)
        time.sleep(0.3)
        svc.stop(timeout=1.0)
    assert not any("KEIN einziges" in r.message for r in caplog.records)
