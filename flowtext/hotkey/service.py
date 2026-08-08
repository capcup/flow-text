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

#: Kommt so lange gar kein Tastaturereignis an, stimmt etwas Grundsätzliches
#: nicht — dann meldet sich der Watchdog mit einer Anleitung.
_NO_EVENT_WARNING_SECONDS = 20.0

_NO_EVENT_HELP = """\
Seit dem Start ist KEIN einziges Tastaturereignis angekommen.

Das bedeutet: der Event-Tap liefert nichts — unabhängig davon, welcher
Auslöser konfiguriert ist. Der Hotkey kann so gar nicht funktionieren.

Fast immer eine fehlende Berechtigung für die App, aus der FlowText
gestartet wurde (Terminal.app, iTerm2, VS Code, Cursor …):

  Systemeinstellungen → Datenschutz & Sicherheit → Bedienungshilfen
  Systemeinstellungen → Datenschutz & Sicherheit → Eingabeüberwachung

Beide Schalter setzen, dann die App mit Cmd+Q VOLLSTÄNDIG beenden und neu
starten. Zum Einrichten Terminal.app verwenden, nicht das IDE-Terminal.

Zum Gegenprüfen:  python -m flowtext --watch-keys\
"""


class HotkeyService:
    """Erkennt den Hotkey und meldet ihn über eine Queue an den Controller."""

    def __init__(
        self,
        cfg: HotkeyConfig,
        events: queue.Queue[str],
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
        self._slowest_callback = 0.0
        self._dropped = 0
        self._event_count = 0
        self._trigger_count = 0
        self._watchdog: threading.Thread | None = None

    # ------------------------------------------------------------------ API

    def describe(self) -> str:
        return self._detector.describe()

    @property
    def slowest_callback_ms(self) -> float:
        return self._slowest_callback * 1000.0

    @property
    def dropped_triggers(self) -> int:
        return self._dropped

    @property
    def event_count(self) -> int:
        """Wie viele Tastaturereignisse insgesamt angekommen sind."""
        return self._event_count

    @property
    def trigger_count(self) -> int:
        return self._trigger_count

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("HotkeyService läuft bereits")
        self._thread = threading.Thread(
            target=self._supervise, name="hotkey-supervisor", daemon=True
        )
        self._thread.start()
        self._watchdog = threading.Thread(
            target=self._warn_if_no_events, name="hotkey-watchdog", daemon=True
        )
        self._watchdog.start()

    def _warn_if_no_events(self) -> None:
        """Meldet sich, wenn der Event-Tap gar nichts liefert.

        Ohne diese Meldung äußert sich eine fehlende Berechtigung als
        vollkommene Stille — das Programm läuft, reagiert aber nie.
        """
        if self._stop.wait(_NO_EVENT_WARNING_SECONDS):
            return
        if self._event_count == 0:
            for line in _NO_EVENT_HELP.splitlines():
                log.error("%s", line)

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
                listener.join()  # wirft eine gespeicherte Callback-Exception
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
        self._event_count += 1
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
            if log.isEnabledFor(logging.DEBUG):
                log.debug("%s %s%s", "↓" if press else "↑", name, "  ← AUSGELÖST" if fired else "")
            if fired:
                self._trigger_count += 1
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
            if elapsed > self._slowest_callback:
                self._slowest_callback = elapsed
                if elapsed > _SLOW_CALLBACK_SECONDS:
                    log.warning(
                        "Hotkey-Callback brauchte %.1f ms. Bei >1000 ms deaktiviert "
                        "macOS den Event-Tap dauerhaft.",
                        elapsed * 1000.0,
                    )
