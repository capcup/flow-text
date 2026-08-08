"""Tests der Tastennamen-Normalisierung.

Der wichtigste Fall: die **Seite** eines Modifiers muss zuverlässig erkannt
werden. pynput macht das schwer, weil ``Key.cmd_l`` dort ein Enum-*Alias*
von ``Key.cmd`` ist (gleicher Virtual-Key-Code 0x37) — ``key.name`` liefert
dann ``"cmd"``, nicht ``"cmd_l"``. Deshalb normalisiert FlowText unter macOS
über den Virtual-Key-Code aus dem CGEvent.
"""

from __future__ import annotations

import pytest

from flowtext.hotkey import keynames


class FakeKeyCode:
    """Imitiert ``pynput.keyboard.KeyCode``."""

    def __init__(self, vk: int | None = None, char: str | None = None) -> None:
        self.vk = vk
        self.char = char


class FakeKey:
    """Imitiert ein ``pynput.keyboard.Key``-Enum-Member."""

    def __init__(self, vk: int, name: str) -> None:
        self.value = FakeKeyCode(vk)
        self.name = name


@pytest.fixture
def darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(keynames.sys, "platform", "darwin")


@pytest.mark.parametrize(
    ("vk", "pynput_name", "expected"),
    [
        (0x36, "cmd_r", "cmd_r"),
        (0x37, "cmd", "cmd_l"),  # pynput meldet "cmd" — wir wollen die Seite
        (0x3D, "alt_r", "alt_r"),
        (0x3A, "alt", "alt_l"),
        (0x3E, "ctrl_r", "ctrl_r"),
        (0x3B, "ctrl", "ctrl_l"),
        (0x3C, "shift_r", "shift_r"),
        (0x38, "shift", "shift_l"),
        (0x31, "space", "space"),
    ],
)
def test_modifier_side_from_vk(darwin: None, vk: int, pynput_name: str, expected: str) -> None:
    assert keynames.normalize(FakeKey(vk, pynput_name)) == expected


def test_right_command_is_distinct_from_left(darwin: None) -> None:
    """Regression: Doppel-Tap cmd_r darf nicht auf cmd_l hereinfallen."""
    assert keynames.normalize(FakeKey(0x36, "cmd_r")) != keynames.normalize(FakeKey(0x37, "cmd"))


def test_vk_from_plain_keycode(darwin: None) -> None:
    assert keynames.normalize(FakeKeyCode(vk=0x36)) == "cmd_r"


def test_character_keys(darwin: None) -> None:
    assert keynames.normalize(FakeKeyCode(vk=0x02, char="D")) == "d"


@pytest.mark.parametrize("char", [" ", "\xa0", " ", "\x00"])
def test_space_variants(darwin: None, char: str) -> None:
    """Option+Space liefert U+00A0, Ctrl+Space ein NUL-Byte."""
    assert keynames.normalize(FakeKeyCode(vk=0x99, char=char)) == "space"


def test_unknown_key_gets_stable_fallback(darwin: None) -> None:
    assert keynames.normalize(FakeKeyCode(vk=0x99)) == "vk153"


def test_unusable_key_returns_none(darwin: None) -> None:
    assert keynames.normalize(object()) is None
