"""Tests der Trigger-Erkennung — reine Logik, kein macOS nötig."""

from __future__ import annotations

import pytest

from flowtext.hotkey.detectors import (
    ComboDetector,
    DoubleTapDetector,
    HotkeyConfigError,
    build_detector,
    canonical,
    expand,
    parse_combo,
)


class TestParsing:
    def test_parses_angle_brackets(self) -> None:
        assert parse_combo("<ctrl>+<shift>+d") == frozenset({"ctrl", "shift", "d"})

    def test_parses_without_brackets(self) -> None:
        assert parse_combo("ctrl+shift+d") == frozenset({"ctrl", "shift", "d"})

    def test_aliases(self) -> None:
        assert canonical("<Option>") == "alt"
        assert canonical("COMMAND") == "cmd"
        assert canonical("spacebar") == "space"

    def test_modifier_only_combo_rejected(self) -> None:
        with pytest.raises(HotkeyConfigError):
            parse_combo("<ctrl>+<alt>")

    def test_empty_combo_rejected(self) -> None:
        with pytest.raises(HotkeyConfigError):
            parse_combo("")

    def test_expand_adds_generic_modifier(self) -> None:
        assert expand({"alt_r", "space"}) == {"alt_r", "alt", "space"}


class TestComboDetector:
    def _press(self, det: ComboDetector, *names: str) -> list[bool]:
        return [det.on_press(name, 0.0) for name in names]

    def test_fires_on_complete_combo(self) -> None:
        det = ComboDetector("<ctrl>+<shift>+d")
        assert self._press(det, "ctrl_l", "shift_l", "d") == [False, False, True]

    def test_works_with_right_hand_modifiers(self) -> None:
        det = ComboDetector("<ctrl>+<shift>+d")
        assert self._press(det, "ctrl_r", "shift_r", "d") == [False, False, True]

    def test_extra_modifier_does_not_fire(self) -> None:
        """Regression: issubset() ließ jede Obermenge auslösen."""
        det = ComboDetector("<ctrl>+<alt>+<space>")
        assert self._press(det, "ctrl_l", "alt_l", "shift_l", "space") == [False] * 4

    def test_missing_modifier_does_not_fire(self) -> None:
        det = ComboDetector("<ctrl>+<shift>+d")
        assert self._press(det, "ctrl_l", "d") == [False, False]

    def test_autorepeat_fires_once(self) -> None:
        det = ComboDetector("<ctrl>+<shift>+d")
        self._press(det, "ctrl_l", "shift_l")
        assert det.on_press("d", 0.0) is True
        assert det.on_press("d", 0.1) is False
        assert det.on_press("d", 0.2) is False

    def test_rearms_after_release(self) -> None:
        det = ComboDetector("<ctrl>+<shift>+d")
        self._press(det, "ctrl_l", "shift_l")
        assert det.on_press("d", 0.0) is True
        det.on_release("d", 0.1)
        assert det.on_press("d", 0.2) is True

    def test_describe_mentions_combo(self) -> None:
        assert "<ctrl>+<shift>+d" in ComboDetector("<ctrl>+<shift>+d").describe()


class TestDoubleTapDetector:
    def _tap(self, det: DoubleTapDetector, t: float, hold: float = 0.05) -> bool:
        det.on_press("alt_r", t)
        return det.on_release("alt_r", t + hold)

    def test_double_tap_fires(self) -> None:
        det = DoubleTapDetector("alt_r", window=0.35, max_tap_duration=0.30)
        assert self._tap(det, 0.0) is False
        assert self._tap(det, 0.20) is True

    def test_single_tap_does_not_fire(self) -> None:
        det = DoubleTapDetector("alt_r")
        assert self._tap(det, 0.0) is False

    def test_too_slow_does_not_fire(self) -> None:
        det = DoubleTapDetector("alt_r", window=0.35)
        assert self._tap(det, 0.0) is False
        assert self._tap(det, 1.0) is False

    def test_third_tap_needs_new_pair(self) -> None:
        det = DoubleTapDetector("alt_r", window=0.35)
        self._tap(det, 0.0)
        assert self._tap(det, 0.2) is True
        assert self._tap(det, 0.4) is False  # Zähler wurde zurückgesetzt
        assert self._tap(det, 0.6) is True

    def test_held_key_is_not_a_tap(self) -> None:
        det = DoubleTapDetector("alt_r", max_tap_duration=0.30)
        det.on_press("alt_r", 0.0)
        assert det.on_release("alt_r", 1.0) is False
        assert self._tap(det, 1.1) is False

    def test_used_as_modifier_resets(self) -> None:
        """Option+C darf nicht als halber Doppel-Tap zählen."""
        det = DoubleTapDetector("alt_r", window=0.35)
        assert self._tap(det, 0.0) is False
        det.on_press("c", 0.10)  # andere Taste -> Sequenz verfällt
        det.on_release("c", 0.12)
        assert self._tap(det, 0.20) is False

    def test_wrong_key_ignored(self) -> None:
        det = DoubleTapDetector("alt_r")
        assert det.on_press("alt_l", 0.0) is False
        assert det.on_release("alt_l", 0.05) is False

    def test_left_and_right_option_are_distinct(self) -> None:
        det = DoubleTapDetector("alt_r")
        det.on_press("alt_l", 0.0)
        det.on_release("alt_l", 0.05)
        det.on_press("alt_l", 0.10)
        assert det.on_release("alt_l", 0.15) is False

    def test_invalid_window_rejected(self) -> None:
        with pytest.raises(HotkeyConfigError):
            DoubleTapDetector("alt_r", window=0.0)


class TestFactory:
    def test_builds_double_tap(self) -> None:
        det = build_detector(
            "double_tap",
            combo="<ctrl>+d",
            tap_key="alt_r",
            double_tap_window=0.35,
            max_tap_duration=0.3,
        )
        assert isinstance(det, DoubleTapDetector)

    def test_builds_combo(self) -> None:
        det = build_detector(
            "combo",
            combo="<ctrl>+d",
            tap_key="alt_r",
            double_tap_window=0.35,
            max_tap_duration=0.3,
        )
        assert isinstance(det, ComboDetector)

    def test_unknown_mode_rejected(self) -> None:
        with pytest.raises(HotkeyConfigError):
            build_detector(
                "morse",
                combo="<ctrl>+d",
                tap_key="alt_r",
                double_tap_window=0.35,
                max_tap_duration=0.3,
            )
