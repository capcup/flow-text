"""Protokolle und Verdrahtung der Pipeline.

Die drei Stufen sind über ``Protocol`` entkoppelt und einzeln austauschbar.
In v1.1 werden hier ``MlxTranscriber``, ``OllamaRefiner`` und
``ClipboardInjector`` eingehängt — die Verdrahtung bleibt unverändert.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    language: str | None = None
    seconds: float = 0.0


@runtime_checkable
class Transcriber(Protocol):
    """Audio → Rohtext.

    Optional: ``warmup()`` zum Vorladen des Modells und ``shutdown()`` zum
    Aufräumen. :class:`Pipeline` ruft beide auf, wenn sie existieren.
    """

    def transcribe(self, audio_path: Path) -> TranscriptionResult: ...


@runtime_checkable
class Refiner(Protocol):
    """Rohtext → aufbereiteter Text. Darf niemals blockieren oder werfen."""

    def refine(self, text: str) -> str: ...


@runtime_checkable
class Injector(Protocol):
    """Text → Zielanwendung."""

    def inject(self, text: str) -> None: ...


class Pipeline:
    """Führt die Stufen aus und schützt jede einzeln gegen Fehler.

    Grundsatz: Scheitert die Verfeinerung, wird trotzdem das Rohtranskript
    ausgegeben. Nur ein Fehlschlag der Transkription bricht ab.
    """

    def __init__(
        self,
        transcriber: Transcriber,
        refiner: Refiner | None = None,
        injector: Injector | None = None,
        *,
        cleaner: Refiner | None = None,
    ) -> None:
        self.transcriber = transcriber
        #: Regelbasierte Bereinigung; läuft vor der (langsameren) Verfeinerung.
        self.cleaner = cleaner
        self.refiner = refiner
        self.injector = injector

    def warmup(self) -> None:
        """Bereitet alle Stufen vor: Modell laden, Dienste prüfen.

        Läuft beim Start, damit das erste Diktat nicht auf einen
        Modell-Download wartet. Ein Fehlschlag darf den Start nie
        verhindern — die Stufe meldet sich dann zur Laufzeit.
        """
        for name, stage in (
            ("Transkription", self.transcriber),
            ("Bereinigung", self.cleaner),
            ("Verfeinerung", self.refiner),
            ("Ausgabe", self.injector),
        ):
            warmup = getattr(stage, "warmup", None)
            if warmup is None:
                continue
            try:
                warmup()
            except Exception:
                log.exception("Warmup der Stufe %s fehlgeschlagen.", name)

    def shutdown(self) -> None:
        """Räumt Stufen auf, die Ressourcen halten (z. B. laufende Timer)."""
        for stage in (self.transcriber, self.cleaner, self.refiner, self.injector):
            shutdown = getattr(stage, "shutdown", None)
            if shutdown is None:
                continue
            try:
                shutdown()
            except Exception:
                log.debug("Shutdown einer Pipeline-Stufe fehlgeschlagen.", exc_info=True)

    def run(self, audio_path: Path) -> str:
        started = time.perf_counter()
        result = self.transcriber.transcribe(audio_path)
        text = result.text.strip()
        log.info(
            "Transcribed in %.2fs: %r",
            time.perf_counter() - started,
            text[:120] + ("…" if len(text) > 120 else ""),
        )
        if not text:
            log.warning("Empty transcript — nothing to output.")
            return ""

        if self.cleaner is not None:
            try:
                text = self.cleaner.refine(text).strip()
            except Exception:
                log.exception("Clean-up failed — using raw transcript.")
            if not text:
                log.info("Nothing left after clean-up — nothing to output.")
                return ""

        if self.refiner is not None:
            try:
                refined = self.refiner.refine(text)
                if refined.strip():
                    text = refined.strip()
            except Exception:
                log.exception("Refinement failed — using raw transcript.")

        if self.injector is not None:
            self.injector.inject(text)

        log.info("Finished in %.2fs.", time.perf_counter() - started)
        return text
