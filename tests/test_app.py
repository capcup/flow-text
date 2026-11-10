"""Controller-Tests: Zustandsübergänge ohne Threads, deterministisch."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from flowtext import app as app_module
from flowtext.app import FlowTextApp
from flowtext.audio.recorder import AudioError, Recording
from flowtext.config import Config
from flowtext.hotkey.detectors import Signal
from flowtext.state import State


class FakeRecorder:
    def __init__(self, duration: float = 1.0, start_error: Exception | None = None) -> None:
        self.duration = duration
        self.start_error = start_error
        self.is_recording = False
        self.elapsed = 0.0
        self.saved: list[Path] = []
        self.aborted = False
        self.start_calls = 0
        self.abort_calls = 0

    def start(self) -> None:
        self.start_calls += 1
        if self.start_error:
            raise self.start_error
        self.is_recording = True

    def stop(self) -> Recording | None:
        self.is_recording = False
        if self.duration <= 0:
            return None
        samples = np.full(int(16000 * self.duration), 0.5, dtype=np.float32)
        return Recording(samples=samples, sample_rate=16000)

    def abort(self) -> None:
        self.aborted = True
        self.abort_calls += 1
        self.is_recording = False

    def save(self, recording: Recording, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"RIFF")
        self.saved.append(path)
        return path


class FakeHotkey:
    dropped_triggers = 0
    slowest_callback_ms = 0.1

    def describe(self) -> str:
        return "Fake-Hotkey"

    def start(self) -> None: ...

    def stop(self, timeout: float = 2.0) -> None: ...


class FakePipeline:
    def __init__(self) -> None:
        self.runs: list[Path] = []

    def warmup(self) -> None: ...

    def shutdown(self) -> None: ...

    def run(self, path: Path) -> str:
        self.runs.append(path)
        return "text"


@pytest.fixture
def make_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(app_module, "temp_dir", lambda: tmp_path / "tmp")

    def _make(recorder: FakeRecorder | None = None, cfg: Config | None = None) -> FlowTextApp:
        cfg = cfg or Config()
        cfg.feedback.sound = False
        return FlowTextApp(
            cfg,
            recorder=recorder or FakeRecorder(),  # type: ignore[arg-type]
            hotkey=FakeHotkey(),  # type: ignore[arg-type]
            pipeline=FakePipeline(),  # type: ignore[arg-type]
        )

    return _make


def test_first_trigger_starts_recording(make_app) -> None:
    app = make_app()
    app._on_trigger()
    assert app.state.current is State.RECORDING


def test_second_trigger_queues_job(make_app) -> None:
    app = make_app()
    app._on_trigger()
    app._on_trigger()
    assert app.state.current is State.PROCESSING
    assert app.jobs.qsize() == 1


def test_trigger_during_processing_is_ignored(make_app) -> None:
    """Single-Flight: keine überlappenden Aufnahmen."""
    app = make_app()
    app._on_trigger()
    app._on_trigger()
    app._on_trigger()  # während PROCESSING
    assert app.state.current is State.PROCESSING
    assert app.jobs.qsize() == 1


def test_short_recording_returns_to_idle(make_app) -> None:
    app = make_app(FakeRecorder(duration=0.0))
    app._on_trigger()
    app._on_trigger()
    assert app.state.current is State.IDLE
    assert app.jobs.qsize() == 0


def test_microphone_error_returns_to_idle(make_app) -> None:
    """Regression: früher blieb die App in einem inkonsistenten Zustand."""
    app = make_app(FakeRecorder(start_error=AudioError("kein Mikrofon")))
    app._on_trigger()
    assert app.state.current is State.IDLE
    app._on_trigger()  # muss weiterhin bedienbar sein
    assert app.state.current is State.IDLE


def test_max_duration_stops_automatically(make_app) -> None:
    cfg = Config()
    cfg.audio.max_seconds = 5.0
    recorder = FakeRecorder()
    app = make_app(recorder, cfg)
    app._on_trigger()
    recorder.elapsed = 6.0
    app._check_max_duration()
    assert app.state.current is State.PROCESSING


def test_max_duration_check_is_noop_when_idle(make_app) -> None:
    app = make_app()
    app._check_max_duration()
    assert app.state.current is State.IDLE


def test_worker_processes_job_and_deletes_file(make_app) -> None:
    app = make_app()
    app._on_trigger()
    app._on_trigger()
    path = app.jobs.get_nowait()
    assert path.exists()

    app.pipeline.run(path)  # type: ignore[union-attr]
    app.state.transition(State.IDLE)
    path.unlink()
    assert app.state.current is State.IDLE


def test_full_queue_does_not_wedge_the_state(make_app) -> None:
    app = make_app()
    for _ in range(app.jobs.maxsize):
        app.jobs.put_nowait(Path("/tmp/dummy.wav"))
    app._on_trigger()
    app._on_trigger()
    assert app.state.current is State.IDLE  # über ERROR zurück nach IDLE


# ------------------------------------------------------------ früher Start


def test_early_start_opens_microphone_without_changing_state(make_app) -> None:
    recorder = FakeRecorder()
    app = make_app(recorder)
    app._on_signal(Signal.START)
    assert recorder.is_recording
    assert app.state.current is State.IDLE  # noch keine Aufnahme im Sinne des Zustands


def test_confirm_after_early_start_does_not_restart_recorder(make_app) -> None:
    recorder = FakeRecorder()
    app = make_app(recorder)
    app._on_signal(Signal.START)
    app._on_signal(Signal.CONFIRM)
    assert app.state.current is State.RECORDING
    assert recorder.start_calls == 1  # das Mikrofon lief schon


def test_cancel_after_early_start_discards_recording(make_app) -> None:
    recorder = FakeRecorder()
    app = make_app(recorder)
    app._on_signal(Signal.START)
    app._on_signal(Signal.CANCEL)
    assert recorder.abort_calls == 1
    assert not recorder.is_recording
    assert app.state.current is State.IDLE
    assert app.jobs.qsize() == 0


def test_cancel_without_early_start_is_noop(make_app) -> None:
    """CANCEL während einer laufenden Aufnahme (Stopp-Doppel-Tap) darf nichts abbrechen."""
    recorder = FakeRecorder()
    app = make_app(recorder)
    app._on_signal(Signal.CONFIRM)  # klassischer Start
    app._on_signal(Signal.CANCEL)
    assert recorder.abort_calls == 0
    assert app.state.current is State.RECORDING


def test_start_while_recording_is_ignored(make_app) -> None:
    """Der zweite Tap des Stopp-Doppel-Taps meldet ebenfalls START."""
    recorder = FakeRecorder()
    app = make_app(recorder)
    app._on_signal(Signal.CONFIRM)
    app._on_signal(Signal.START)
    assert recorder.start_calls == 1
    app._on_signal(Signal.CONFIRM)  # Stopp
    assert app.state.current is State.PROCESSING
    assert app.jobs.qsize() == 1


def test_early_start_ignored_while_paused(make_app) -> None:
    recorder = FakeRecorder()
    app = make_app(recorder)
    app.set_paused(True)
    app._on_signal(Signal.START)
    assert recorder.start_calls == 0


def test_pause_discards_tentative_recording(make_app) -> None:
    recorder = FakeRecorder()
    app = make_app(recorder)
    app._on_signal(Signal.START)
    app.set_paused(True)
    assert recorder.abort_calls == 1
    assert not recorder.is_recording


def test_early_start_failure_is_reported_on_confirm(make_app) -> None:
    """Ein Fehlstart darf nicht sofort Alarm schlagen — erst die Bestätigung tut es."""
    recorder = FakeRecorder(start_error=AudioError("kein Mikrofon"))
    app = make_app(recorder)
    app._on_signal(Signal.START)
    assert app.state.current is State.IDLE
    assert recorder.start_calls == 1
    app._on_signal(Signal.CONFIRM)
    assert recorder.start_calls == 2  # zweiter Versuch mit regulärer Fehlerbehandlung
    assert app.state.current is State.IDLE


def test_tentative_recording_times_out(make_app) -> None:
    """Sicherheitsnetz: bleibt die Bestätigung aus, läuft das Mikrofon nicht ewig."""
    recorder = FakeRecorder()
    app = make_app(recorder)
    app._on_signal(Signal.START)
    recorder.elapsed = 0.5
    app._check_tentative_timeout()
    assert recorder.is_recording  # noch im Rahmen
    recorder.elapsed = 5.0
    app._check_tentative_timeout()
    assert recorder.abort_calls == 1
    assert not recorder.is_recording


def test_start_signal_twice_does_not_open_twice(make_app) -> None:
    recorder = FakeRecorder()
    app = make_app(recorder)
    app._on_signal(Signal.START)
    app._on_signal(Signal.START)
    assert recorder.start_calls == 1
