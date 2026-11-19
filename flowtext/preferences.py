"""Einstellungen, die das Settings-Fenster ändert — getrennt von ``config.toml``.

Das Fenster kann ``config.toml`` nicht zurückschreiben, ohne die Kommentare
des Nutzers zu verlieren (``tomllib`` liest nur). Geänderte Werte landen
deshalb in ``~/Library/Application Support/FlowText/preferences.json`` und
überschreiben beim Laden die Werte aus ``config.toml``.

Nur Schalter, die das Fenster anbietet, dürfen hier stehen, und es wird nur
gespeichert, was der Nutzer tatsächlich geändert hat. Alles andere
(Hotkey, Audio, Modelle …) bleibt in ``config.toml``.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Config, support_dir

log = logging.getLogger(__name__)


def preferences_path() -> Path:
    return support_dir() / "preferences.json"


@dataclass(frozen=True)
class Option:
    """Ein im Fenster änderbarer Wert."""

    section: str
    key: str
    #: ``bool`` oder ``list`` (Liste von Texten)
    kind: type
    label: str
    hint: str


#: Reihenfolge = Reihenfolge im Fenster.
OPTIONS: tuple[Option, ...] = (
    Option(
        "streaming",
        "enabled",
        bool,
        "Live insertion",
        "Text appears after each pause while you speak. Inserted text can't be corrected later.",
    ),
    Option(
        "cleanup",
        "remove_fillers",
        bool,
        "Remove filler words",
        'Strips "äh", "ähm", "hm" and stutters like "ich ich".',
    ),
    Option(
        "feedback",
        "sound",
        bool,
        "Play sounds",
        "Short sounds when recording starts, stops and finishes.",
    ),
    Option(
        "overlay",
        "enabled",
        bool,
        "Show recording indicator",
        "A small bar at the screen bottom shows that FlowText is listening and how loud you are.",
    ),
    Option(
        "pipeline",
        "vocabulary",
        list,
        "Vocabulary",
        "Names and terms Whisper should spell correctly, separated by commas.",
    ),
)

_BY_NAME = {(o.section, o.key): o for o in OPTIONS}


def find(section: str, key: str) -> Option:
    try:
        return _BY_NAME[(section, key)]
    except KeyError:
        raise KeyError(f"{section}.{key} ist keine änderbare Einstellung") from None


def get(cfg: Config, option: Option) -> Any:
    return getattr(getattr(cfg, option.section), option.key)


def set_value(cfg: Config, option: Option, value: Any) -> None:
    setattr(getattr(cfg, option.section), option.key, value)


def check(option: Option, value: Any) -> Any:
    """Prüft und normalisiert einen Wert; wirft ``ValueError`` bei falschem Typ."""
    if option.kind is bool:
        if not isinstance(value, bool):
            raise ValueError(f"{option.section}.{option.key} muss true oder false sein")
        return value
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"{option.section}.{option.key} muss eine Liste von Texten sein")
    return parse_terms(", ".join(value))


def parse_terms(text: str) -> list[str]:
    """``"A, B\\nC"`` → ``["A", "B", "C"]``; ohne Leere und Doppelte."""
    seen: dict[str, None] = {}
    for part in text.replace("\n", ",").split(","):
        term = part.strip()
        if term:
            seen.setdefault(term)
    return list(seen)


class Preferences:
    """Liest und schreibt ``preferences.json``."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or preferences_path()

    def load(self) -> dict[tuple[str, str], Any]:
        """Alle gültigen gespeicherten Werte. Kaputtes wird übersprungen."""
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            log.warning("preferences.json nicht lesbar (%s) — wird ignoriert.", exc)
            return {}
        if not isinstance(raw, dict):
            return {}

        found: dict[tuple[str, str], Any] = {}
        for section, values in raw.items():
            if not isinstance(values, dict):
                continue
            for key, value in values.items():
                option = _BY_NAME.get((section, key))
                if option is None:
                    continue
                try:
                    found[(section, key)] = check(option, value)
                except ValueError as exc:
                    log.warning("Gespeicherte Einstellung übersprungen: %s", exc)
        return found

    def apply(self, cfg: Config) -> bool:
        """Überschreibt Werte in ``cfg``. ``True``, wenn etwas gesetzt wurde."""
        values = self.load()
        for (section, key), value in values.items():
            set_value(cfg, _BY_NAME[(section, key)], value)
        return bool(values)

    def save(self, option: Option, value: Any) -> None:
        """Merkt sich einen geänderten Wert (atomar, damit nichts halb geschrieben wird)."""
        stored = self.load()
        stored[(option.section, option.key)] = check(option, value)
        nested: dict[str, dict[str, Any]] = {}
        for (section, key), item in stored.items():
            nested.setdefault(section, {})[key] = item

        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".preferences-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(nested, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
