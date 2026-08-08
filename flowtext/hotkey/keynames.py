"""Übersetzung von pynput-Key-Objekten in stabile Namensstrings.

Warum eine eigene Tabelle statt ``key.name``?

* pynput bildet ``Key.alt`` und ``Key.alt_l`` unter macOS auf denselben
  Virtual-Key-Code (0x3A) ab. Enum-Member mit gleichem Wert werden in Python
  zu **Aliassen** — ``Key.alt_l is Key.alt``. Ein naives ``key.name`` liefert
  daher nicht zuverlässig die Seite, die man erwartet.
* ``Option+Space`` erzeugt unter macOS ein geschütztes Leerzeichen (U+00A0),
  ``Ctrl+Space`` ein NUL-Byte. Beides muss auf ``"space"`` normalisiert werden.
* Nicht abbildbare Tasten sollen einen stabilen Fallback bekommen statt
  ``None``, damit die Detektoren konsistente Sets führen.
"""

from __future__ import annotations

from typing import Any

try:  # pragma: no cover — auf Nicht-macOS/CI ggf. nicht installiert
    from pynput import keyboard
except Exception:  # noqa: BLE001
    keyboard = None  # type: ignore[assignment]


#: (pynput-Attributname, kanonischer Name). Reihenfolge zählt: bei
#: Enum-Aliassen (alt/alt_l) gewinnt der erste Eintrag.
_SPECIAL_NAMES: tuple[tuple[str, str], ...] = (
    ("alt_r", "alt_r"),
    ("alt_gr", "alt_r"),
    ("alt_l", "alt_l"),
    ("alt", "alt_l"),
    ("ctrl_r", "ctrl_r"),
    ("ctrl_l", "ctrl_l"),
    ("ctrl", "ctrl_l"),
    ("cmd_r", "cmd_r"),
    ("cmd_l", "cmd_l"),
    ("cmd", "cmd_l"),
    ("shift_r", "shift_r"),
    ("shift_l", "shift_l"),
    ("shift", "shift_l"),
    ("space", "space"),
    ("enter", "enter"),
    ("tab", "tab"),
    ("esc", "esc"),
    ("backspace", "backspace"),
    ("delete", "delete"),
    ("caps_lock", "caps_lock"),
    ("up", "up"),
    ("down", "down"),
    ("left", "left"),
    ("right", "right"),
    ("home", "home"),
    ("end", "end"),
    ("page_up", "page_up"),
    ("page_down", "page_down"),
    *tuple((f"f{i}", f"f{i}") for i in range(1, 21)),
)

#: Zeichen, die je nach Modifier für die Leertaste geliefert werden:
#: normales Leerzeichen, geschütztes Leerzeichen (Option+Space),
#: schmales geschütztes Leerzeichen und NUL (Ctrl+Space).
_SPACE_CHARS = frozenset({" ", "\xa0", "\u202f", "\x00"})


def _build_table() -> dict[Any, str]:
    if keyboard is None:
        return {}
    table: dict[Any, str] = {}
    for attr, name in _SPECIAL_NAMES:
        key = getattr(keyboard.Key, attr, None)
        if key is not None and key not in table:
            table[key] = name
    return table


_TABLE: dict[Any, str] = _build_table()


def normalize(key: Any) -> str | None:
    """pynput-Key → kanonischer Name, oder ``None`` wenn unbrauchbar."""
    name = _TABLE.get(key)
    if name is not None:
        return name

    char = getattr(key, "char", None)
    if char:
        if char in _SPACE_CHARS:
            return "space"
        return char.lower()

    # Key-Enum ohne Tabelleneintrag (z. B. Medientasten).
    raw_name = getattr(key, "name", None)
    if raw_name:
        return str(raw_name).lower()

    vk = getattr(key, "vk", None)
    if vk is not None:
        return f"vk{vk}"
    return None
