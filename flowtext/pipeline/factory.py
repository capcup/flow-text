"""Baut die Pipeline anhand der Konfiguration zusammen."""

from __future__ import annotations

import logging

from ..config import PipelineConfig
from .base import Injector, Pipeline, Refiner, Transcriber
from .stub import LogInjector, NullRefiner, StubTranscriber

log = logging.getLogger(__name__)


def _build_transcriber(cfg: PipelineConfig) -> Transcriber:
    if cfg.transcriber == "stub":
        return StubTranscriber()
    if cfg.transcriber == "mlx":
        try:
            from .transcribe_mlx import MlxTranscriber
        except ImportError as exc:
            log.error(
                "mlx-whisper ist nicht installiert (%s) — es wird der Stub verwendet. "
                "Installation: pip install 'flowtext[pipeline]'",
                exc,
            )
            return StubTranscriber()
        return MlxTranscriber(model=cfg.whisper_model, language=cfg.language)
    raise ValueError(f"Unbekannter pipeline.transcriber: {cfg.transcriber!r}")


def _build_refiner(cfg: PipelineConfig) -> Refiner:
    if cfg.refiner == "none":
        return NullRefiner()
    if cfg.refiner == "ollama":
        try:
            from .refine_ollama import OllamaRefiner
        except ImportError as exc:
            log.error("ollama ist nicht installiert (%s) — Verfeinerung deaktiviert.", exc)
            return NullRefiner()
        return OllamaRefiner(
            model=cfg.ollama_model,
            prompt=cfg.ollama_prompt,
            timeout=cfg.ollama_timeout,
        )
    raise ValueError(f"Unbekannter pipeline.refiner: {cfg.refiner!r}")


def _build_injector(cfg: PipelineConfig) -> Injector:
    if cfg.injector == "none":
        return LogInjector()
    if cfg.injector == "clipboard":
        try:
            from .inject_macos import ClipboardInjector
        except ImportError as exc:
            log.error("pyperclip ist nicht installiert (%s) — Ausgabe nur ins Log.", exc)
            return LogInjector()
        return ClipboardInjector()
    raise ValueError(f"Unbekannter pipeline.injector: {cfg.injector!r}")


def build_pipeline(cfg: PipelineConfig) -> Pipeline:
    return Pipeline(
        transcriber=_build_transcriber(cfg),
        refiner=_build_refiner(cfg),
        injector=_build_injector(cfg),
    )
