"""Schutz vor erfundenem Text bei Stille.

Whisper halluziniert auf Stille und Rauschen zuverlässig Sätze aus seinen
Trainingsdaten — im Deutschen vor allem "Vielen Dank." und Untertitel-
Abspänne. ``no_speech_prob`` hilft beim Turbo-Modell nicht (gemessen: 0,00).
Die Lautstärke trennt die Fälle dagegen sauber.

Zwei Stufen:

1. **Vor** der Transkription: liegt der Pegel unter der Schwelle, wird gar
   nicht erst transkribiert.
2. **Nach** der Transkription: bekannte Phantomsätze werden verworfen —
   Untertitel-Abspänne immer, Höflichkeitsfloskeln nur bei leiser Aufnahme,
   denn "Vielen Dank." kann auch ein echtes Diktat sein.
"""

from __future__ import annotations

import logging
import re

import numpy as np

from ..audio.recorder import Recording

log = logging.getLogger(__name__)

#: Fensterlänge für die Pegelmessung.
_FRAME_SECONDS = 0.05

#: So viele Fenster müssen laut sein (4 × 50 ms = 200 ms, etwa ein kurzes
#: Wort). Ein einzelnes Tastenklacken des Hotkeys zählt damit nicht als Sprache.
_LOUD_FRAMES = 4

#: Floskeln gelten als Phantom, wenn der Pegel unter Schwelle × Faktor liegt.
_QUIET_FACTOR = 4.0

#: Sagt niemand beim Diktieren — immer verwerfen.
_ALWAYS_PHANTOM = re.compile(
    r"untertitel\w* (im auftrag|der amara|von|des zdf|des wdr|des ndr|des br|des swr)"
    r"|amara org|copyright (zdf|wdr|ndr|swr|br)\b"
    r"|subtitles by|transcribed by|thanks for watching|thank you for watching"
    r"|danke fürs zuschauen|vielen dank fürs zuschauen"
)

#: Kann echt sein — nur bei leiser Aufnahme verwerfen.
_QUIET_PHANTOM = frozenset(
    {
        "vielen dank",
        "danke",
        "danke schön",
        "dankeschön",
        "tschüss",
        "bis zum nächsten mal",
        "thank you",
        "thanks",
        "you",
        "bye",
    }
)


def speech_level(recording: Recording) -> float:
    """Pegel der lautesten Stelle, robust gegen kurze Störgeräusche.

    RMS des viertlautesten 50-ms-Fensters. Der Mittelwert über die ganze
    Aufnahme wäre ungeeignet: leise Sprache mit Pausen läge dort kaum über
    dem Grundrauschen. Das absolute Maximum ebenso — ein Tastenklick reicht.
    """
    samples = np.asarray(recording.samples, dtype=np.float64).reshape(-1)
    if len(samples) == 0:
        return 0.0
    frame = max(int(recording.sample_rate * _FRAME_SECONDS), 1)
    count = len(samples) // frame
    if count == 0:
        return float(np.sqrt(np.mean(samples**2)))
    frames = samples[: count * frame].reshape(count, frame)
    rms = np.sqrt(np.mean(frames**2, axis=1))
    k = min(_LOUD_FRAMES, count)
    return float(np.partition(rms, -k)[-k])


def _normalise(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


class SpeechGuard:
    def __init__(self, threshold: float = 0.005) -> None:
        #: ``0`` schaltet die Pegelprüfung ab; Untertitel-Phantome werden
        #: trotzdem verworfen.
        self._threshold = max(float(threshold), 0.0)

    def is_silent(self, recording: Recording) -> bool:
        """Enthält die Aufnahme keine Sprache? Dann nicht transkribieren."""
        if self._threshold <= 0:
            return False
        level = speech_level(recording)
        if level < self._threshold:
            log.info(
                "No speech detected (level %.4f < %.4f) — skipping transcription.",
                level,
                self._threshold,
            )
            return True
        return False

    def is_phantom(self, text: str, recording: Recording) -> bool:
        """Ist ``text`` ein bekannter Whisper-Phantomsatz?"""
        normalised = _normalise(text)
        if not normalised:
            return False
        if _ALWAYS_PHANTOM.search(normalised):
            log.info("Discarded a known Whisper phantom (subtitle credit).")
            return True
        if normalised in _QUIET_PHANTOM and self._threshold > 0:
            level = speech_level(recording)
            if level < self._threshold * _QUIET_FACTOR:
                log.info("Discarded a likely Whisper phantom on quiet audio (level %.4f).", level)
                return True
        return False
