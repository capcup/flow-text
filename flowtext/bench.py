"""Benchmark: Latenz und Wortfehlerrate auf echten Aufnahmen.

Ohne Messung bleibt jede Aussage über Erkennungsqualität geschätzt. Dieses
Modul lässt die Pipeline — genau so, wie die App sie baut, nur ohne
Einfügen — über einen Ordner mit Aufnahmen laufen::

    diktate/
        einkauf.wav     # 16 kHz mono ist ideal, anderes wird umgerechnet
        einkauf.txt     # was wirklich gesagt wurde (optional)

Fehlt die ``.txt``, wird nur die Latenz gemessen. Eine **leere** ``.txt``
bedeutet "hier soll nichts erkannt werden" (Stille, Rauschen).

Aufnahmen sammeln: ``audio.delete_after_use = false`` legt jedes Diktat samt
Transkript im Temp-Ordner ab. Von dort kopieren und die ``.txt`` von Hand
korrigieren. Aufrufen über ``python tools/bench.py``.
"""

from __future__ import annotations

import copy
import re
import statistics
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .audio.recorder import Recording
from .config import CleanupConfig, Config, PipelineConfig
from .pipeline.base import Pipeline
from .pipeline.factory import build_pipeline


def normalise(text: str) -> list[str]:
    """Kleinschreibung, ohne Satzzeichen — WER soll Wörter messen, nicht Kommas."""
    return re.sub(r"[^\w\s]", "", text.lower()).split()


def wer(reference: str, hypothesis: str) -> float:
    """Wortfehlerrate: Levenshtein-Abstand auf Wortebene / Wörter der Referenz.

    Bei leerer Referenz (Stille) zählt jedes erkannte Wort als Fehler: 0.0,
    wenn nichts erkannt wurde, sonst 1.0.
    """
    ref, hyp = normalise(reference), normalise(hypothesis)
    if not ref:
        return 1.0 if hyp else 0.0
    previous = list(range(len(hyp) + 1))
    for i, ref_word in enumerate(ref, start=1):
        current = [i]
        for j, hyp_word in enumerate(hyp, start=1):
            current.append(
                min(
                    previous[j] + 1,  # Auslassung
                    current[j - 1] + 1,  # Einfügung
                    previous[j - 1] + (ref_word != hyp_word),  # Ersetzung
                )
            )
        previous = current
    return previous[-1] / len(ref)


@dataclass(frozen=True)
class Case:
    name: str
    recording: Recording
    #: ``None`` = keine Referenz, nur Latenz messen.
    reference: str | None = None


@dataclass(frozen=True)
class CaseResult:
    name: str
    seconds: float
    latency: float
    text: str
    wer: float | None


@dataclass
class VariantResult:
    name: str
    cases: list[CaseResult] = field(default_factory=list)

    @property
    def median_latency(self) -> float:
        return statistics.median(c.latency for c in self.cases) if self.cases else 0.0

    @property
    def mean_wer(self) -> float | None:
        scored = [c.wer for c in self.cases if c.wer is not None]
        return sum(scored) / len(scored) if scored else None


def load_cases(folder: Path) -> list[Case]:
    """Liest alle ``*.wav`` des Ordners samt gleichnamiger ``.txt``."""
    import soundfile as sf

    cases = []
    for wav in sorted(folder.glob("*.wav")):
        samples, rate = sf.read(str(wav), dtype="float32", always_2d=True)
        mono = np.ascontiguousarray(samples.mean(axis=1), dtype=np.float32)
        txt = wav.with_suffix(".txt")
        reference = txt.read_text(encoding="utf-8").strip() if txt.is_file() else None
        cases.append(Case(wav.stem, Recording(samples=mono, sample_rate=int(rate)), reference))
    return cases


def variants(
    cfg: Config, models: Sequence[str] = (), *, compare_plain: bool = False
) -> list[tuple[str, PipelineConfig, CleanupConfig]]:
    """Die zu vergleichenden Konfigurationen.

    Immer dabei: die geladene Konfiguration. Dazu je ein Lauf pro
    ``--model`` und auf Wunsch einer ganz ohne Vokabeln, Bereinigung und
    Stille-Schwelle — der zeigt, was diese Optionen tatsächlich bringen.
    """
    out = [("config", cfg.pipeline, cfg.cleanup)]
    for model in models:
        if model == cfg.pipeline.whisper_model:
            continue
        pipeline = copy.deepcopy(cfg.pipeline)
        pipeline.whisper_model = model
        out.append((model.rsplit("/", 1)[-1], pipeline, cfg.cleanup))
    if compare_plain:
        pipeline = copy.deepcopy(cfg.pipeline)
        pipeline.vocabulary = []
        pipeline.silence_threshold = 0.0
        pipeline.refiner = "none"
        out.append(("plain", pipeline, CleanupConfig()))
    return out


def run_variant(
    name: str,
    cases: Sequence[Case],
    pipeline: Pipeline,
    *,
    repeats: int = 3,
) -> VariantResult:
    """Lässt ``pipeline`` über alle Fälle laufen; Latenz = Median der Läufe."""
    pipeline.injector = None  # messen, nicht einfügen
    pipeline.warmup()
    result = VariantResult(name)
    for case in cases:
        timings, text = [], ""
        for _ in range(max(repeats, 1)):
            started = time.perf_counter()
            text = pipeline.run(case.recording)
            timings.append(time.perf_counter() - started)
        result.cases.append(
            CaseResult(
                name=case.name,
                seconds=case.recording.duration,
                latency=statistics.median(timings),
                text=text,
                wer=None if case.reference is None else wer(case.reference, text),
            )
        )
    return result


def run(
    cfg: Config,
    folder: Path,
    *,
    models: Sequence[str] = (),
    compare_plain: bool = False,
    repeats: int = 3,
) -> list[VariantResult]:
    cases = load_cases(folder)
    if not cases:
        raise FileNotFoundError(f"Keine *.wav-Dateien in {folder}")
    return [
        run_variant(name, cases, build_pipeline(pipeline_cfg, cleanup_cfg), repeats=repeats)
        for name, pipeline_cfg, cleanup_cfg in variants(cfg, models, compare_plain=compare_plain)
    ]


def _percent(value: float | None) -> str:
    return "   –" if value is None else f"{value * 100:3.0f}%"


def format_report(results: Sequence[VariantResult], *, details: bool = False) -> str:
    lines = [f"{'Variante':<28} {'Clips':>5} {'Latenz (Median)':>16} {'WER':>5}"]
    for result in results:
        lines.append(
            f"{result.name:<28} {len(result.cases):>5} "
            f"{result.median_latency * 1000:>13.0f} ms {_percent(result.mean_wer):>5}"
        )
    if details:
        for result in results:
            lines += ["", f"── {result.name}"]
            for case in result.cases:
                lines.append(
                    f"{case.name:<24} {case.seconds:5.1f}s {case.latency * 1000:6.0f} ms "
                    f"{_percent(case.wer)}  {case.text!r}"
                )
    return "\n".join(lines)
