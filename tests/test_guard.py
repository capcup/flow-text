from __future__ import annotations

import numpy as np
import pytest

from flowtext.audio.recorder import Recording
from flowtext.config import PipelineConfig
from flowtext.pipeline.base import Pipeline, TranscriptionResult
from flowtext.pipeline.factory import build_pipeline
from flowtext.pipeline.guard import SpeechGuard, speech_level

RATE = 16_000


def _clip(*parts: tuple[float, float], seed: int = 0) -> Recording:
    """Baut eine Aufnahme aus (Sekunden, Rausch-Sigma)-Abschnitten."""
    rng = np.random.default_rng(seed)
    chunks = [rng.normal(0.0, sigma, int(RATE * seconds)) for seconds, sigma in parts]
    return Recording(samples=np.concatenate(chunks).astype(np.float32), sample_rate=RATE)


SILENCE = Recording(samples=np.zeros(RATE * 2, dtype=np.float32), sample_rate=RATE)
NOISE = _clip((3.0, 0.003))  # gemessenes Grundrauschen, ergab "Vielen Dank."
SPEECH = _clip((0.5, 0.001), (1.0, 0.1), (0.5, 0.001))
QUIET_SPEECH = _clip((2.0, 0.001), (0.4, 0.012), (2.0, 0.001))


class TestSpeechLevel:
    def test_orders_silence_noise_and_speech(self) -> None:
        assert speech_level(SILENCE) == 0.0
        assert speech_level(NOISE) == pytest.approx(0.003, rel=0.2)
        assert speech_level(SPEECH) == pytest.approx(0.1, rel=0.2)

    def test_a_single_click_is_not_speech(self) -> None:
        samples = np.zeros(RATE, dtype=np.float32)
        samples[4000:4160] = 0.9  # 10 ms Tastenklick
        assert speech_level(Recording(samples=samples, sample_rate=RATE)) == 0.0

    def test_pauses_do_not_hide_quiet_speech(self) -> None:
        """Der Mittelwert läge hier nahe am Rauschen — die lauteste Stelle nicht."""
        assert speech_level(QUIET_SPEECH) > 0.008

    def test_empty_and_tiny_recordings(self) -> None:
        empty = Recording(samples=np.zeros(0, dtype=np.float32), sample_rate=RATE)
        tiny = Recording(samples=np.full(100, 0.5, dtype=np.float32), sample_rate=RATE)
        assert speech_level(empty) == 0.0
        assert speech_level(tiny) == pytest.approx(0.5)


class TestSpeechGuard:
    def test_silence_and_noise_are_gated(self) -> None:
        guard = SpeechGuard(0.005)
        assert guard.is_silent(SILENCE)
        assert guard.is_silent(NOISE)
        assert not guard.is_silent(SPEECH)
        assert not guard.is_silent(QUIET_SPEECH)

    def test_zero_disables_the_gate(self) -> None:
        assert not SpeechGuard(0).is_silent(SILENCE)

    @pytest.mark.parametrize(
        "text",
        [
            "Untertitel im Auftrag des ZDF für funk, 2017",
            "Untertitel der Amara.org-Community",
            "Untertitelung des ZDF, 2020",
            "Thanks for watching!",
        ],
    )
    def test_subtitle_credits_are_always_phantoms(self, text: str) -> None:
        assert SpeechGuard(0.005).is_phantom(text, SPEECH)
        assert SpeechGuard(0).is_phantom(text, SPEECH)

    def test_courtesies_are_phantoms_only_on_quiet_audio(self) -> None:
        guard = SpeechGuard(0.005)
        assert guard.is_phantom("Vielen Dank.", QUIET_SPEECH)
        assert not guard.is_phantom("Vielen Dank.", SPEECH)  # echt diktiert
        assert not guard.is_phantom("Vielen Dank für die Rückmeldung.", QUIET_SPEECH)
        assert not SpeechGuard(0).is_phantom("Vielen Dank.", QUIET_SPEECH)

    def test_ordinary_text_is_never_a_phantom(self) -> None:
        guard = SpeechGuard(0.005)
        assert not guard.is_phantom("Der Untertitel fehlt noch.", QUIET_SPEECH)
        assert not guard.is_phantom("", SILENCE)


# ----------------------------------------------------------------- Verdrahtung


class _Transcriber:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    def transcribe(self, recording: Recording) -> TranscriptionResult:
        self.calls += 1
        return TranscriptionResult(text=self.text)


class _Injector:
    def __init__(self) -> None:
        self.received: list[str] = []

    def inject(self, text: str) -> None:
        self.received.append(text)


def test_silence_is_not_even_transcribed() -> None:
    transcriber, injector = _Transcriber("Vielen Dank."), _Injector()
    pipe = Pipeline(transcriber, None, injector, guard=SpeechGuard(0.005))
    assert pipe.run(NOISE) == ""
    assert transcriber.calls == 0
    assert injector.received == []


def test_phantom_after_transcription_is_not_pasted() -> None:
    transcriber, injector = _Transcriber("Untertitel der Amara.org-Community"), _Injector()
    pipe = Pipeline(transcriber, None, injector, guard=SpeechGuard(0.005))
    assert pipe.run(SPEECH) == ""
    assert transcriber.calls == 1
    assert injector.received == []


def test_speech_passes_through() -> None:
    injector = _Injector()
    pipe = Pipeline(_Transcriber("Vielen Dank."), None, injector, guard=SpeechGuard(0.005))
    assert pipe.run(SPEECH) == "Vielen Dank."
    assert injector.received == ["Vielen Dank."]


def test_factory_wires_the_threshold() -> None:
    cfg = PipelineConfig(transcriber="stub", injector="none", silence_threshold=0.5)
    pipe = build_pipeline(cfg)
    assert pipe.guard is not None
    assert pipe.guard.is_silent(SPEECH)
