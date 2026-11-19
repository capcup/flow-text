"""Teilt eine laufende Aufnahme an Sprechpausen in Abschnitte.

Grundlage der abschnittsweisen Ausgabe: Whisper kann nicht streamen, wohl
aber einzelne fertige Abschnitte schnell transkribieren (~1 s). Der
:class:`Segmenter` bekommt die Samples in kleinen Häppchen und gibt einen
Abschnitt frei, sobald

* der offene Abschnitt Sprache enthält,
* mindestens ``min_seconds`` lang ist und
* die Aufnahme seit ``pause_seconds`` still ist.

Wird ein Abschnitt länger als ``max_seconds`` (Whisper sieht höchstens 30 s),
wird an der leisesten Stelle der letzten Sekunden getrennt.

Stille ohne Sprache wird bis auf einen kurzen Rest verworfen: sonst entstünden
stille Abschnitte, und der Anfang des nächsten Wortes ginge nicht verloren,
weil ein halbes Wort Vorlauf stehen bleibt.

Reine Logik auf Samples, ohne Threads und ohne Mikrofon — dadurch ohne Gerät
testbar.
"""

from __future__ import annotations

import numpy as np

from .recorder import AudioArray

#: Fensterlänge der Pegelmessung (wie ``pipeline.guard``).
_FRAME_SECONDS = 0.05

#: So viele laute Fenster gelten als Sprache (4 × 50 ms = 200 ms), ein
#: Tastenklick also nicht.
_MIN_SPEECH_FRAMES = 4

#: In diesem Zeitfenster vor dem Ende wird bei Überlänge getrennt.
_FORCED_WINDOW_SECONDS = 5.0

#: Reine Stille wird gekürzt, sobald sie länger ist als ...
_TRIM_AFTER_SECONDS = 1.0
#: ... und es bleibt dieser Vorlauf stehen.
_KEEP_LEADING_SECONDS = 0.5


class Segmenter:
    def __init__(
        self,
        sample_rate: int,
        *,
        level: float = 0.005,
        pause_seconds: float = 0.7,
        min_seconds: float = 2.0,
        max_seconds: float = 20.0,
    ) -> None:
        self._rate = sample_rate
        self._level = level
        self._pause = pause_seconds
        self._min = min_seconds
        self._max = max_seconds
        self._frame = max(int(sample_rate * _FRAME_SECONDS), 1)
        self._samples: AudioArray = np.zeros(0, dtype=np.float32)

    # ------------------------------------------------------------------ API

    def feed(self, samples: AudioArray) -> list[AudioArray]:
        """Nimmt neue Samples entgegen und gibt fertige Abschnitte zurück."""
        chunk = np.asarray(samples, dtype=np.float32).reshape(-1)
        if len(chunk):
            self._samples = np.concatenate([self._samples, chunk])

        done: list[AudioArray] = []
        while True:
            cut = self._find_cut()
            if cut is None:
                return done
            done.append(self._samples[:cut])
            self._samples = self._samples[cut:]

    def flush(self) -> AudioArray:
        """Der noch offene Rest — beim Stopp der Aufnahme."""
        rest, self._samples = self._samples, np.zeros(0, dtype=np.float32)
        return rest

    # -------------------------------------------------------------- intern

    def _frame_levels(self) -> AudioArray:
        count = len(self._samples) // self._frame
        if count == 0:
            return np.zeros(0, dtype=np.float32)
        frames = self._samples[: count * self._frame].reshape(count, self._frame)
        levels: AudioArray = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1)).astype(
            np.float32
        )
        return levels

    def _find_cut(self) -> int | None:
        levels = self._frame_levels()
        if len(levels) == 0:
            return None

        loud = levels >= self._level
        duration = len(self._samples) / self._rate

        if int(loud.sum()) < _MIN_SPEECH_FRAMES:
            if duration > _TRIM_AFTER_SECONDS:
                keep = int(self._rate * _KEEP_LEADING_SECONDS)
                self._samples = self._samples[-keep:]
            return None

        frame_seconds = self._frame / self._rate
        trailing_silence = (len(levels) - 1 - int(np.flatnonzero(loud)[-1])) * frame_seconds
        if duration >= self._min and trailing_silence >= self._pause:
            return len(levels) * self._frame

        if duration >= self._max:
            return self._quietest_cut(levels, frame_seconds)
        return None

    def _quietest_cut(self, levels: AudioArray, frame_seconds: float) -> int:
        """Trennstelle bei Überlänge: das leiseste Fenster der letzten Sekunden."""
        count = len(levels)
        start = max(count - int(_FORCED_WINDOW_SECONDS / frame_seconds), 1)
        smooth = np.convolve(levels, np.ones(3, dtype=np.float32) / 3.0, mode="same")
        quietest = start + int(np.argmin(smooth[start : count - 1]))
        return (quietest + 1) * self._frame
