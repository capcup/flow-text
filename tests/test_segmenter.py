from __future__ import annotations

import numpy as np
import pytest

from flowtext.audio.recorder import AudioArray
from flowtext.audio.segmenter import Segmenter

RATE = 16_000


def tone(seconds: float, level: float = 0.1) -> AudioArray:
    """Gleichmäßiges Signal mit dem gewünschten RMS-Pegel."""
    rng = np.random.default_rng(7)
    return (rng.standard_normal(int(RATE * seconds)) * level).astype(np.float32)


def quiet(seconds: float) -> AudioArray:
    return tone(seconds, 0.0005)


def feed_in_blocks(seg: Segmenter, audio: AudioArray, block: float = 0.2) -> list[AudioArray]:
    """Füttert den Segmenter so, wie die App es tut: in kleinen Häppchen."""
    out: list[AudioArray] = []
    step = int(RATE * block)
    for i in range(0, len(audio), step):
        out += seg.feed(audio[i : i + step])
    return out


def make(**kwargs: float) -> Segmenter:
    return Segmenter(
        RATE, level=0.005, pause_seconds=0.7, min_seconds=2.0, max_seconds=20.0, **kwargs
    )


def test_cuts_after_a_long_enough_pause() -> None:
    seg = make()
    done = feed_in_blocks(seg, np.concatenate([tone(3.0), quiet(1.0), tone(1.0)]))
    assert len(done) == 1
    # Der erste Abschnitt endet in der Pause, der Rest bleibt offen.
    assert 3.0 <= len(done[0]) / RATE <= 4.0
    assert 0.9 <= len(seg.flush()) / RATE <= 1.2


def test_no_cut_before_the_pause_is_long_enough() -> None:
    seg = make()
    assert feed_in_blocks(seg, np.concatenate([tone(3.0), quiet(0.4), tone(2.0)])) == []


def test_short_segments_are_held_back_until_min_length() -> None:
    """Ein Ein-Wort-Fetzen vor der ersten Pause wird nicht abgetrennt."""
    seg = make()
    done = feed_in_blocks(seg, np.concatenate([tone(0.8), quiet(1.0), tone(1.5), quiet(1.0)]))
    assert len(done) == 1
    assert len(done[0]) / RATE >= 2.0  # beide Teile gehören zusammen


def test_silence_alone_never_produces_a_segment() -> None:
    seg = make()
    assert feed_in_blocks(seg, quiet(30.0)) == []
    # …und wächst nicht unbegrenzt: nur ein kurzer Vorlauf bleibt stehen.
    assert len(seg.flush()) / RATE <= 1.5


def test_a_single_click_is_not_speech() -> None:
    seg = make()
    audio = quiet(5.0)
    audio[8000:8160] = 0.9  # 10 ms Tastenklick
    assert feed_in_blocks(seg, audio) == []


def test_leading_silence_keeps_a_short_lead_in_for_the_next_word() -> None:
    seg = make()
    feed_in_blocks(seg, quiet(5.0))
    feed_in_blocks(seg, tone(3.0))
    rest = seg.flush()
    # Vorlauf (~0,5 s) + Sprache; der Wortanfang ist nicht abgeschnitten.
    assert 3.3 <= len(rest) / RATE <= 3.7


def test_overlong_speech_is_cut_at_the_quietest_spot() -> None:
    seg = make()
    audio = np.concatenate([tone(17.0), tone(0.3, 0.01), tone(5.0)])  # leise Delle bei 17 s
    done = feed_in_blocks(seg, audio)
    assert len(done) == 1
    assert done[0].shape[0] / RATE == pytest.approx(17.15, abs=0.4)


def test_nothing_is_lost_or_duplicated() -> None:
    seg = make()
    audio = np.concatenate([tone(3.0), quiet(1.0), tone(4.0), quiet(1.2), tone(2.5)])
    done = feed_in_blocks(seg, audio)
    rebuilt = np.concatenate([*done, seg.flush()])
    # Nur reine Stille am Anfang darf gekürzt werden; hier gibt es keine.
    np.testing.assert_array_equal(rebuilt, audio)
    assert len(done) == 2


def test_flush_resets_the_segmenter() -> None:
    seg = make()
    seg.feed(tone(1.0))
    assert len(seg.flush()) == RATE
    assert len(seg.flush()) == 0


def test_empty_feed_is_harmless() -> None:
    seg = make()
    assert seg.feed(np.zeros(0, dtype=np.float32)) == []
    assert len(seg.flush()) == 0
