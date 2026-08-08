"""Tests des AudioRecorders mit einer Fake-Stream-Fabrik (kein PortAudio nötig)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from flowtext.audio.recorder import AudioError, AudioRecorder
from flowtext.config import AudioConfig


class FakeStream:
    """Minimaler Ersatz für ``sd.InputStream``."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.callback = kwargs["callback"]
        self.started = False
        self.closed = False
        self.stop_error: Exception | None = None

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        if self.stop_error:
            raise self.stop_error
        self.started = False

    def close(self) -> None:
        self.closed = True

    # Testhilfe: simuliert PortAudio, das Puffer liefert
    def feed(self, frames: int, amplitude: float = 0.5) -> None:
        block = np.full((frames, 1), amplitude, dtype=np.float32)
        self.callback(block, frames, None, None)


@pytest.fixture
def cfg() -> AudioConfig:
    return AudioConfig(sample_rate=16000, min_seconds=0.3, max_seconds=2.0, blocksize=1024)


@pytest.fixture
def recorder_and_streams(cfg: AudioConfig) -> tuple[AudioRecorder, list[FakeStream]]:
    created: list[FakeStream] = []

    def factory(**kwargs: Any) -> FakeStream:
        stream = FakeStream(**kwargs)
        created.append(stream)
        return stream

    return AudioRecorder(cfg, stream_factory=factory), created


def test_records_and_returns_duration(recorder_and_streams) -> None:
    rec, streams = recorder_and_streams
    rec.start()
    assert rec.is_recording
    streams[0].feed(16000)  # 1 Sekunde
    result = rec.stop()
    assert result is not None
    assert result.duration == pytest.approx(1.0)
    assert result.peak == pytest.approx(0.5)
    assert not rec.is_recording
    assert streams[0].closed


def test_no_data_returns_none(recorder_and_streams) -> None:
    rec, _ = recorder_and_streams
    rec.start()
    assert rec.stop() is None


def test_too_short_is_discarded(recorder_and_streams) -> None:
    """Regression: Fehlbedienung darf keine 0.05s-Datei erzeugen."""
    rec, streams = recorder_and_streams
    rec.start()
    streams[0].feed(800)  # 0.05 s < min_seconds
    assert rec.stop() is None


def test_max_length_is_truncated(recorder_and_streams) -> None:
    rec, streams = recorder_and_streams
    rec.start()
    for _ in range(5):
        streams[0].feed(16000)  # 5 s bei max_seconds = 2.0
    result = rec.stop()
    assert result is not None
    assert result.duration == pytest.approx(2.0)


def test_double_start_raises(recorder_and_streams) -> None:
    rec, _ = recorder_and_streams
    rec.start()
    with pytest.raises(AudioError):
        rec.start()


def test_stop_without_start_returns_none(recorder_and_streams) -> None:
    """Regression: früher warnte das nur und lief weiter."""
    rec, _ = recorder_and_streams
    assert rec.stop() is None


def test_stream_failure_becomes_audio_error(cfg: AudioConfig) -> None:
    def failing(**kwargs: Any) -> FakeStream:
        raise OSError("kein Gerät")

    rec = AudioRecorder(cfg, stream_factory=failing)
    with pytest.raises(AudioError, match="Mikrofon"):
        rec.start()
    assert not rec.is_recording


def test_device_unplugged_still_returns_data(recorder_and_streams) -> None:
    """Gerät während der Aufnahme abgezogen: Daten retten statt abstürzen."""
    rec, streams = recorder_and_streams
    rec.start()
    streams[0].feed(16000)
    streams[0].stop_error = OSError("device disconnected")
    result = rec.stop()
    assert result is not None
    assert result.duration == pytest.approx(1.0)


def test_restart_drains_previous_buffer(recorder_and_streams) -> None:
    rec, streams = recorder_and_streams
    rec.start()
    streams[0].feed(16000)
    rec.stop()
    rec.start()
    streams[1].feed(16000)
    result = rec.stop()
    assert result is not None
    assert result.duration == pytest.approx(1.0)


def test_abort_discards_everything(recorder_and_streams) -> None:
    rec, streams = recorder_and_streams
    rec.start()
    streams[0].feed(16000)
    rec.abort()
    assert not rec.is_recording
    rec.start()
    streams[1].feed(16000)
    result = rec.stop()
    assert result is not None
    assert result.duration == pytest.approx(1.0)


def test_callback_never_blocks_on_a_lock(recorder_and_streams) -> None:
    """Regression: der alte Code nahm im Audio-Callback dasselbe Lock,
    das stop() über stream.stop() hinweg hielt → Deadlock."""
    rec, streams = recorder_and_streams
    rec.start()
    stream = streams[0]

    # stream.stop() feuert -- wie PortAudio -- noch einen Callback.
    original_stop = stream.stop

    def stop_with_late_callback() -> None:
        stream.feed(16000)
        original_stop()

    stream.stop = stop_with_late_callback  # type: ignore[method-assign]
    result = rec.stop()
    assert result is not None  # ohne Deadlock zurückgekehrt
