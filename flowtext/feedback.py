"""Akustische Rückmeldung über den Zustand.

Nutzt ``afplay`` (macOS-Bordmittel) als abgekoppelten Subprozess. Bewusst
"fire and forget": Feedback darf den Controller-Thread niemals bremsen.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path

from .config import FeedbackConfig
from .state import State

log = logging.getLogger(__name__)


class Feedback:
    def __init__(self, cfg: FeedbackConfig) -> None:
        self._cfg = cfg
        self._afplay = shutil.which("afplay") if sys.platform == "darwin" else None
        if cfg.sound and not self._afplay:
            log.debug("afplay nicht gefunden — akustisches Feedback deaktiviert.")

    def _play(self, path: str) -> None:
        if not self._cfg.sound or not self._afplay or not Path(path).is_file():
            return
        try:
            subprocess.Popen(
                [self._afplay, path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            log.debug("Sound konnte nicht abgespielt werden: %s", exc)

    def on_state_change(self, previous: State, current: State) -> None:
        if current is State.RECORDING:
            self._play(self._cfg.start_sound)
        elif current is State.PROCESSING:
            self._play(self._cfg.stop_sound)
        elif current is State.ERROR:
            self._play(self._cfg.error_sound)
        elif current is State.IDLE and previous is State.PROCESSING:
            self._play(self._cfg.done_sound)
