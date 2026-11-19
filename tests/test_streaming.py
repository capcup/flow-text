"""Abschnittsweise Ausgabe: Text erscheint nach jeder Sprechpause, schon während der Aufnahme."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from flowtext import app as app_module
from flowtext.app import FlowTextApp
from flowtext.audio.recorder import AudioArray, Recording
from flowtext.config import Config, ConfigError, load, validate
from flowtext.pipeline.base import Pipeline, TranscriptionResult
from flowtext.state import State

RATE = 16_000


def tone(seconds: float, level: float = 0.1) -> AudioArray:
    rng = np.random.default_rng(3)
    return (rng.standard_normal(int(RATE * seconds)) * level).astype(np.float32)


def quiet(seconds: float) -> AudioArray:
    return tone(seconds, 0.0005)


# ---------------------------------------------------------------- Fakes


class StreamRecorder:
    """Nimmt vorbereitete Audiodaten entgegen und gibt sie wie der echte Recorder frei."""

    def __init__(self) -> None:
        self.is_recording = False
        self.elapsed = 0.0
        self.pending: list[AudioArray] = []
        self.everything: list[AudioArray] = []
        self.saved: list[Path] = []
        self.take_calls = 0

    def start(self) -> None:
        self.is_recording = True
        self.pending, self.everything = [], []

    def feed(self, samples: AudioArray) -> None:
        self.pending.append(samples)
        self.everything.append(samples)

    def take_new(self) -> AudioArray | None:
        self.take_calls += 1
        if not self.pending:
            return None
        merged = np.concatenate(self.pending)
        self.pending = []
        return merged

    def stop(self) -> Recording | None:
        self.is_recording = False
        if not self.everything:
            return None
        return Recording(samples=np.concatenate(self.everything), sample_rate=RATE)

    def abort(self) -> None:
        self.is_recording = False
        self.pending, self.everything = [], []

    def save(self, recording: Recording, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"RIFF")
        self.saved.append(path)
        return path


class FakeHotkey:
    dropped_triggers = 0
    slowest_callback_ms = 0.1

    def describe(self) -> str:
        return "Fake"

    def start(self) -> None: ...

    def stop(self, timeout: float = 2.0) -> None: ...


class ScriptedPipeline:
    """Liefert pro Aufruf den nächsten Text und merkt sich, womit sie aufgerufen wurde."""

    def __init__(self, *texts: str | Exception) -> None:
        self.texts = list(texts)
        self.calls: list[dict[str, Any]] = []

    def warmup(self) -> None: ...

    def shutdown(self) -> None: ...

    def run(self, recording: Recording, *, context: str = "", prefix: str = "") -> str:
        self.calls.append({"seconds": recording.duration, "context": context, "prefix": prefix})
        result = self.texts.pop(0) if self.texts else ""
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture
def make(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(app_module, "temp_dir", lambda: tmp_path / "tmp")

    def _make(*texts: str | Exception, enabled: bool = True, keep: bool = False):
        cfg = Config()
        cfg.feedback.sound = False
        cfg.streaming.enabled = enabled
        cfg.audio.delete_after_use = not keep
        recorder = StreamRecorder()
        pipeline = ScriptedPipeline(*texts)
        app = FlowTextApp(
            cfg,
            recorder=recorder,  # type: ignore[arg-type]
            hotkey=FakeHotkey(),  # type: ignore[arg-type]
            pipeline=pipeline,  # type: ignore[arg-type]
        )
        return app, recorder, pipeline

    return _make


def speak(app: FlowTextApp, recorder: StreamRecorder, audio: AudioArray) -> None:
    """Füttert die Aufnahme in 0,2-s-Häppchen, wie die Controller-Schleife es abholt."""
    step = int(RATE * 0.2)
    for i in range(0, len(audio), step):
        recorder.feed(audio[i : i + step])
        app._poll_streaming()


def drain(app: FlowTextApp) -> None:
    """Arbeitet alle wartenden Abschnitte der Reihe nach ab (wie der Stream-Worker)."""
    while not app.segments.empty():
        app._process_segment(app.segments.get_nowait())


# ---------------------------------------------------------------- App


def test_segments_are_cut_while_still_recording(make) -> None:
    app, recorder, _ = make("Erster Satz.")
    app._on_trigger()
    speak(app, recorder, np.concatenate([tone(3.0), quiet(1.0)]))
    assert app.state.current is State.RECORDING
    assert app.segments.qsize() == 1  # schon VOR dem Stopp


def test_full_dictation_inserts_each_part_with_context(make) -> None:
    app, recorder, pipeline = make("Das ist der erste Satz.", "Und hier der zweite.", "Fertig.")
    app._on_trigger()
    speak(app, recorder, np.concatenate([tone(3.0), quiet(1.0), tone(3.0), quiet(1.0), tone(1.0)]))
    drain(app)
    app._on_trigger()  # Stopp
    assert app.state.current is State.PROCESSING
    drain(app)

    first, second, third = pipeline.calls
    assert (first["context"], first["prefix"]) == ("", "")
    assert second["context"] == "Das ist der erste Satz."
    assert second["prefix"] == " "
    assert third["context"] == "Das ist der erste Satz. Und hier der zweite."
    assert app.state.current is State.IDLE
    assert app.notice is None


def test_stop_sends_the_open_rest_as_final_segment(make) -> None:
    app, recorder, pipeline = make("Kurzer Satz.")
    app._on_trigger()
    speak(app, recorder, tone(1.5))
    assert app.segments.empty()  # noch keine Pause
    app._on_trigger()
    job = app.segments.get_nowait()
    assert job.final
    assert job.recording is not None and job.recording.duration == pytest.approx(1.5, abs=0.01)
    app._process_segment(job)
    assert pipeline.calls[0]["seconds"] == pytest.approx(1.5, abs=0.01)
    assert app.state.current is State.IDLE


def test_nothing_recognised_leaves_a_hint(make) -> None:
    app, recorder, _ = make("", "")
    app._on_trigger()
    speak(app, recorder, tone(3.0))
    app._on_trigger()
    drain(app)
    assert app.state.current is State.IDLE
    assert app.notice is not None and not app.notice.is_error


def test_failure_in_the_middle_does_not_disturb_the_recording(make) -> None:
    app, recorder, _ = make(RuntimeError("Modell fehlt"), "Weiter geht es.")
    app._on_trigger()
    speak(app, recorder, np.concatenate([tone(3.0), quiet(1.0), tone(2.0)]))
    drain(app)
    # Die Aufnahme läuft weiter; nur die Meldung ist da.
    assert app.state.current is State.RECORDING
    assert app.notice is not None and app.notice.is_error

    app._on_trigger()
    drain(app)
    assert app.state.current is State.IDLE  # über ERROR zurück nach IDLE
    assert app.notice is not None and "Transcription failed" in app.notice.message


def test_disabled_streaming_keeps_the_classic_path(make) -> None:
    app, recorder, _ = make(enabled=False)
    app._on_trigger()
    speak(app, recorder, tone(3.0))
    assert recorder.take_calls == 0
    app._on_trigger()
    assert app.segments.empty()
    assert app.jobs.qsize() == 1


def test_pausing_while_recording_drops_the_stream(make) -> None:
    app, _, _ = make("x")
    app._on_trigger()
    app.set_paused(True)
    assert app._segmenter is None and app._stream_run is None
    assert app.state.current is State.IDLE


def test_a_new_dictation_starts_with_a_clean_context(make) -> None:
    app, recorder, pipeline = make("Eins.", "Zwei.")
    for _ in range(2):
        app._on_trigger()
        speak(app, recorder, tone(2.5))
        app._on_trigger()
        drain(app)
    assert [c["context"] for c in pipeline.calls] == ["", ""]


def test_full_final_queue_reports_an_error(make) -> None:
    app, recorder, _ = make()
    app._on_trigger()
    speak(app, recorder, tone(2.0))
    for _ in range(app.segments.maxsize):
        app.segments.put_nowait(app_module.SegmentJob(app_module.StreamRun(), None))
    # put() wartet 1 s; für den Test genügt ein kurzer Timeout.
    original_put = app.segments.put
    app.segments.put = lambda job, timeout=None: original_put(job, timeout=0.01)  # type: ignore[method-assign]
    app._on_trigger()
    assert app.state.current is State.IDLE
    assert app.notice is not None and "busy" in app.notice.message


def test_debug_dump_keeps_the_whole_recording_with_its_text(make, tmp_path: Path) -> None:
    app, recorder, _ = make("Erster.", "Zweiter.", keep=True)
    app._on_trigger()
    speak(app, recorder, np.concatenate([tone(3.0), quiet(1.0), tone(2.0)]))
    app._on_trigger()
    drain(app)
    assert len(recorder.saved) == 1
    assert recorder.saved[0].with_suffix(".txt").read_text(encoding="utf-8") == "Erster. Zweiter.\n"


# ---------------------------------------------------------------- Pipeline


class RecordingTranscriber:
    def __init__(self) -> None:
        self.contexts: list[str | None] = []

    def transcribe(self, recording: Recording, *, context: str = "") -> TranscriptionResult:
        self.contexts.append(context)
        return TranscriptionResult(text="hallo welt")


class RecordingInjector:
    def __init__(self) -> None:
        self.received: list[str] = []

    def inject(self, text: str) -> None:
        self.received.append(text)


CLIP = Recording(samples=np.full(1600, 0.25, dtype=np.float32), sample_rate=RATE)


def test_pipeline_passes_context_and_prefixes_only_the_injection() -> None:
    transcriber, injector = RecordingTranscriber(), RecordingInjector()
    pipe = Pipeline(transcriber, None, injector)
    assert pipe.run(CLIP, context="davor", prefix=" ") == "hallo welt"
    assert transcriber.contexts == ["davor"]
    assert injector.received == [" hallo welt"]


def test_pipeline_without_context_keeps_the_old_call() -> None:
    """Transcriber ohne ``context``-Parameter funktionieren weiter."""

    class Old:
        def transcribe(self, recording: Recording) -> TranscriptionResult:
            return TranscriptionResult(text="alt")

    assert Pipeline(Old()).run(CLIP) == "alt"  # type: ignore[arg-type]


# ---------------------------------------------------------------- Transcriber


def _install_mlx(monkeypatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def transcribe(audio: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"text": "x"}

    module = types.ModuleType("mlx_whisper")
    module.transcribe = transcribe  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mlx_whisper", module)
    return calls


def test_context_follows_the_vocabulary_in_the_prompt(monkeypatch) -> None:
    from flowtext.pipeline.transcribe_mlx import MlxTranscriber

    calls = _install_mlx(monkeypatch)
    transcriber = MlxTranscriber(vocabulary=["FlowText"])
    transcriber.transcribe(CLIP, context="Das war davor.")
    transcriber.transcribe(CLIP)
    assert calls[0]["initial_prompt"] == "FlowText. Das war davor."
    assert calls[1]["initial_prompt"] == "FlowText."


def test_context_alone_becomes_the_prompt_and_is_truncated(monkeypatch) -> None:
    from flowtext.pipeline.transcribe_mlx import MlxTranscriber

    calls = _install_mlx(monkeypatch)
    MlxTranscriber().transcribe(CLIP, context="a" * 500 + "ENDE")
    prompt = calls[0]["initial_prompt"]
    assert prompt.endswith("ENDE") and len(prompt) == 200


# ---------------------------------------------------------------- Config


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_streaming_is_off_by_default_and_configurable(tmp_path: Path) -> None:
    assert Config().streaming.enabled is False
    cfg = load(_write(tmp_path, "[streaming]\nenabled = true\npause_seconds = 1.0\n"))
    assert cfg.streaming.enabled is True
    assert cfg.streaming.pause_seconds == 1.0


@pytest.mark.parametrize(
    "body",
    [
        'enabled = "true"',
        "pause_seconds = 0",
        'pause_seconds = "0.7"',
        "min_segment_seconds = 30\nmax_segment_seconds = 20",
        "max_segment_seconds = 40",
        "min_segment_seconds = true",
    ],
)
def test_invalid_streaming_values_are_rejected(tmp_path: Path, body: str) -> None:
    with pytest.raises(ConfigError, match="streaming"):
        load(_write(tmp_path, f"[streaming]\n{body}\n"))


def test_defaults_validate() -> None:
    validate(Config())
