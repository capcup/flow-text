"""Tests der Trigger-Erkennung — reine Logik, kein macOS nötig."""

from __future__ import annotations

import pytest

from flowtext.hotkey.detectors import (
    ComboDetector,
    DoubleTapDetector,
    HotkeyConfigError,
    Signal,
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
        return [det.on_press(name, 0.0) is Signal.CONFIRM for name in names]

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
        assert det.on_press("d", 0.0) is Signal.CONFIRM
        assert det.on_press("d", 0.1) is Signal.NONE
        assert det.on_press("d", 0.2) is Signal.NONE

    def test_rearms_after_release(self) -> None:
        det = ComboDetector("<ctrl>+<shift>+d")
        self._press(det, "ctrl_l", "shift_l")
        assert det.on_press("d", 0.0) is Signal.CONFIRM
        det.on_release("d", 0.1)
        assert det.on_press("d", 0.2) is Signal.CONFIRM

    def test_describe_mentions_combo(self) -> None:
        assert "<ctrl>+<shift>+d" in ComboDetector("<ctrl>+<shift>+d").describe()


class TestDoubleTapDetector:
    def _tap(self, det: DoubleTapDetector, t: float, hold: float = 0.05) -> bool:
        """Ein Tap; ``True``, wenn das Loslassen auslöst (``CONFIRM``)."""
        det.on_press("alt_r", t)
        return det.on_release("alt_r", t + hold) is Signal.CONFIRM

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
        assert det.on_release("alt_r", 1.0) is Signal.NONE
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
        assert det.on_press("alt_l", 0.0) is Signal.NONE
        assert det.on_release("alt_l", 0.05) is Signal.NONE

    def test_left_and_right_option_are_distinct(self) -> None:
        det = DoubleTapDetector("alt_r")
        det.on_press("alt_l", 0.0)
        det.on_release("alt_l", 0.05)
        det.on_press("alt_l", 0.10)
        assert det.on_release("alt_l", 0.15) is Signal.NONE

    def test_generic_cmd_matches_both_left_and_right(self) -> None:
        det = DoubleTapDetector("cmd")
        # Tap cmd_l
        det.on_press("cmd_l", 0.0)
        det.on_release("cmd_l", 0.05)
        # Tap cmd_r
        det.on_press("cmd_r", 0.15)
        assert det.on_release("cmd_r", 0.20) is Signal.CONFIRM

    def test_invalid_window_rejected(self) -> None:
        with pytest.raises(HotkeyConfigError):
            DoubleTapDetector("alt_r", window=0.0)


class TestDoubleTapEarlyStart:
    """Früher Start: Mikrofon schon beim *Druck* des zweiten Taps."""

    @staticmethod
    def _det() -> DoubleTapDetector:
        return DoubleTapDetector("cmd_r", window=0.5, max_tap_duration=0.45, early_start=True)

    @staticmethod
    def _first_tap(det: DoubleTapDetector, t: float = 0.0) -> None:
        assert det.on_press("cmd_r", t) is Signal.NONE
        assert det.on_release("cmd_r", t + 0.08) is Signal.NONE

    def test_second_press_starts_and_release_confirms(self) -> None:
        det = self._det()
        self._first_tap(det)
        assert det.on_press("cmd_r", 0.25) is Signal.START
        assert det.on_release("cmd_r", 0.33) is Signal.CONFIRM

    def test_first_press_never_starts(self) -> None:
        assert self._det().on_press("cmd_r", 0.0) is Signal.NONE

    def test_second_press_too_late_does_not_start(self) -> None:
        det = self._det()
        self._first_tap(det)
        assert det.on_press("cmd_r", 1.5) is Signal.NONE
        assert det.on_release("cmd_r", 1.55) is Signal.NONE

    def test_held_second_press_cancels(self) -> None:
        """Cmd lange gehalten (Modifier-Griff) → vorläufiger Start wird verworfen."""
        det = self._det()
        self._first_tap(det)
        assert det.on_press("cmd_r", 0.25) is Signal.START
        assert det.on_release("cmd_r", 1.00) is Signal.CANCEL

    def test_other_key_after_start_cancels(self) -> None:
        """Tap, dann Cmd+V: das ist kein Doppel-Tap."""
        det = self._det()
        self._first_tap(det)
        assert det.on_press("cmd_r", 0.25) is Signal.START
        assert det.on_press("v", 0.28) is Signal.CANCEL
        assert det.on_release("v", 0.30) is Signal.NONE
        assert det.on_release("cmd_r", 0.32) is Signal.NONE

    def test_other_key_without_start_is_silent(self) -> None:
        det = self._det()
        self._first_tap(det)
        assert det.on_press("v", 0.10) is Signal.NONE

    def test_single_tap_emits_nothing(self) -> None:
        det = self._det()
        self._first_tap(det)

    def test_release_confirms_even_if_release_gap_exceeds_window(self) -> None:
        """Das Zeitfenster wurde schon beim Druck geprüft."""
        det = self._det()
        self._first_tap(det)  # Ende bei 0.08
        assert det.on_press("cmd_r", 0.50) is Signal.START  # Abstand 0.42 s
        assert det.on_release("cmd_r", 0.62) is Signal.CONFIRM  # Abstand 0.54 s > Fenster

    def test_rearms_after_confirm(self) -> None:
        det = self._det()
        self._first_tap(det)
        det.on_press("cmd_r", 0.25)
        det.on_release("cmd_r", 0.33)
        # Der nächste Doppel-Tap beginnt wieder bei Null.
        assert det.on_press("cmd_r", 1.0) is Signal.NONE
        det.on_release("cmd_r", 1.05)
        assert det.on_press("cmd_r", 1.2) is Signal.START

    def test_generic_cmd_second_side_press_does_not_restart(self) -> None:
        det = DoubleTapDetector("cmd", early_start=True)
        det.on_press("cmd_l", 0.0)
        det.on_release("cmd_l", 0.05)
        assert det.on_press("cmd_r", 0.2) is Signal.START
        assert det.on_press("cmd_l", 0.21) is Signal.NONE  # läuft bereits

    def test_disabled_by_default(self) -> None:
        det = DoubleTapDetector("cmd_r")
        det.on_press("cmd_r", 0.0)
        det.on_release("cmd_r", 0.05)
        assert det.on_press("cmd_r", 0.2) is Signal.NONE
        assert det.on_release("cmd_r", 0.25) is Signal.CONFIRM


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

    def test_passes_early_start_to_double_tap(self) -> None:
        det = build_detector(
            "double_tap",
            combo="<ctrl>+d",
            tap_key="alt_r",
            double_tap_window=0.35,
            max_tap_duration=0.3,
            early_start=True,
        )
        det.on_press("alt_r", 0.0)
        det.on_release("alt_r", 0.05)
        assert det.on_press("alt_r", 0.2) is Signal.START

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
