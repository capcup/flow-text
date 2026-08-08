"""Deadlockfreie Mikrofonaufnahme.

Die zentrale Regel: **der PortAudio-Callback nimmt niemals ein Lock.**

Die ursprüngliche Implementierung hielt in ``stop_recording()`` ein Lock,
während sie ``stream.stop()`` aufrief. ``stream.stop()`` wartet aber auf das
Ende des laufenden Audio-Callbacks — und der versuchte, genau dasselbe Lock
zu nehmen. Ergebnis: sporadischer Deadlock, der (weil er im Hotkey-Thread
passierte) gleich den Event-Tap mit in den Tod riss.

Hier übergibt der Callback seine Puffer über eine ``queue.Queue``. Die ist
für den Producer lock-frei genug und blockiert nie.
"""

from __future__ import annotations

import logging
import queue
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ..config import AudioConfig

log = logging.getLogger(__name__)


class AudioError(RuntimeError):
    """Aufnahme konnte nicht gestartet oder beendet werden."""


@dataclass(frozen=True)
class Recording:
    samples: np.ndarray
    sample_rate: int

    @property
    def duration(self) -> float:
        return len(self.samples) / float(self.sample_rate)

    @property
    def peak(self) -> float:
        return float(np.max(np.abs(self.samples))) if len(self.samples) else 0.0


#: Signatur einer Stream-Fabrik — für Tests austauschbar.
StreamFactory = Callable[..., Any]


def _default_stream_factory(**kwargs: Any) -> Any:
    import sounddevice as sd

    return sd.InputStream(**kwargs)


class AudioRecorder:
    def __init__(
        self,
        cfg: AudioConfig,
        stream_factory: StreamFactory | None = None,
    ) -> None:
        self._cfg = cfg
        self._make_stream = stream_factory or _default_stream_factory
        self._chunks: queue.Queue[np.ndarray] = queue.Queue()
        self._stream: Any = None
        self._started_at: float | None = None
        self._max_frames = int(cfg.sample_rate * cfg.max_seconds)
        self._overflow_logged = False

    # ------------------------------------------------------------------ API

    @property
    def is_recording(self) -> bool:
        return self._stream is not None

    @property
    def elapsed(self) -> float:
        return 0.0 if self._started_at is None else time.monotonic() - self._started_at

    def start(self) -> None:
        """Startet den Stream.

        Bewusst OHNE Lock: ``stream.start()`` kann den Callback feuern, bevor
        es zurückkehrt. Ein Lock an dieser Stelle wäre ein Deadlock.
        """
        if self._stream is not None:
            raise AudioError("Aufnahme läuft bereits")

        self._drain()
        self._overflow_logged = False
        try:
            stream = self._make_stream(
                samplerate=self._cfg.sample_rate,
                channels=self._cfg.channels,
                dtype="float32",
                blocksize=self._cfg.blocksize,
                device=self._cfg.device,
                callback=self._callback,
            )
            stream.start()
        except Exception as exc:  # sounddevice wirft PortAudioError & Co.
            raise AudioError(f"Mikrofon konnte nicht geöffnet werden: {exc}") from exc

        self._stream = stream
        self._started_at = time.monotonic()

    def stop(self) -> Recording | None:
        """Beendet den Stream und gibt die Aufnahme zurück.

        Gibt ``None`` zurück, wenn nichts Verwertbares aufgenommen wurde —
        das ist ein normaler Fall (Fehlbedienung), keine Ausnahme.
        """
        stream, self._stream = self._stream, None
        self._started_at = None

        if stream is not None:
            try:
                stream.stop()
            except Exception as exc:  # noqa: BLE001 — Gerät abgezogen o. Ä.
                log.warning("Fehler beim Stoppen des Audio-Streams: %s", exc)
            finally:
                try:
                    stream.close()
                except Exception as exc:  # noqa: BLE001
                    log.warning("Fehler beim Schließen des Audio-Streams: %s", exc)

        chunks = self._drain()
        if not chunks:
            log.warning("Keine Audiodaten erfasst — ist die Mikrofonberechtigung erteilt?")
            return None

        samples = np.concatenate(chunks, axis=0).reshape(-1)
        if len(samples) > self._max_frames:
            log.info("Aufnahme auf %.0fs gekürzt.", self._cfg.max_seconds)
            samples = samples[: self._max_frames]

        recording = Recording(samples=samples, sample_rate=self._cfg.sample_rate)
        if recording.duration < self._cfg.min_seconds:
            log.info("Aufnahme mit %.2fs zu kurz — verworfen.", recording.duration)
            return None
        return recording

    def abort(self) -> None:
        """Bricht ab und verwirft alles (z. B. beim Herunterfahren)."""
        stream, self._stream = self._stream, None
        self._started_at = None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:  # noqa: BLE001
                pass
        self._drain()

    def save(self, recording: Recording, path: Path) -> Path:
        import soundfile as sf

        path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(path), recording.samples, recording.sample_rate, subtype="PCM_16")
        return path

    # -------------------------------------------------------------- intern

    def _callback(self, indata: Any, frames: int, time_info: Any, status: Any) -> None:
        """Realtime-Thread: kein Lock, keine I/O, kein Logging im Normalfall."""
        if status and not self._overflow_logged:
            self._overflow_logged = True  # nur einmal, sonst flutet es das Log
        self._chunks.put_nowait(np.asarray(indata).copy())

    def _drain(self) -> list[np.ndarray]:
        out: list[np.ndarray] = []
        while True:
            try:
                out.append(self._chunks.get_nowait())
            except queue.Empty:
                return out
