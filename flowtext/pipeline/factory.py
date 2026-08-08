"""Baut die Pipeline anhand der Konfiguration zusammen.

Aktuell ist nur der Stub-Betrieb implementiert. Die Stufen für v1.2
(``mlx-whisper``, Ollama, Clipboard-Injektion) werden hier eingehängt,
sobald sie existieren — die Verdrahtung in :mod:`flowtext.pipeline.base`
bleibt dabei unverändert.

Unbekannte oder noch nicht implementierte Werte fängt bereits
:func:`flowtext.config.validate` beim Start ab, mit einem Hinweis auf die
Roadmap. Hier landet also nur, was tatsächlich gebaut werden kann.
"""

from __future__ import annotations

from ..config import PipelineConfig
from .base import Injector, Pipeline, Refiner, Transcriber
from .stub import LogInjector, NullRefiner, StubTranscriber

_TRANSCRIBERS: dict[str, type[Transcriber]] = {"stub": StubTranscriber}
_REFINERS: dict[str, type[Refiner]] = {"none": NullRefiner}
_INJECTORS: dict[str, type[Injector]] = {"none": LogInjector}


def build_pipeline(cfg: PipelineConfig) -> Pipeline:
    return Pipeline(
        transcriber=_TRANSCRIBERS[cfg.transcriber](),
        refiner=_REFINERS[cfg.refiner](),
        injector=_INJECTORS[cfg.injector](),
    )
