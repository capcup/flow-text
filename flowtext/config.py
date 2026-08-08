"""Konfiguration: Defaults, TOML-Laden, Pfade.

Bewusst ohne pydantic — ``tomllib`` ist seit Python 3.11 in der Standard-
bibliothek und Dataclasses reichen für diesen Umfang völlig aus.

Suchreihenfolge (erste Treffer gewinnen, Werte werden gemergt):

1. ``./config.local.toml``   (nicht im Git, für persönliche Overrides)
2. ``./config.toml``         (Projekt-Default)
3. ``~/Library/Application Support/FlowText/config.toml``
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover — nur für ältere Interpreter/CI
    import tomli as tomllib  # type: ignore[no-redef]

APP_NAME = "FlowText"


def support_dir() -> Path:
    """Verzeichnis für Konfiguration und temporäre Dateien."""
    return Path.home() / "Library" / "Application Support" / APP_NAME


def log_dir() -> Path:
    return Path.home() / "Library" / "Logs" / APP_NAME


def temp_dir() -> Path:
    return support_dir() / "tmp"


# --------------------------------------------------------------------------- Sektionen


@dataclass
class HotkeyConfig:
    #: ``"double_tap"`` (Standard) oder ``"combo"``
    mode: str = "double_tap"

    #: Nur für ``mode = "double_tap"``. Seitenspezifische Namen wie
    #: ``alt_r``, ``alt_l``, ``cmd_r``, ``ctrl_r``, ``shift_r``.
    tap_key: str = "alt_r"

    #: Maximaler Abstand zwischen zwei Taps (Sekunden).
    double_tap_window: float = 0.35

    #: Länger gehalten als das = kein Tap, sondern ein Modifier-Griff.
    max_tap_duration: float = 0.30

    #: Nur für ``mode = "combo"``.
    #: ACHTUNG: ``<ctrl>+<alt>+<space>`` ist ein macOS-Systemkurzbefehl
    #: ("nächste Eingabequelle") und keine gute Wahl.
    combo: str = "<ctrl>+<shift>+d"

    #: Backoff-Obergrenze für Listener-Neustarts durch den Supervisor.
    max_restarts: int = 5


@dataclass
class AudioConfig:
    sample_rate: int = 16_000
    channels: int = 1
    blocksize: int = 1024

    #: ``None`` = Systemstandard, sonst Gerätename oder Index.
    device: str | int | None = None

    #: Harte Obergrenze; danach wird automatisch gestoppt.
    max_seconds: float = 300.0

    #: Kürzere Aufnahmen gelten als Fehlbedienung und werden verworfen.
    min_seconds: float = 0.3

    #: WAV-Datei nach erfolgreicher Verarbeitung löschen.
    delete_after_use: bool = True


@dataclass
class PipelineConfig:
    #: ``"stub"`` (nur loggen) oder ``"mlx"`` (ab v1.1 mit mlx-whisper).
    transcriber: str = "stub"
    whisper_model: str = "mlx-community/whisper-large-v3-turbo"
    language: str = "de"

    #: ``"none"`` oder ``"ollama"``.
    refiner: str = "none"
    ollama_model: str = "qwen2.5:7b"
    ollama_timeout: float = 20.0
    ollama_prompt: str = (
        "Formatiere den folgenden diktierten Text: korrigiere Interpunktion, "
        "Groß-/Kleinschreibung und offensichtliche Erkennungsfehler. "
        "Ändere NICHT den Inhalt und antworte NUR mit dem korrigierten Text."
    )

    #: ``"none"`` oder ``"clipboard"``.
    injector: str = "none"


@dataclass
class FeedbackConfig:
    sound: bool = True
    start_sound: str = "/System/Library/Sounds/Tink.aiff"
    stop_sound: str = "/System/Library/Sounds/Pop.aiff"
    done_sound: str = "/System/Library/Sounds/Glass.aiff"
    error_sound: str = "/System/Library/Sounds/Basso.aiff"


@dataclass
class LoggingConfig:
    level: str = "INFO"
    to_file: bool = True
    max_bytes: int = 1_000_000
    backup_count: int = 3


@dataclass
class Config:
    hotkey: HotkeyConfig = field(default_factory=HotkeyConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    feedback: FeedbackConfig = field(default_factory=FeedbackConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    #: Woher die Konfiguration stammt (für das Log).
    source: str = "defaults"


# --------------------------------------------------------------------------- Laden


class ConfigError(ValueError):
    """Ungültige Konfiguration."""


def _apply(obj: Any, data: dict[str, Any], path: str) -> None:
    """Setzt bekannte Felder aus ``data`` auf ``obj``; unbekannte werfen."""
    known = {f.name: f for f in fields(obj)}
    for key, value in data.items():
        if key not in known:
            raise ConfigError(f"Unbekannter Konfigurationsschlüssel: {path}.{key}")
        current = getattr(obj, key)
        if is_dataclass(current) and isinstance(value, dict):
            _apply(current, value, f"{path}.{key}")
        else:
            setattr(obj, key, value)


def candidate_paths() -> list[Path]:
    return [
        Path.cwd() / "config.local.toml",
        Path.cwd() / "config.toml",
        support_dir() / "config.toml",
    ]


def load(explicit: Path | None = None) -> Config:
    """Lädt die Konfiguration. Ohne Datei greifen die Defaults."""
    cfg = Config()
    paths = [explicit] if explicit else candidate_paths()

    for path in paths:
        if path is None or not path.is_file():
            continue
        try:
            with path.open("rb") as fh:
                data = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{path} ist kein gültiges TOML: {exc}") from exc
        _apply(cfg, data, "config")
        cfg.source = str(path)
        break

    validate(cfg)
    return cfg


def validate(cfg: Config) -> None:
    """Prüft die Werte, die zur Laufzeit sonst schwer zu debuggende Fehler geben."""
    if cfg.hotkey.mode not in {"double_tap", "combo"}:
        raise ConfigError(
            f"hotkey.mode muss 'double_tap' oder 'combo' sein, nicht {cfg.hotkey.mode!r}"
        )
    if cfg.hotkey.double_tap_window <= 0:
        raise ConfigError("hotkey.double_tap_window muss > 0 sein")
    if cfg.audio.sample_rate not in {8_000, 16_000, 22_050, 44_100, 48_000}:
        raise ConfigError(
            f"audio.sample_rate {cfg.audio.sample_rate} ist ungewöhnlich — Whisper erwartet 16000"
        )
    if cfg.audio.min_seconds >= cfg.audio.max_seconds:
        raise ConfigError("audio.min_seconds muss kleiner als audio.max_seconds sein")
    if cfg.audio.channels != 1:
        raise ConfigError("audio.channels muss 1 sein (Whisper erwartet Mono)")
