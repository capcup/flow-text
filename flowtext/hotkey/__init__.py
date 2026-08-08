"""Globale Hotkey-Erkennung."""

from .detectors import ComboDetector, DoubleTapDetector, TriggerDetector, build_detector
from .service import TRIGGER, HotkeyService

__all__ = [
    "TRIGGER",
    "ComboDetector",
    "DoubleTapDetector",
    "HotkeyService",
    "TriggerDetector",
    "build_detector",
]
