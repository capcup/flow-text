from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from flowtext import bench
from flowtext.audio.recorder import Recording
from flowtext.config import Config
from flowtext.pipeline.base import Pipeline, TranscriptionResult

RATE = 16_000


class TestWer:
    @pytest.mark.parametrize(
        ("reference", "hypothesis", "expected"),
        [
            ("das ist ein test", "das ist ein test", 0.0),
            ("Das ist, ein Test!", "das ist ein test", 0.0),  # Satzzeichen/Großschreibung egal
            ("das ist ein test", "das ist kein test", 0.25),  # Ersetzung
            ("das ist ein test", "das ist test", 0.25),  # Auslassung
            ("das ist ein test", "das ist ein ein test", 0.25),  # Einfügung
            ("das ist ein test", "", 1.0),
            ("größe über öl", "größe über öl", 0.0),  # Umlaute bleiben Wörter
        ],
    )
    def test_word_level_distance(self, reference: str, hypothesis: str, expected: float) -> None:
        assert bench.wer(reference, hypothesis) == pytest.approx(expected)

    def test_empty_reference_means_nothing_should_be_recognised(self) -> None:
        assert bench.wer("", "") == 0.0
        assert bench.wer("", "Vielen Dank.") == 1.0


def _write_wav(path: Path, seconds: float = 0.5, channels: int = 1, rate: int = RATE) -> None:
    import soundfile as sf

    samples = np.full((int(rate * seconds), channels), 0.25, dtype=np.float32)
    sf.write(str(path), samples, rate, subtype="PCM_16")


def test_load_cases_pairs_wav_with_txt(tmp_path: Path) -> None:
    _write_wav(tmp_path / "b.wav")
    _write_wav(tmp_path / "a.wav", seconds=1.0, channels=2, rate=48_000)
    (tmp_path / "a.txt").write_text("Hallo Welt\n", encoding="utf-8")
    (tmp_path / "stille.txt").write_text("", encoding="utf-8")  # ohne WAV: ignoriert

    cases = bench.load_cases(tmp_path)
    assert [c.name for c in cases] == ["a", "b"]
    assert cases[0].reference == "Hallo Welt"
    assert cases[1].reference is None
    # Stereo wird zu Mono, die Abtastrate bleibt für den Transcriber erhalten.
    assert cases[0].recording.samples.ndim == 1
    assert cases[0].recording.sample_rate == 48_000
    assert cases[0].recording.duration == pytest.approx(1.0)


class _Transcriber:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    def transcribe(self, recording: Recording) -> TranscriptionResult:
        self.calls += 1
        return TranscriptionResult(text=self.text)


class _Injector:
    def inject(self, text: str) -> None:
        raise AssertionError("Der Benchmark darf nie etwas einfügen")


def test_run_variant_measures_without_injecting() -> None:
    clip = Recording(samples=np.full(RATE, 0.25, dtype=np.float32), sample_rate=RATE)
    cases = [
        bench.Case("mit", clip, "das ist ein test"),
        bench.Case("ohne", clip, None),
    ]
    transcriber = _Transcriber("Das ist kein Test.")
    result = bench.run_variant("x", cases, Pipeline(transcriber, None, _Injector()), repeats=2)

    assert transcriber.calls == 4
    assert [c.wer for c in result.cases] == [pytest.approx(0.25), None]
    assert result.mean_wer == pytest.approx(0.25)
    assert result.median_latency >= 0.0
    assert result.cases[0].seconds == pytest.approx(1.0)


def test_variants_cover_models_and_plain() -> None:
    cfg = Config()
    cfg.pipeline.vocabulary = ["FlowText"]
    cfg.cleanup.remove_fillers = True

    out = bench.variants(cfg, ["org/other-model", cfg.pipeline.whisper_model], compare_plain=True)
    assert [name for name, _, _ in out] == ["config", "other-model", "plain"]
    assert out[1][1].whisper_model == "org/other-model"
    assert out[1][1].vocabulary == ["FlowText"]
    plain_pipeline, plain_cleanup = out[2][1], out[2][2]
    assert plain_pipeline.vocabulary == []
    assert plain_pipeline.silence_threshold == 0.0
    assert plain_cleanup.remove_fillers is False
    # Die geladene Konfiguration bleibt unangetastet.
    assert cfg.pipeline.vocabulary == ["FlowText"]


def test_run_fails_clearly_on_an_empty_folder(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Keine"):
        bench.run(Config(), tmp_path)


def test_report_lists_every_variant() -> None:
    result = bench.VariantResult("config", [bench.CaseResult("a", 2.0, 0.95, "hallo", 0.1)])
    empty = bench.VariantResult("plain", [bench.CaseResult("a", 2.0, 0.5, "x", None)])
    report = bench.format_report([result, empty], details=True)
    assert "config" in report and "950 ms" in report and "10%" in report
    assert "–" in report  # ohne Referenz keine WER
    assert "'hallo'" in report
