"""Platzhalter-Stufen für Phase 3.

Der ``StubTranscriber`` transkribiert nicht, sondern protokolliert nur, dass
eine verwertbare Audiodatei bereitsteht. Damit lässt sich die komplette
Hotkey- und Aufnahmekette testen, bevor mlx-whisper ins Spiel kommt.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .base import TranscriptionResult

log = logging.getLogger(__name__)


class StubTranscriber:
    def warmup(self) -> None:
        log.info("Transkription: Stub-Modus — es wird nichts erkannt.")

    def transcribe(self, audio_path: Path) -> TranscriptionResult:
        size_kb = audio_path.stat().st_size / 1024 if audio_path.exists() else 0.0
        log.info(
            "⚡ Audio bereit für Inferenz: %s (%.1f kB). "
            'Stub-Modus — für echte Transkription pipeline.transcriber = "mlx" setzen.',
            audio_path,
            size_kb,
        )
        return TranscriptionResult(text="", language=None)


class NullRefiner:
    def refine(self, text: str) -> str:
        return text


class LogInjector:
    def inject(self, text: str) -> None:
        log.info("Ausgabe (nur Log): %s", text)
