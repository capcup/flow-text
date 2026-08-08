"""Zustandsautomat von FlowText.

    IDLE ─(trigger)→ RECORDING ─(trigger|timeout)→ PROCESSING ─→ IDLE
                                                       │
                                                    ERROR ─→ IDLE

Trigger in allen Zuständen außer ``IDLE`` und ``RECORDING`` werden verworfen
(Single-Flight). Das verhindert überlappende Aufnahmen und Pipeline-Läufe.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable
from enum import Enum
from typing import ClassVar


class State(Enum):
    IDLE = "idle"
    RECORDING = "recording"
    PROCESSING = "processing"
    ERROR = "error"

    def __str__(self) -> str:
        return self.value


Observer = Callable[[State, State], None]


class StateMachine:
    """Thread-sicherer Zustandshalter mit Observer-Benachrichtigung.

    Observer werden AUSSERHALB des Locks aufgerufen, damit ein langsamer
    Observer (z. B. Soundausgabe) keinen Zustandswechsel blockiert.
    """

    _ALLOWED: ClassVar[dict[State, frozenset[State]]] = {
        State.IDLE: frozenset({State.RECORDING, State.ERROR}),
        State.RECORDING: frozenset({State.PROCESSING, State.IDLE, State.ERROR}),
        State.PROCESSING: frozenset({State.IDLE, State.ERROR}),
        State.ERROR: frozenset({State.IDLE}),
    }

    def __init__(self) -> None:
        self._state = State.IDLE
        self._lock = threading.Lock()
        self._observers: list[Observer] = []

    @property
    def current(self) -> State:
        with self._lock:
            return self._state

    def subscribe(self, observer: Observer) -> None:
        self._observers.append(observer)

    def transition(self, target: State) -> bool:
        """Wechselt den Zustand. Gibt ``False`` bei unerlaubtem Übergang zurück."""
        with self._lock:
            previous = self._state
            if target is previous:
                return True
            if target not in self._ALLOWED[previous]:
                return False
            self._state = target

        for observer in self._observers:
            # Ein kaputter Observer darf einen Zustandswechsel nicht verhindern.
            with contextlib.suppress(Exception):
                observer(previous, target)
        return True
