from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from flowtext.audio.recorder import Recording
from flowtext.pipeline.base import Pipeline, TranscriptionResult

#: Eine kurze Aufnahme im Speicher — die Fakes werten sie nicht aus.
CLIP = Recording(samples=np.zeros(1600, dtype=np.float32), sample_rate=16_000)


class FakeTranscriber:
    def __init__(self, text: str = "hallo welt", error: Exception | None = None) -> None:
        self.text = text
        self.error = error
        self.warmed = False

    def warmup(self) -> None:
        self.warmed = True

    def transcribe(self, recording: Recording) -> TranscriptionResult:
        if self.error:
            raise self.error
        return TranscriptionResult(text=self.text, language="de")


class FakeRefiner:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    def refine(self, text: str) -> str:
        if self.error:
            raise self.error
        return text.capitalize() + "."


class RecordingInjector:
    def __init__(self) -> None:
        self.received: list[str] = []

    def inject(self, text: str) -> None:
        self.received.append(text)


def test_full_pipeline(tmp_path: Path) -> None:
    injector = RecordingInjector()
    pipe = Pipeline(FakeTranscriber(), FakeRefiner(), injector)
    assert pipe.run(CLIP) == "Hallo welt."
    assert injector.received == ["Hallo welt."]


def test_refiner_failure_falls_back_to_raw_text(tmp_path: Path) -> None:
    """Ollama aus? Dann eben das Rohtranskript — aber niemals nichts."""
    injector = RecordingInjector()
    pipe = Pipeline(FakeTranscriber(), FakeRefiner(RuntimeError("ollama weg")), injector)
    assert pipe.run(CLIP) == "hallo welt"
    assert injector.received == ["hallo welt"]


def test_empty_refinement_keeps_raw_text(tmp_path: Path) -> None:
    class BlankRefiner:
        def refine(self, text: str) -> str:
            return "   "

    injector = RecordingInjector()
    pipe = Pipeline(FakeTranscriber(), BlankRefiner(), injector)
    assert pipe.run(CLIP) == "hallo welt"


def test_empty_transcript_skips_injection(tmp_path: Path) -> None:
    injector = RecordingInjector()
    pipe = Pipeline(FakeTranscriber(text="   "), FakeRefiner(), injector)
    assert pipe.run(CLIP) == ""
    assert injector.received == []


def test_transcription_failure_propagates(tmp_path: Path) -> None:
    pipe = Pipeline(FakeTranscriber(error=RuntimeError("Modell fehlt")))
    with pytest.raises(RuntimeError):
        pipe.run(CLIP)


def test_warmup_failure_is_swallowed() -> None:
    """Ein fehlgeschlagenes Warmup darf den Start nicht verhindern."""

    class BadTranscriber(FakeTranscriber):
        def warmup(self) -> None:
            raise RuntimeError("kein Netz")

    Pipeline(BadTranscriber()).warmup()  # darf nicht werfen


def test_warmup_is_called() -> None:
    transcriber = FakeTranscriber()
    Pipeline(transcriber).warmup()
    assert transcriber.warmed
