"""Transkription über ``mlx-whisper`` — Apple-Silicon-native Inferenz.

Zwei Entscheidungen prägen dieses Modul:

**Das Modell wird beim Start geladen, nicht beim ersten Hotkey.** Sonst
wartet man beim ersten Diktat zehn Sekunden und hält das Tool für kaputt.
``mlx-whisper`` bietet kein explizites Laden, deshalb transkribiert
:meth:`warmup` einmal 100 ms Stille — das füllt den internen Cache.

**Die Sprache wird fest vorgegeben.** Auto-Erkennung kostet Zeit und geht
bei kurzen Diktaten regelmäßig daneben ("Danke." als Englisch).
"""

from __future__ import annotations

import logging
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np

from .base import TranscriptionResult

log = logging.getLogger(__name__)


class TranscriptionError(RuntimeError):
    """Die Transkription konnte nicht ausgeführt werden."""


class MlxTranscriber:
    def __init__(
        self,
        model: str = "mlx-community/whisper-large-v3-turbo",
        language: str | None = "de",
        *,
        sample_rate: int = 16_000,
    ) -> None:
        self._model = model
        self._language = language
        self._sample_rate = sample_rate
        self._ready = False

    # ------------------------------------------------------------------ API

    def warmup(self) -> None:
        """Lädt das Modell, indem einmal Stille transkribiert wird."""
        started = time.perf_counter()
        log.info("Lade Whisper-Modell %s …", self._model)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                silence = Path(tmp) / "warmup.wav"
                self._write_silence(silence)
                self._transcribe_file(silence)
        except Exception as exc:  # noqa: BLE001 — Warmup darf den Start nie verhindern
            log.warning(
                "Warmup fehlgeschlagen (%s). Das Modell wird beim ersten Diktat geladen, "
                "das dauert dann einige Sekunden.",
                exc,
            )
            return
        self._ready = True
        log.info("Whisper-Modell bereit (%.1fs).", time.perf_counter() - started)

    def transcribe(self, audio_path: Path) -> TranscriptionResult:
        started = time.perf_counter()
        try:
            result = self._transcribe_file(audio_path)
        except Exception as exc:
            raise TranscriptionError(f"Transkription fehlgeschlagen: {exc}") from exc

        text = str(result.get("text", "")).strip()
        return TranscriptionResult(
            text=text,
            language=str(result.get("language") or self._language or ""),
            seconds=time.perf_counter() - started,
        )

    # -------------------------------------------------------------- intern

    def _transcribe_file(self, audio_path: Path) -> dict[str, Any]:
        import mlx_whisper

        kwargs: dict[str, Any] = {"path_or_hf_repo": self._model}
        if self._language:
            kwargs["language"] = self._language
        result: dict[str, Any] = mlx_whisper.transcribe(str(audio_path), **kwargs)
        return result

    def _write_silence(self, path: Path, seconds: float = 0.1) -> None:
        import soundfile as sf

        samples = np.zeros(int(self._sample_rate * seconds), dtype=np.float32)
        sf.write(str(path), samples, self._sample_rate, subtype="PCM_16")
