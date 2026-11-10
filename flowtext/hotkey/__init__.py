"""Globale Hotkey-Erkennung."""

from .detectors import (
    ComboDetector,
    DoubleTapDetector,
    Signal,
    TriggerDetector,
    build_detector,
)
from .service import TRIGGER, HotkeyService

__all__ = [
    "TRIGGER",
    "ComboDetector",
    "DoubleTapDetector",
    "HotkeyService",
    "Signal",
    "TriggerDetector",
    "build_detector",
]
