from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from flowtext.audio.recorder import Recording
from flowtext.config import CleanupConfig, PipelineConfig
from flowtext.pipeline.base import Pipeline, TranscriptionResult
from flowtext.pipeline.cleanup_rules import RuleBasedCleaner
from flowtext.pipeline.factory import build_pipeline

#: Eine kurze Aufnahme im Speicher — die Fakes werten sie nicht aus.
CLIP = Recording(samples=np.zeros(1600, dtype=np.float32), sample_rate=16_000)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Kommas, die nur zum Füller gehörten, verschwinden mit.
        ("Das ist, äh, gut.", "Das ist gut."),
        ("Das kostet, ähm, 5 Euro", "Das kostet 5 Euro"),
        ("Das ist äh gut", "Das ist gut"),
        # Vor einer Konjunktion bleibt das Komma stehen.
        ("Ich glaube, äh, dass das geht.", "Ich glaube, dass das geht."),
        ("Das ist gut, äh, ähm, aber teuer.", "Das ist gut, aber teuer."),
        # Satzanfang: Folgewort wird großgeschrieben.
        ("Äh, das ist gut.", "Das ist gut."),
        ("Äh, ähm, das passt", "Das passt"),
        ("Äh... das stimmt.", "Das stimmt."),
        ("Gut. Ähm, weiter.", "Gut. Weiter."),
        ("Wirklich? Äh, ja!", "Wirklich? Ja!"),
        # Satzende und Sätze, die nur aus dem Füller bestehen.
        ("Das ist gut, ähm.", "Das ist gut."),
        ("Gut. Ähm. Weiter geht's.", "Gut. Weiter geht's."),
        ("Ähm.", ""),
    ],
)
def test_hesitations_are_removed(raw: str, expected: str) -> None:
    assert RuleBasedCleaner("de").refine(raw) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Das ist eh egal.",  # "eh" ist ein Wort
        "Wir gehen, um zu essen.",  # engl. "um" ist im Deutschen Inhalt
        "Ähnlich gut wie Mähen und Zählen.",  # kein Treffer im Wortinneren
        "Halt an! Das ist halt so.",  # mehrdeutig → bleibt
        "Das ist sehr sehr gut.",  # Verstärkung
        "Der Mann, der der Frau hilft.",  # legitime Dopplung
    ],
)
def test_content_is_left_alone(text: str) -> None:
    assert RuleBasedCleaner("de").refine(text) == text


def test_repeated_function_words_collapse() -> None:
    cleaner = RuleBasedCleaner("de")
    assert cleaner.refine("Ich ich glaube und und hoffe.") == "Ich glaube und hoffe."


def test_extra_fillers_including_phrases() -> None:
    cleaner = RuleBasedCleaner("de", extra_fillers=["sozusagen", "so zu sagen", "  "])
    assert cleaner.refine("Das ist sozusagen fertig, so zu sagen.") == "Das ist fertig."
    assert cleaner.refine("Das ist so gut.") == "Das ist so gut."


def test_language_selects_the_word_list() -> None:
    assert RuleBasedCleaner("en").refine("Um, I I think, uh, that works.") == "I think that works."
    # Unbekannte Sprache: nur die sprachneutralen Laute.
    assert RuleBasedCleaner("fr").refine("Hm, um bon äh.") == "Um bon äh."


def test_disabled_cleaner_is_a_no_op() -> None:
    assert RuleBasedCleaner("de", remove_fillers=False).refine("Äh, ja.") == "Äh, ja."


# ----------------------------------------------------------------- Verdrahtung


class _Transcriber:
    def __init__(self, text: str) -> None:
        self.text = text

    def transcribe(self, recording: Recording) -> TranscriptionResult:
        return TranscriptionResult(text=self.text, language="de")


class _Injector:
    def __init__(self) -> None:
        self.received: list[str] = []

    def inject(self, text: str) -> None:
        self.received.append(text)


class _SpyRefiner:
    def __init__(self) -> None:
        self.seen: list[str] = []

    def refine(self, text: str) -> str:
        self.seen.append(text)
        return text


def test_cleaner_runs_before_refiner(tmp_path: Path) -> None:
    refiner, injector = _SpyRefiner(), _Injector()
    pipe = Pipeline(_Transcriber("Äh, hallo."), refiner, injector, cleaner=RuleBasedCleaner("de"))
    assert pipe.run(CLIP) == "Hallo."
    assert refiner.seen == ["Hallo."]
    assert injector.received == ["Hallo."]


def test_only_fillers_means_no_output(tmp_path: Path) -> None:
    refiner, injector = _SpyRefiner(), _Injector()
    pipe = Pipeline(_Transcriber("Ähm."), refiner, injector, cleaner=RuleBasedCleaner("de"))
    assert pipe.run(CLIP) == ""
    assert refiner.seen == []
    assert injector.received == []


def test_failing_cleaner_falls_back_to_raw_text(tmp_path: Path) -> None:
    class Broken:
        def refine(self, text: str) -> str:
            raise RuntimeError("kaputt")

    injector = _Injector()
    pipe = Pipeline(_Transcriber("Äh, hallo."), None, injector, cleaner=Broken())
    assert pipe.run(CLIP) == "Äh, hallo."
    assert injector.received == ["Äh, hallo."]


def test_factory_builds_cleaner_only_when_enabled() -> None:
    cfg = PipelineConfig(transcriber="stub", injector="none")
    assert build_pipeline(cfg).cleaner is None
    assert build_pipeline(cfg, CleanupConfig()).cleaner is None

    pipe = build_pipeline(cfg, CleanupConfig(remove_fillers=True, extra_fillers=["quasi"]))
    assert isinstance(pipe.cleaner, RuleBasedCleaner)
    assert pipe.cleaner.refine("Das ist, äh, quasi fertig.") == "Das ist fertig."


# ----------------------------------------------------------------- Ersetzungen


def test_replacements_match_whole_words_case_insensitively() -> None:
    cleaner = RuleBasedCleaner(
        "de",
        remove_fillers=False,
        replacements={"flow text": "FlowText", "Flow": "Fluss", "c plus plus": "C++"},
    )
    # Längster Schlüssel gewinnt, Leerraum und Großschreibung der Fundstelle sind egal.
    assert cleaner.refine("Flow  Text und flow.") == "FlowText und Fluss."
    assert cleaner.refine("Overflow bleibt, C plus plus auch.") == "Overflow bleibt, C++ auch."
    # Ohne remove_fillers bleiben Füllwörter stehen.
    assert cleaner.refine("Äh, flow text.") == "Äh, FlowText."


def test_replacements_run_after_filler_removal() -> None:
    cleaner = RuleBasedCleaner("de", replacements={"flow text": "FlowText"})
    assert cleaner.refine("Äh, flow, ähm, text ist gut.") == "FlowText ist gut."


def test_replacement_text_is_taken_literally() -> None:
    cleaner = RuleBasedCleaner("de", remove_fillers=False, replacements={"pfad": r"C:\1\g<0>"})
    assert cleaner.refine("Der Pfad.") == r"Der C:\1\g<0>."


def test_factory_builds_cleaner_for_replacements_alone() -> None:
    cfg = PipelineConfig(transcriber="stub", injector="none")
    pipe = build_pipeline(cfg, CleanupConfig(replacements={"olammer": "Ollama"}))
    assert isinstance(pipe.cleaner, RuleBasedCleaner)
    assert pipe.cleaner.refine("Äh, Olammer läuft.") == "Äh, Ollama läuft."
