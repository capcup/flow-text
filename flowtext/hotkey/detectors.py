"""Trigger-Erkennung — reine Logik, ohne pynput.

Die Detektoren arbeiten ausschließlich auf normalisierten Tastennamen
(``"alt_r"``, ``"space"``, ``"d"`` …) und expliziten Zeitstempeln. Dadurch
sind sie vollständig unit-testbar, ohne macOS oder einen Event-Tap.

Namenskonvention: eingehende Tastennamen sind IMMER seitenspezifisch
(``alt_l`` / ``alt_r``). In der *Konfiguration* darf zusätzlich der
generische Name stehen — ``cmd`` passt dann auf beide Command-Tasten,
``cmd_r`` nur auf die rechte.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

MODIFIERS = frozenset({"ctrl", "alt", "cmd", "shift"})

#: seitenspezifisch → generisch
GENERIC: dict[str, str] = {
    "ctrl_l": "ctrl",
    "ctrl_r": "ctrl",
    "alt_l": "alt",
    "alt_r": "alt",
    "cmd_l": "cmd",
    "cmd_r": "cmd",
    "shift_l": "shift",
    "shift_r": "shift",
}

#: Aliasse, die in der Konfiguration erlaubt sind.
ALIASES: dict[str, str] = {
    "control": "ctrl",
    "option": "alt",
    "opt": "alt",
    "command": "cmd",
    "super": "cmd",
    "meta": "cmd",
    "spacebar": "space",
    "return": "enter",
    "escape": "esc",
    "option_r": "alt_r",
    "option_l": "alt_l",
    "control_r": "ctrl_r",
    "control_l": "ctrl_l",
    "command_r": "cmd_r",
    "command_l": "cmd_l",
}


class HotkeyConfigError(ValueError):
    """Ungültige Hotkey-Definition."""


def canonical(token: str) -> str:
    """Normalisiert ein Konfigurations-Token: ``"<Ctrl>"`` → ``"ctrl"``."""
    clean = token.strip().strip("<>").strip().lower()
    if not clean:
        raise HotkeyConfigError(f"Leeres Hotkey-Token in {token!r}")
    return ALIASES.get(clean, clean)


def parse_combo(combo: str) -> frozenset[str]:
    """``"<ctrl>+<shift>+d"`` → ``frozenset({"ctrl", "shift", "d"})``."""
    tokens = [t for t in combo.split("+") if t.strip()]
    if not tokens:
        raise HotkeyConfigError(f"Leere Hotkey-Kombination: {combo!r}")
    parsed = {canonical(t) for t in tokens}
    if not parsed - MODIFIERS - set(GENERIC):
        raise HotkeyConfigError(
            f"Hotkey {combo!r} besteht nur aus Modifiern — das löst unkontrolliert aus."
        )
    return frozenset(parsed)


def expand(pressed: set[str]) -> set[str]:
    """Ergänzt generische Modifier-Namen zu den seitenspezifischen."""
    return pressed | {GENERIC[name] for name in pressed if name in GENERIC}


class Signal(StrEnum):
    """Ergebnis eines Tastenereignisses.

    ``CONFIRM`` ist der klassische Auslöser (früher ``True``): Start bzw. Stopp
    der Aufnahme. ``START`` und ``CANCEL`` gibt es nur im frühen Startmodus
    des Doppel-Taps: ``START`` = Mikrofon schon beim zweiten Tastendruck
    öffnen, ``CANCEL`` = es wurde doch kein Doppel-Tap, Aufnahme verwerfen.

    ``StrEnum``, damit die Werte unverändert durch ``queue.Queue[str]`` laufen.
    """

    NONE = "none"
    START = "start"
    CONFIRM = "trigger"
    CANCEL = "cancel"


class TriggerDetector(Protocol):
    """Zustandsbehafteter Erkenner. Gibt je Ereignis ein :class:`Signal` zurück."""

    def on_press(self, name: str, now: float) -> Signal: ...

    def on_release(self, name: str, now: float) -> Signal: ...

    def describe(self) -> str: ...


class ComboDetector:
    """Klassische Tastenkombination, z. B. ``Ctrl+Shift+D``.

    Zwei Eigenschaften, die die ursprüngliche Implementierung nicht hatte:

    * **Exakter Modifier-Vergleich** — ``Ctrl+Alt+Shift+Space`` löst NICHT
      aus, wenn ``Ctrl+Alt+Space`` konfiguriert ist.
    * **Entprellung** — Auto-Repeat der Haupttaste feuert nur einmal.
    """

    def __init__(self, combo: str) -> None:
        self._target = parse_combo(combo)
        self._target_mods = self._target & MODIFIERS
        self._combo_text = combo
        self._pressed: set[str] = set()
        self._armed = True

    def describe(self) -> str:
        return f"Combo {self._combo_text}"

    def on_press(self, name: str, now: float) -> Signal:
        self._pressed.add(name)
        expanded = expand(self._pressed)
        if not self._target <= expanded:
            return Signal.NONE
        if (expanded & MODIFIERS) != self._target_mods:
            return Signal.NONE  # zusätzlicher Modifier gedrückt → nicht unsere Kombination
        if not self._armed:
            return Signal.NONE  # Auto-Repeat
        self._armed = False
        return Signal.CONFIRM

    def on_release(self, name: str, now: float) -> Signal:
        self._pressed.discard(name)
        if not self._target <= expand(self._pressed):
            self._armed = True
        return Signal.NONE


class DoubleTapDetector:
    """Doppeltes Antippen einer einzelnen Taste (Standard: Command).

    Ein einzelner Modifier ist nie ein macOS-Systemkurzbefehl — anders als
    ``Ctrl+Option+Space`` ("nächste Eingabequelle"). Andere Apps können den
    Griff trotzdem belegen: Claude für Mac hört auf Doppel-Tap Option.
    Bekannte Kollisionen stehen in ``config.RESERVED_TAP_KEYS``.

    Ein *Tap* ist ein Druck-Loslass-Paar kürzer als ``max_tap_duration``.
    Wird die Taste als Modifier benutzt (also eine andere Taste dazwischen
    gedrückt), verfällt die Sequenz.

    Zwei Betriebsarten:

    * ``early_start=False`` (klassisch): ausgelöst wird beim Loslassen des
      zweiten Taps (``Signal.CONFIRM``).
    * ``early_start=True``: schon der *Druck* des zweiten Taps meldet
      ``Signal.START`` (sofern der erste Tap sauber war und im Zeitfenster
      liegt) — so geht der Anfang des Diktats nicht verloren. Das Loslassen
      bestätigt (``CONFIRM``); wurde die Taste zu lange gehalten oder als
      Modifier benutzt, kommt stattdessen ``CANCEL``.

    ``key`` darf generisch (``cmd``) oder seitenspezifisch (``cmd_r``) sein.
    """

    def __init__(
        self,
        key: str = "cmd",
        window: float = 0.50,
        max_tap_duration: float = 0.45,
        *,
        early_start: bool = False,
    ) -> None:
        self._key = canonical(key)
        if window <= 0 or max_tap_duration <= 0:
            raise HotkeyConfigError("double_tap_window und max_tap_duration müssen > 0 sein")
        self._window = window
        self._max_tap = max_tap_duration
        self._early_start = early_start
        self._press_time: float | None = None
        self._last_tap_end = 0.0
        #: ``True`` zwischen ``START`` und dem Abschluss (``CONFIRM``/``CANCEL``).
        self._armed = False

    def _matches(self, name: str) -> bool:
        """Ein generisch konfigurierter Modifier passt auf beide Seiten."""
        if name == self._key:
            return True
        return bool(self._key in MODIFIERS and GENERIC.get(name) == self._key)

    def describe(self) -> str:
        return f"Double-tap {self._key} (window {self._window:.2f}s)"

    def on_press(self, name: str, now: float) -> Signal:
        if not self._matches(name):
            # Andere Taste → die Zieltaste wird als Modifier verwendet.
            cancelled = self._armed
            self._armed = False
            self._press_time = None
            self._last_tap_end = 0.0
            return Signal.CANCEL if cancelled else Signal.NONE
        if self._press_time is not None:
            return Signal.NONE  # zweite Modifier-Seite o. Ä.: Sequenz läuft bereits
        self._press_time = now
        if self._early_start and self._last_tap_end and (now - self._last_tap_end) <= self._window:
            self._armed = True
            return Signal.START
        return Signal.NONE

    def on_release(self, name: str, now: float) -> Signal:
        if not self._matches(name):
            return Signal.NONE
        press_time, self._press_time = self._press_time, None
        armed, self._armed = self._armed, False
        if press_time is None:
            return Signal.NONE
        if now - press_time > self._max_tap:
            self._last_tap_end = 0.0  # gehalten, kein Tap
            return Signal.CANCEL if armed else Signal.NONE
        if armed:
            # Das Zeitfenster wurde schon beim Druck geprüft.
            self._last_tap_end = 0.0
            return Signal.CONFIRM
        if self._last_tap_end and (now - self._last_tap_end) <= self._window:
            self._last_tap_end = 0.0
            return Signal.CONFIRM
        self._last_tap_end = now
        return Signal.NONE


def build_detector(
    mode: str,
    *,
    combo: str,
    tap_key: str,
    double_tap_window: float,
    max_tap_duration: float,
    early_start: bool = False,
) -> TriggerDetector:
    if mode == "double_tap":
        return DoubleTapDetector(
            tap_key, double_tap_window, max_tap_duration, early_start=early_start
        )
    if mode == "combo":
        return ComboDetector(combo)
    raise HotkeyConfigError(f"Unbekannter hotkey.mode: {mode!r}")
