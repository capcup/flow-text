"""Settings-Fenster: gespeicherte Einstellungen, Umschalten zur Laufzeit, Menüanbindung."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from flowtext import app as app_module
from flowtext import config as config_module
from flowtext import preferences
from flowtext.app import FlowTextApp
from flowtext.audio.recorder import Recording
from flowtext.config import Config, load
from flowtext.pipeline.base import Pipeline, TranscriptionResult
from flowtext.pipeline.cleanup_rules import RuleBasedCleaner
from flowtext.pipeline.transcribe_mlx import MlxTranscriber
from flowtext.preferences import OPTIONS, Preferences

STREAMING = preferences.find("streaming", "enabled")
FILLERS = preferences.find("cleanup", "remove_fillers")
SOUND = preferences.find("feedback", "sound")
VOCAB = preferences.find("pipeline", "vocabulary")


# ---------------------------------------------------------------- Preferences


class TestPreferences:
    def test_nothing_stored_means_no_changes(self, tmp_path: Path) -> None:
        cfg = Config()
        assert Preferences(tmp_path / "p.json").apply(cfg) is False
        assert cfg.streaming.enabled is False

    def test_saved_values_are_applied_on_top_of_the_config(self, tmp_path: Path) -> None:
        prefs = Preferences(tmp_path / "p.json")
        prefs.save(STREAMING, True)
        prefs.save(VOCAB, ["FlowText", "Ollama"])

        cfg = Config()
        cfg.feedback.sound = False  # aus config.toml, nicht im Fenster geändert
        assert prefs.apply(cfg) is True
        assert cfg.streaming.enabled is True
        assert cfg.pipeline.vocabulary == ["FlowText", "Ollama"]
        assert cfg.feedback.sound is False

    def test_only_changed_values_are_stored(self, tmp_path: Path) -> None:
        prefs = Preferences(tmp_path / "p.json")
        prefs.save(FILLERS, True)
        assert json.loads(prefs.path.read_text(encoding="utf-8")) == {
            "cleanup": {"remove_fillers": True}
        }

    def test_a_later_save_keeps_earlier_values(self, tmp_path: Path) -> None:
        prefs = Preferences(tmp_path / "p.json")
        prefs.save(FILLERS, True)
        prefs.save(SOUND, False)
        assert prefs.load() == {("cleanup", "remove_fillers"): True, ("feedback", "sound"): False}

    def test_garbage_is_skipped_not_fatal(self, tmp_path: Path, caplog) -> None:
        path = tmp_path / "p.json"
        path.write_text(
            json.dumps(
                {
                    "streaming": {
                        "enabled": "ja",
                        "pause_seconds": 9,
                    },  # Typ falsch / nicht änderbar
                    "hotkey": {"mode": "combo"},  # gehört nicht ins Fenster
                    "cleanup": {"remove_fillers": True},
                }
            ),
            encoding="utf-8",
        )
        with caplog.at_level("WARNING"):
            assert Preferences(path).load() == {("cleanup", "remove_fillers"): True}

    @pytest.mark.parametrize("content", ["{kaputt", "[]", '"text"', ""])
    def test_unreadable_file_is_ignored(self, tmp_path: Path, content: str) -> None:
        path = tmp_path / "p.json"
        path.write_text(content, encoding="utf-8")
        assert Preferences(path).load() == {}

    def test_save_rejects_wrong_types(self, tmp_path: Path) -> None:
        prefs = Preferences(tmp_path / "p.json")
        with pytest.raises(ValueError, match="true oder false"):
            prefs.save(STREAMING, "an")
        with pytest.raises(ValueError, match="Liste"):
            prefs.save(VOCAB, "FlowText")
        assert not prefs.path.exists()

    def test_save_leaves_no_temp_files(self, tmp_path: Path) -> None:
        Preferences(tmp_path / "sub" / "p.json").save(SOUND, False)
        assert [p.name for p in (tmp_path / "sub").iterdir()] == ["p.json"]

    def test_unknown_setting_is_an_error(self) -> None:
        with pytest.raises(KeyError):
            preferences.find("hotkey", "mode")

    def test_parse_terms(self) -> None:
        assert preferences.parse_terms("  A, B ,, A\nC ") == ["A", "B", "C"]
        assert preferences.parse_terms("") == []

    def test_every_option_has_a_label_and_hint(self) -> None:
        assert [(o.section, o.key) for o in OPTIONS] == [
            ("streaming", "enabled"),
            ("cleanup", "remove_fillers"),
            ("feedback", "sound"),
            ("overlay", "enabled"),
            ("pipeline", "vocabulary"),
        ]
        assert all(o.label and o.hint for o in OPTIONS)


class TestConfigLoad:
    def _setup(self, tmp_path: Path, monkeypatch) -> Path:
        toml = tmp_path / "config.toml"
        toml.write_text("[cleanup]\nremove_fillers = false\n", encoding="utf-8")
        monkeypatch.setattr(config_module, "candidate_paths", lambda: [toml])
        path = tmp_path / "preferences.json"
        monkeypatch.setattr(preferences, "preferences_path", lambda: path)
        return path

    def test_window_settings_win_over_config_toml(self, tmp_path: Path, monkeypatch) -> None:
        path = self._setup(tmp_path, monkeypatch)
        Preferences(path).save(FILLERS, True)
        cfg = load()
        assert cfg.cleanup.remove_fillers is True
        assert cfg.source.endswith("config.toml + preferences.json")

    def test_without_stored_values_the_file_decides(self, tmp_path: Path, monkeypatch) -> None:
        self._setup(tmp_path, monkeypatch)
        cfg = load()
        assert cfg.cleanup.remove_fillers is False
        assert "preferences" not in cfg.source

    def test_an_explicit_path_ignores_the_stored_values(self, tmp_path: Path, monkeypatch) -> None:
        path = self._setup(tmp_path, monkeypatch)
        Preferences(path).save(FILLERS, True)
        explicit = tmp_path / "other.toml"
        explicit.write_text("", encoding="utf-8")
        assert load(explicit).cleanup.remove_fillers is False


# ---------------------------------------------------------------- App


class _Recorder:
    is_recording = False
    elapsed = 0.0

    def start(self) -> None: ...

    def stop(self) -> Recording | None:
        return None

    def abort(self) -> None: ...


class _Hotkey:
    dropped_triggers = 0
    slowest_callback_ms = 0.0

    def describe(self) -> str:
        return "Fake"

    def start(self) -> None: ...

    def stop(self, timeout: float = 2.0) -> None: ...


class _Transcriber:
    def transcribe(self, recording: Recording, *, context: str = "") -> TranscriptionResult:
        return TranscriptionResult(text="")


@pytest.fixture
def make_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(app_module, "temp_dir", lambda: tmp_path / "tmp")

    def _make(transcriber: Any = None):
        cfg = Config()
        cfg.feedback.sound = False
        prefs = Preferences(tmp_path / "preferences.json")
        app = FlowTextApp(
            cfg,
            recorder=_Recorder(),  # type: ignore[arg-type]
            hotkey=_Hotkey(),  # type: ignore[arg-type]
            pipeline=Pipeline(transcriber or _Transcriber()),
            prefs=prefs,
        )
        return app, prefs

    return _make


class TestSetOption:
    def test_toggle_applies_immediately_and_is_remembered(self, make_app) -> None:
        app, prefs = make_app()
        app.set_option("streaming", "enabled", True)
        assert app.cfg.streaming.enabled is True
        assert prefs.load() == {("streaming", "enabled"): True}

    def test_filler_removal_is_switched_on_and_off_without_a_restart(self, make_app) -> None:
        app, _ = make_app()
        assert app.pipeline.cleaner is None
        app.set_option("cleanup", "remove_fillers", True)
        assert isinstance(app.pipeline.cleaner, RuleBasedCleaner)
        assert app.pipeline.cleaner.refine("Das ist, äh, gut.") == "Das ist gut."
        app.set_option("cleanup", "remove_fillers", False)
        assert app.pipeline.cleaner is None

    def test_vocabulary_reaches_the_transcriber(self, make_app, monkeypatch) -> None:
        calls: list[dict[str, Any]] = []

        def transcribe(audio: Any, **kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            return {"text": "x"}

        module = types.ModuleType("mlx_whisper")
        module.transcribe = transcribe  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "mlx_whisper", module)

        app, _ = make_app(MlxTranscriber())
        app.set_option("pipeline", "vocabulary", ["FlowText", " Ollama ", "FlowText"])
        assert app.cfg.pipeline.vocabulary == ["FlowText", "Ollama"]

        clip = Recording(samples=np.zeros(1600, dtype=np.float32), sample_rate=16_000)
        app.pipeline.transcriber.transcribe(clip)
        assert calls[0]["initial_prompt"] == "FlowText, Ollama."

    def test_sound_setting_reaches_the_feedback(self, make_app) -> None:
        app, _ = make_app()
        app.cfg.feedback.sound = True
        app.set_option("feedback", "sound", False)
        assert app.feedback._cfg.sound is False

    def test_wrong_type_is_refused_and_changes_nothing(self, make_app) -> None:
        app, prefs = make_app()
        with pytest.raises(ValueError, match="true oder false"):
            app.set_option("streaming", "enabled", "an")
        assert app.cfg.streaming.enabled is False
        assert prefs.load() == {}

    def test_unknown_setting_is_refused(self, make_app) -> None:
        app, _ = make_app()
        with pytest.raises(KeyError):
            app.set_option("hotkey", "mode", "combo")

    def test_persist_can_be_switched_off(self, make_app) -> None:
        app, prefs = make_app()
        app.set_option("streaming", "enabled", True, persist=False)
        assert app.cfg.streaming.enabled is True
        assert prefs.load() == {}

    def test_a_failing_disk_does_not_undo_the_change(self, make_app, monkeypatch) -> None:
        app, prefs = make_app()

        def boom(*args: Any, **kwargs: Any) -> None:
            raise OSError("Platte voll")

        monkeypatch.setattr(prefs, "save", boom)
        app.set_option("streaming", "enabled", True)
        assert app.cfg.streaming.enabled is True

    def test_stream_worker_always_runs_so_the_option_works_without_a_restart(
        self, make_app
    ) -> None:
        app, _ = make_app()
        assert app.cfg.streaming.enabled is False
        app.start()
        try:
            assert "stream-worker" in [t.name for t in app._threads]
        finally:
            app.shutdown()


# ---------------------------------------------------------------- Fenster (AppKit)


@pytest.fixture
def window_app():
    """Das echte Fenster braucht AppKit und eine Anmeldesitzung mit Bildschirm."""
    appkit = pytest.importorskip("AppKit")
    if not appkit.NSScreen.screens():
        pytest.skip("kein Bildschirm (keine Anmeldesitzung)")
    appkit.NSApplication.sharedApplication()

    class FakeApp:
        def __init__(self) -> None:
            self.cfg = Config()
            self.calls: list[tuple[str, str, Any]] = []

        def set_option(self, section: str, key: str, value: Any) -> None:
            option = preferences.find(section, key)
            preferences.set_value(self.cfg, option, preferences.check(option, value))
            self.calls.append((section, key, value))

    return FakeApp()


def _index(section: str, key: str) -> int:
    return [(o.section, o.key) for o in OPTIONS].index((section, key))


class TestWindow:
    def _window(self, app: Any, opened: list[int] | None = None):
        from flowtext.settings_window import SettingsWindow

        sink: list[int] = [] if opened is None else opened
        return SettingsWindow(app, lambda: sink.append(1))

    def test_shows_the_current_values(self, window_app) -> None:
        window_app.cfg.cleanup.remove_fillers = True
        window_app.cfg.pipeline.vocabulary = ["A", "B"]
        window = self._window(window_app)
        window.render_png(Path("/dev/null"))  # baut und füllt das Fenster
        states = [int(window._controls[i].state()) for i in range(4)]
        assert states == [0, 1, 1, 1]  # Live aus, Füllwörter an, Töne an, Anzeige an
        assert str(window._controls[_index("pipeline", "vocabulary")].stringValue()) == "A, B"

    def test_clicks_change_the_settings(self, window_app) -> None:
        window = self._window(window_app)
        window.render_png(Path("/dev/null"))
        for section, key in (
            ("streaming", "enabled"),
            ("cleanup", "remove_fillers"),
            ("feedback", "sound"),
            ("overlay", "enabled"),
        ):
            window._controls[_index(section, key)].performClick_(None)
        assert window_app.cfg.streaming.enabled is True
        assert window_app.cfg.cleanup.remove_fillers is True
        assert window_app.cfg.feedback.sound is False
        assert window_app.cfg.overlay.enabled is False

    def test_vocabulary_is_tidied_and_saved_on_commit_and_on_close(self, window_app) -> None:
        window = self._window(window_app)
        window.render_png(Path("/dev/null"))
        field = window._controls[_index("pipeline", "vocabulary")]
        field.setStringValue_("  Whisper, ,Ollama,Whisper\nMLX ")
        window.commit_text_fields()
        assert window_app.cfg.pipeline.vocabulary == ["Whisper", "Ollama", "MLX"]
        assert str(field.stringValue()) == "Whisper, Ollama, MLX"
        n = len(window_app.calls)
        window.commit_text_fields()
        assert len(window_app.calls) == n  # unverändert: nicht noch einmal speichern

    def test_a_failing_change_does_not_escape_into_cocoa(self, window_app, caplog) -> None:
        def boom(*args: Any) -> None:
            raise RuntimeError("kaputt")

        window_app.set_option = boom
        window = self._window(window_app)
        window.render_png(Path("/dev/null"))
        with caplog.at_level("ERROR"):
            window._controls[0].performClick_(None)
        assert "konnte nicht gesetzt werden" in caplog.text
        assert int(window._controls[0].state()) == 0  # Anzeige springt zurück

    def test_config_button_calls_back(self, window_app) -> None:
        opened: list[int] = []
        window = self._window(window_app, opened)
        window.render_png(Path("/dev/null"))
        window._actions.openConfig_(None)
        assert opened == [1]

    def test_reopening_after_close_works(self, window_app) -> None:
        window = self._window(window_app)
        window.show()
        window.close()
        window.show()
        window.close()

    def test_renders_a_png(self, window_app, tmp_path: Path) -> None:
        target = tmp_path / "settings.png"
        self._window(window_app).render_png(target)
        assert target.stat().st_size > 5_000
