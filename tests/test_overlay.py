"""Aufnahme-Anzeige: Rechnung, Pegel aus dem Recorder, Anbindung an die Menüleiste, Fenster."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest

from flowtext import indicator
from flowtext.config import Config, ConfigError, load

# ---------------------------------------------------------------- Rechnung


class TestIndicator:
    def test_level_mapping(self) -> None:
        assert indicator.normalise(0.0) == 0.0
        assert indicator.normalise(-1.0) == 0.0
        assert indicator.normalise(0.0005) < 0.03  # Stille
        assert indicator.normalise(0.003) < 0.1  # Raumrauschen (gemessen)
        assert 0.1 < indicator.normalise(0.005) < 0.2  # Stille-Schwelle: sichtbar, aber klein
        assert indicator.normalise(0.1) == pytest.approx(1.0)
        assert indicator.normalise(0.8) == 1.0  # nie über 1

    def test_level_mapping_is_monotonic(self) -> None:
        values = [indicator.normalise(x / 1000) for x in range(0, 300)]
        assert values == sorted(values)

    def test_rises_faster_than_it_falls(self) -> None:
        up = indicator.smooth(0.0, 1.0)
        down = 1.0 - indicator.smooth(1.0, 0.0)
        assert up > down > 0.0

    def test_smoothing_converges(self) -> None:
        level = 0.0
        for _ in range(60):
            level = indicator.smooth(level, 0.7)
        assert level == pytest.approx(0.7, abs=1e-3)

    def test_silent_bars_are_at_their_minimum(self) -> None:
        heights = indicator.bar_heights("recording", 0.0, 1.234)
        assert heights == [indicator.BASE] * indicator.BARS

    def test_the_middle_bar_reacts_most(self) -> None:
        heights = indicator.bar_heights("recording", 1.0, 0.0)
        assert len(heights) == indicator.BARS
        assert heights[2] == max(heights)
        assert heights[0] < heights[1] < heights[2]
        assert max(heights) <= indicator.BASE + indicator.AMPLITUDE

    def test_bars_stay_inside_the_pill(self) -> None:
        for step in range(200):
            assert max(indicator.bar_heights("recording", 1.0, step / 7)) <= 36.0 - 4.0

    def test_processing_ignores_the_level_and_moves(self) -> None:
        a = indicator.bar_heights("processing", 0.0, 0.5)
        assert a == indicator.bar_heights("processing", 1.0, 0.5)
        assert a != indicator.bar_heights("processing", 0.0, 0.9)
        assert all(indicator.BASE <= h <= indicator.BASE + 9.0 for h in a)
        assert not any(math.isnan(h) for h in a)


# ---------------------------------------------------------------- Recorder


class TestRecorderLevel:
    def _recorder(self):
        from flowtext.audio.recorder import AudioRecorder
        from flowtext.config import AudioConfig

        streams: list[Any] = []

        class Stream:
            def __init__(self, **kwargs: Any) -> None:
                self.callback = kwargs["callback"]
                streams.append(self)

            def start(self) -> None: ...

            def stop(self) -> None: ...

            def close(self) -> None: ...

        return AudioRecorder(AudioConfig(), stream_factory=Stream), streams

    def test_level_follows_the_last_block(self) -> None:
        import numpy as np

        recorder, streams = self._recorder()
        assert recorder.level == 0.0
        recorder.start()
        streams[0].callback(np.full((1024, 1), 0.5, dtype=np.float32), 1024, None, None)
        assert recorder.level == pytest.approx(0.5)
        streams[0].callback(np.zeros((1024, 1), dtype=np.float32), 1024, None, None)
        assert recorder.level == 0.0

    def test_level_is_reset_by_stop_and_abort(self) -> None:
        import numpy as np

        recorder, streams = self._recorder()
        recorder.start()
        streams[0].callback(np.full((1024, 1), 0.5, dtype=np.float32), 1024, None, None)
        recorder.stop()
        assert recorder.level == 0.0
        recorder.start()
        streams[1].callback(np.full((1024, 1), 0.5, dtype=np.float32), 1024, None, None)
        recorder.abort()
        assert recorder.level == 0.0


# ---------------------------------------------------------------- Config


def test_overlay_is_on_by_default_and_configurable(tmp_path: Path) -> None:
    assert Config().overlay.enabled is True
    path = tmp_path / "config.toml"
    path.write_text("[overlay]\nenabled = false\n", encoding="utf-8")
    assert load(path).overlay.enabled is False


def test_overlay_flag_must_be_a_bool(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[overlay]\nenabled = "nein"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="overlay"):
        load(path)


# ---------------------------------------------------------------- Fenster (AppKit)


@pytest.fixture
def appkit():
    kit = pytest.importorskip("AppKit")
    if not kit.NSScreen.screens():
        pytest.skip("kein Bildschirm (keine Anmeldesitzung)")
    kit.NSApplication.sharedApplication()
    return kit


class TestWindow:
    def _overlay(self, level: float = 0.0):
        from flowtext.overlay import RecordingOverlay

        return RecordingOverlay(lambda: level)

    def test_never_takes_the_focus(self, appkit) -> None:
        overlay = self._overlay()
        overlay.show("recording")
        try:
            win = overlay._window
            assert win.styleMask() & appkit.NSWindowStyleMaskNonactivatingPanel
            assert win.ignoresMouseEvents()
            assert not win.canBecomeKeyWindow()
            assert not win.canBecomeMainWindow()
            assert not win.isKeyWindow()
            assert not win.hidesOnDeactivate()
        finally:
            overlay.hide()

    def test_is_visible_on_every_space_and_over_full_screen_apps(self, appkit) -> None:
        overlay = self._overlay()
        overlay.show("recording")
        try:
            win = overlay._window
            behaviour = win.collectionBehavior()
            assert behaviour & appkit.NSWindowCollectionBehaviorCanJoinAllSpaces
            assert behaviour & appkit.NSWindowCollectionBehaviorFullScreenAuxiliary
            assert win.level() >= appkit.NSStatusWindowLevel
            assert win.isVisible()
        finally:
            overlay.hide()

    def test_show_and_hide(self, appkit) -> None:
        overlay = self._overlay()
        assert not overlay.visible
        overlay.hide()  # ohne vorheriges show: harmlos
        overlay.show("recording")
        assert overlay.visible and overlay._window.isVisible()
        overlay.show("recording")  # idempotent
        overlay.show("processing")
        assert overlay._view.mode == "processing"
        overlay.hide()
        assert not overlay.visible and not overlay._window.isVisible()
        assert overlay._timer is None
        overlay.show("recording")  # wieder zeigen
        assert overlay._window.isVisible()
        overlay.hide()

    def test_sits_at_the_bottom_centre_of_a_screen(self, appkit) -> None:
        overlay = self._overlay()
        overlay.show("recording")
        try:
            frame = overlay._window.frame()
            areas = [s.visibleFrame() for s in appkit.NSScreen.screens()]
            assert any(
                a.origin.x <= frame.origin.x
                and frame.origin.x + frame.size.width <= a.origin.x + a.size.width
                and a.origin.y <= frame.origin.y <= a.origin.y + 80
                for a in areas
            )
        finally:
            overlay.hide()

    def test_bars_follow_the_level_source(self, appkit) -> None:
        loud = self._overlay(level=0.2)
        loud.show("recording")
        for _ in range(30):
            loud._frame()
        quiet = self._overlay(level=0.0)
        quiet.show("recording")
        for _ in range(30):
            quiet._frame()
        try:
            assert max(loud._view.heights) > max(quiet._view.heights) + 10
            assert max(quiet._view.heights) == pytest.approx(indicator.BASE)
        finally:
            loud.hide()
            quiet.hide()

    def test_a_failing_level_source_turns_the_overlay_off_quietly(self, appkit, caplog) -> None:
        from flowtext.overlay import RecordingOverlay

        def boom() -> float:
            raise RuntimeError("kaputt")

        overlay = RecordingOverlay(boom)
        overlay._mode = "recording"
        overlay._build()
        overlay._timer = None
        with caplog.at_level("ERROR"):
            overlay._on_timer(None)
        assert "abgeschaltet" in caplog.text
        assert not overlay.visible

    @pytest.mark.parametrize("mode", ["recording", "processing"])
    def test_renders_a_png(self, appkit, tmp_path: Path, mode: str) -> None:
        target = tmp_path / f"{mode}.png"
        self._overlay().render_png(target, mode)
        assert target.stat().st_size > 500
