"""Baut die Pipeline anhand der Konfiguration zusammen.

Die schweren Abhängigkeiten (``mlx-whisper``, ``ollama``, ``pyperclip``)
werden erst importiert, wenn die jeweilige Stufe tatsächlich gewählt ist.
So startet der Stub-Betrieb ohne installierte Pipeline-Extras.

Unbekannte Werte fängt bereits :func:`flowtext.config.validate` beim Start
ab; hier landet nur, was gebaut werden kann.
"""

from __future__ import annotations

import importlib.util
import logging
import sys

from ..config import CleanupConfig, PipelineConfig
from .base import Injector, Pipeline, Refiner, Transcriber
from .guard import SpeechGuard
from .stub import LogInjector, NullRefiner, StubTranscriber

log = logging.getLogger(__name__)

_INSTALL_HINT = "Installation: pip install 'flowtext[pipeline]'"


def _available(module: str) -> bool:
    """Ist das Paket installiert? Prüft ohne es zu importieren.

    Wichtig, damit ein fehlendes Extra beim *Start* auffällt und nicht erst
    beim ersten Diktat — die schweren Importe passieren sonst erst dort.
    """
    if sys.modules.get(module) is not None:
        return True
    if module in sys.modules and sys.modules[module] is None:
        return False
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _build_transcriber(cfg: PipelineConfig) -> Transcriber:
    if cfg.transcriber == "stub":
        return StubTranscriber()

    if not _available("mlx_whisper"):
        log.error(
            "pipeline.transcriber = 'mlx', aber mlx-whisper ist nicht installiert. "
            "%s. Bis dahin wird nichts transkribiert.",
            _INSTALL_HINT,
        )
        return StubTranscriber()

    from .transcribe_mlx import MlxTranscriber

    return MlxTranscriber(model=cfg.whisper_model, language=cfg.language, vocabulary=cfg.vocabulary)


def _build_refiner(cfg: PipelineConfig) -> Refiner:
    if cfg.refiner == "none":
        return NullRefiner()

    if not _available("ollama"):
        log.error(
            "pipeline.refiner = 'ollama', aber die ollama-Bibliothek fehlt. %s. "
            "Verfeinerung ist deaktiviert.",
            _INSTALL_HINT,
        )
        return NullRefiner()

    from .refine_ollama import OllamaRefiner

    return OllamaRefiner(
        model=cfg.ollama_model,
        prompt=cfg.ollama_prompt,
        timeout=cfg.ollama_timeout,
    )


def _build_injector(cfg: PipelineConfig) -> Injector:
    if cfg.injector == "none":
        return LogInjector()

    if not _available("pyperclip"):
        log.error(
            "pipeline.injector = 'clipboard', aber pyperclip fehlt. %s. "
            "Der Text landet nur im Log.",
            _INSTALL_HINT,
        )
        return LogInjector()

    from .inject_macos import ClipboardInjector

    return ClipboardInjector(paste=cfg.paste, restore_after=cfg.restore_clipboard_after)


def _build_cleaner(cfg: PipelineConfig, cleanup: CleanupConfig | None) -> Refiner | None:
    if cleanup is None or not (cleanup.remove_fillers or cleanup.replacements):
        return None

    from .cleanup_rules import RuleBasedCleaner

    return RuleBasedCleaner(
        language=cfg.language,
        remove_fillers=cleanup.remove_fillers,
        extra_fillers=cleanup.extra_fillers,
        replacements=cleanup.replacements,
    )


def build_pipeline(cfg: PipelineConfig, cleanup: CleanupConfig | None = None) -> Pipeline:
    """Erzeugt die Pipeline; fehlende Extras degradieren sichtbar auf den Stub."""
    return Pipeline(
        transcriber=_build_transcriber(cfg),
        refiner=_build_refiner(cfg),
        injector=_build_injector(cfg),
        cleaner=_build_cleaner(cfg, cleanup),
        guard=SpeechGuard(cfg.silence_threshold),
    )
