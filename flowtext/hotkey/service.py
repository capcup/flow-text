"""Hotkey-Service: Event-Tap-Listener + Supervisor.

Zwei Eigenschaften sind hier sicherheitskritisch:

**1. Der Callback muss in unter 1 ms zurückkehren.**
Er läuft im CFRunLoop des ``CGEventTap``. Braucht er zu lange, deaktiviert
macOS den Tap (``kCGEventTapDisabledByTimeout``) — und pynput reaktiviert ihn
nie (im gesamten ``pynput/_util/darwin.py`` gibt es genau einen
``CGEventTapEnable``-Aufruf, beim Start). Deshalb wird hier ausschließlich
Set-Logik ausgeführt und ein Sentinel in eine Queue gelegt.

**2. Der Listener muss überwacht werden.**
pynput fängt jede Exception aus einem Callback ab, ruft ``self.stop()`` und
wirft sie erst beim Aufruf von ``join()`` erneut. Ohne einen Thread, der
``join()`` aufruft, stirbt der Listener vollkommen lautlos.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
import time
from typing import Any

from ..config import HotkeyConfig
from .detectors import TriggerDetector, build_detector
from .keynames import normalize

log = logging.getLogger(__name__)

#: Sentinel, der bei Auslösung in die Event-Queue gelegt wird.
TRIGGER = "trigger"

#: Ab dieser Callback-Dauer wird gewarnt (Tap-Timeout-Frühwarnung).
_SLOW_CALLBACK_SECONDS = 0.005


class HotkeyService:
    """Erkennt den Hotkey und meldet ihn über eine Queue an den Controller."""

    def __init__(
        self,
        cfg: HotkeyConfig,
        events: queue.Queue[str],
        *,
        idle_restart_seconds: float = 0.0,
    ) -> None:
        self._cfg = cfg
        self._events = events
        self._detector: TriggerDetector = build_detector(
            cfg.mode,
            combo=cfg.combo,
            tap_key=cfg.tap_key,
            double_tap_window=cfg.double_tap_window,
            max_tap_duration=cfg.max_tap_duration,
        )
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._listener: Any = None
        self._thread: threading.Thread | None = None
        self._idle_restart = idle_restart_seconds
        self._last_event = time.monotonic()
        self._slowest_callback = 0.0
        self._dropped = 0

    # ------------------------------------------------------------------ API

    def describe(self) -> str:
        return self._detector.describe()

    @property
    def slowest_callback_ms(self) -> float:
        return self._slowest_callback * 1000.0

    @property
    def dropped_triggers(self) -> int:
        return self._dropped

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("HotkeyService läuft bereits")
        self._thread = threading.Thread(
            target=self._supervise, name="hotkey-supervisor", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        listener = self._listener
        if listener is not None:
            with contextlib.suppress(Exception):
                listener.stop()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    # -------------------------------------------------------------- intern

    def _make_listener(self) -> Any:
        from pynput import keyboard  # lazy: erst hier wird pyobjc geladen

        return keyboard.Listener(on_press=self._on_press, on_release=self._on_release)

    def _supervise(self) -> None:
        """Startet den Listener neu, wenn er stirbt (siehe Modul-Docstring)."""
        restarts = 0
        while not self._stop.is_set():
            try:
                listener = self._make_listener()
            except Exception:
                log.exception("Hotkey-Listener konnte nicht erzeugt werden.")
                return

            self._listener = listener
            self._reset_detector()
            listener.start()
            with contextlib.suppress(Exception):
                listener.wait()
            log.info("Hotkey aktiv: %s", self._detector.describe())

            try:
                self._join_with_idle_check(listener)
                if self._stop.is_set():
                    return
                log.warning("Hotkey-Listener wurde beendet — unerwartet.")
            except Exception:
                log.exception("Hotkey-Listener durch eine Exception gestorben.")

            if self._stop.is_set():
                return

            restarts += 1
            if restarts > self._cfg.max_restarts:
                log.error(
                    "Hotkey-Listener konnte %d× nicht stabilisiert werden — FlowText gibt auf. "
                    "Bitte Berechtigungen prüfen und neu starten.",
                    restarts,
                )
                return
            backoff = min(2.0**restarts, 30.0)
            log.info("Neustart des Hotkey-Listeners in %.0fs (Versuch %d).", backoff, restarts)
            self._stop.wait(backoff)

    def _join_with_idle_check(self, listener: Any) -> None:
        """``listener.join()`` — optional mit proaktivem Neustart bei Funkstille."""
        if self._idle_restart <= 0:
            listener.join()
            return
        while not self._stop.is_set():
            listener.join(timeout=5.0)
            if not listener.running:
                listener.join()  # wirft eine gespeicherte Callback-Exception
                return
            if time.monotonic() - self._last_event > self._idle_restart:
                log.info(
                    "Seit %.0fs kein Tastaturereignis — Listener wird vorsorglich neu gestartet.",
                    self._idle_restart,
                )
                listener.stop()
                listener.join()
                return

    def _reset_detector(self) -> None:
        with self._lock:
            self._detector = build_detector(
                self._cfg.mode,
                combo=self._cfg.combo,
                tap_key=self._cfg.tap_key,
                double_tap_window=self._cfg.double_tap_window,
                max_tap_duration=self._cfg.max_tap_duration,
            )

    # ------------------------------------------------------- Event-Callbacks

    def _on_press(self, key: Any) -> None:
        self._dispatch(key, press=True)

    def _on_release(self, key: Any) -> None:
        self._dispatch(key, press=False)

    def _dispatch(self, key: Any, *, press: bool) -> None:
        """Läuft im Event-Tap-Thread. Muss extrem schnell zurückkehren."""
        started = time.perf_counter()
        try:
            name = normalize(key)
            if name is None:
                return
            now = started
            with self._lock:
                fired = (
                    self._detector.on_press(name, now)
                    if press
                    else self._detector.on_release(name, now)
                )
            if fired:
                try:
                    self._events.put_nowait(TRIGGER)
                except queue.Full:
                    self._dropped += 1
        except Exception:
            # Eine Exception hier würde den Listener töten (pynput ruft stop()).
            # Der Supervisor fängt das zwar auf, aber wir vermeiden es lieber.
            log.exception("Fehler im Hotkey-Callback — Ereignis wird ignoriert.")
        finally:
            elapsed = time.perf_counter() - started
            self._last_event = time.monotonic()
            if elapsed > self._slowest_callback:
                self._slowest_callback = elapsed
                if elapsed > _SLOW_CALLBACK_SECONDS:
                    log.warning(
                        "Hotkey-Callback brauchte %.1f ms. Bei >1000 ms deaktiviert "
                        "macOS den Event-Tap dauerhaft.",
                        elapsed * 1000.0,
                    )
