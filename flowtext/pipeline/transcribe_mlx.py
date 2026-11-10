"""Transkription über ``mlx-whisper`` — Apple-Silicon-native Inferenz.

Zwei Entscheidungen prägen dieses Modul:

**Das Modell wird beim Start geladen, nicht beim ersten Hotkey.** Sonst
wartet man beim ersten Diktat zehn Sekunden und hält das Tool für kaputt.
``mlx-whisper`` bietet kein explizites Laden, deshalb transkribiert
:meth:`warmup` einmal 100 ms Stille — das füllt den internen Cache.

**Die Samples gehen direkt aus dem Speicher an Whisper.** Ein Dateipfad
würde ``mlx-whisper`` dazu bringen, das externe ``ffmpeg`` zu starten —
eine versteckte Abhängigkeit, ohne die jedes Diktat scheitert.

**Die Sprache wird fest vorgegeben.** Auto-Erkennung kostet Zeit und geht
bei kurzen Diktaten regelmäßig daneben ("Danke." als Englisch).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from typing import Any

import numpy as np

from ..audio.recorder import AudioArray, Recording
from .base import TranscriptionResult

log = logging.getLogger(__name__)

#: Whisper erwartet 16 kHz Mono als float32.
_WHISPER_RATE = 16_000

#: Whisper nutzt höchstens ~224 Tokens Vortext und schneidet VORN ab. Als
#: grobe Faustregel für Deutsch: 3 Zeichen pro Token.
_PROMPT_CHAR_BUDGET = 600


def vocabulary_prompt(vocabulary: Iterable[str]) -> str | None:
    """Baut aus der Vokabelliste den Vortext (``initial_prompt``) für Whisper.

    Bewusst eine schlichte, saubere Aufzählung: Whisper übernimmt neben der
    Schreibweise auch den Stil des Vortexts.
    """
    terms = list(dict.fromkeys(t.strip() for t in vocabulary if t.strip()))
    if not terms:
        return None
    prompt = ", ".join(terms) + "."
    if len(prompt) > _PROMPT_CHAR_BUDGET:
        log.warning(
            "pipeline.vocabulary ist mit %d Zeichen vermutlich zu lang für Whisper "
            "(~224 Tokens); die ersten Einträge werden dann ignoriert. Liste kürzen.",
            len(prompt),
        )
    return prompt


class TranscriptionError(RuntimeError):
    """Die Transkription konnte nicht ausgeführt werden."""


class MlxTranscriber:
    def __init__(
        self,
        model: str = "mlx-community/whisper-large-v3-turbo",
        language: str | None = "de",
        *,
        vocabulary: Iterable[str] = (),
    ) -> None:
        self._model = model
        self._language = language
        self._initial_prompt = vocabulary_prompt(vocabulary)
        self._ready = False
        self._warned_rate = False

    # ------------------------------------------------------------------ API

    def warmup(self) -> None:
        """Lädt das Modell, indem einmal Stille transkribiert wird."""
        started = time.perf_counter()
        log.info("Loading Whisper model %s …", self._model)
        try:
            self._transcribe(np.zeros(_WHISPER_RATE // 10, dtype=np.float32))
        except Exception as exc:  # noqa: BLE001 — Warmup darf den Start nie verhindern
            log.warning(
                "Warmup failed (%s). The model will be loaded on first dictation, "
                "which may take several seconds.",
                exc,
            )
            return
        self._ready = True
        log.info("Whisper model ready (%.1fs).", time.perf_counter() - started)

    def transcribe(self, recording: Recording) -> TranscriptionResult:
        started = time.perf_counter()
        try:
            result = self._transcribe(self._prepare(recording))
        except Exception as exc:
            raise TranscriptionError(f"Transkription fehlgeschlagen: {exc}") from exc

        text = str(result.get("text", "")).strip()
        return TranscriptionResult(
            text=text,
            language=str(result.get("language") or self._language or ""),
            seconds=time.perf_counter() - started,
        )

    # -------------------------------------------------------------- intern

    def _transcribe(self, samples: AudioArray) -> dict[str, Any]:
        import mlx_whisper

        kwargs: dict[str, Any] = {"path_or_hf_repo": self._model}
        if self._language:
            kwargs["language"] = self._language
        if self._initial_prompt:
            # Wirkt als Hinweis, nicht als Garantie. Bei langen Diktaten
            # verdrängt der bereits erkannte Text den Vortext nach und nach.
            kwargs["initial_prompt"] = self._initial_prompt
        result: dict[str, Any] = mlx_whisper.transcribe(samples, **kwargs)
        return result

    def _prepare(self, recording: Recording) -> AudioArray:
        """Bringt die Aufnahme ins Whisper-Format: float32, mono, 16 kHz."""
        samples: AudioArray = np.ascontiguousarray(recording.samples, dtype=np.float32).reshape(-1)
        rate = recording.sample_rate
        if rate == _WHISPER_RATE or len(samples) == 0:
            return samples

        if not self._warned_rate:
            self._warned_rate = True
            log.warning(
                "audio.sample_rate = %d wird auf %d Hz umgerechnet (einfache lineare "
                "Interpolation). Für beste Erkennung audio.sample_rate = 16000 setzen.",
                rate,
                _WHISPER_RATE,
            )
        target = round(len(samples) * _WHISPER_RATE / rate)
        positions = np.linspace(0.0, len(samples) - 1, num=max(target, 1))
        resampled: AudioArray = np.interp(positions, np.arange(len(samples)), samples).astype(
            np.float32
        )
        return resampled
