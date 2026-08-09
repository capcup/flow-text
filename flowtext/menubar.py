"""Menüleisten-Oberfläche (rumps).

Schließt die auffälligste Lücke der CLI-Variante: man hört zwar Töne, sieht
aber nie, ob gerade aufgenommen wird oder die Transkription noch läuft.

**Zwei Regeln bestimmen den Aufbau:**

1. *Cocoa gehört der Hauptthread — und zwar zuerst.* ``rumps`` blockiert ihn,
   deshalb startet :class:`~flowtext.app.FlowTextApp` hier über ``start()``
   statt ``run()``. Entscheidend ist die Reihenfolge: ``start()`` läuft erst
   im ersten Timer-Tick, also **innerhalb** der bereits laufenden Runloop.
   Vorher gestartet, legen pynput, PortAudio und MLX ihre eigenen
   CoreFoundation-Runloops an, ``rumps.run()`` kehrt sofort zurück und es
   erscheint nie ein Icon.
2. *Kein Cocoa-Zugriff aus fremden Threads.* Der Zustandswechsel kommt aus
   dem Controller-Thread; er wird nur in ein Attribut geschrieben. Ein
   rumps-Timer läuft im Hauptthread und überträgt ihn in die Oberfläche.

Der Zustandsautomat war darauf schon vorbereitet — die Anzeige ist einfach
ein weiterer Observer neben dem Ton.
"""

from __future__ import annotations

import importlib.util
import logging
import subprocess
import sys
import time
from typing import Any

from . import permissions
from .app import FlowTextApp
from .config import Config, candidate_paths, log_dir
from .state import State

log = logging.getLogger(__name__)

#: Zeichen in der Menüleiste. Bewusst Text statt Bilddateien — dadurch
#: braucht die Stufe 1 keine Assets und funktioniert in Hell und Dunkel.
ICONS: dict[State, str] = {
    State.IDLE: "🎙",
    State.RECORDING: "🔴",
    State.PROCESSING: "⏳",
    State.ERROR: "⚠️",
}
PAUSED_ICON = "⏸"

#: Fehlende Berechtigungen haben Vorrang vor allem anderen — ohne sie kann
#: der Auslöser gar nicht funktionieren.
LOCKED_ICON = "🔒"

LABELS: dict[State, str] = {
    State.IDLE: "Ready",
    State.RECORDING: "Recording…",
    State.PROCESSING: "Processing…",
    State.ERROR: "Error — see log",
}

#: Direktlinks in die Systemeinstellungen.
SETTINGS_URLS = {
    "Bedienungshilfen": (
        "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
    ),
    "Eingabeüberwachung": (
        "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"
    ),
}

#: Solange Rechte fehlen, wird alle N Ticks nachgesehen. 200 ms × 10 = 2 s.
PERMISSION_RECHECK_TICKS = 10

#: Wie oft die Oberfläche den Zustand abholt. 0,2 s ist unauffällig und
#: kostet praktisch nichts.
TICK_SECONDS = 0.2

#: Kehrt ``rumps.run()`` schneller zurück, hat die Cocoa-Runloop nie
#: gelaufen. Das ist kein normales Beenden, sondern ein Fehler — und einer,
#: der sich sonst nur als „kein Icon" äußert.
MIN_RUNLOOP_SECONDS = 1.0


def available() -> bool:
    """Ist ``rumps`` installiert?"""
    if "rumps" in sys.modules:
        return True
    try:
        return importlib.util.find_spec("rumps") is not None
    except (ImportError, ValueError):
        return False


def icon_for(state: State, paused: bool, permissions_ok: bool = True) -> str:
    if not permissions_ok:
        return LOCKED_ICON
    return PAUSED_ICON if paused else ICONS.get(state, ICONS[State.IDLE])


def label_for(state: State, paused: bool, permissions_ok: bool = True) -> str:
    if not permissions_ok:
        return "Permissions missing"
    if paused:
        return "Paused"
    return LABELS.get(state, str(state))


def _open(path: str) -> None:
    """Datei in der Standardanwendung öffnen."""
    try:
        subprocess.Popen(["/usr/bin/open", path])
    except OSError as exc:
        log.warning("Could not open %s: %s", path, exc)


class MenubarApp:
    """Verbindet :class:`FlowTextApp` mit einem rumps-Statusicon."""

    def __init__(self, cfg: Config, app: FlowTextApp) -> None:
        self.cfg = cfg
        self.app = app
        self._state = State.IDLE
        self._ui: Any = None
        self._items: dict[str, Any] = {}
        self._permissions_ok = permissions.check().ok
        self._ticks = 0
        self._started = False
        # Läuft im Controller-Thread: NUR schreiben, kein Cocoa-Zugriff.
        app.state.subscribe(self._remember_state)

    # ------------------------------------------------------------------ API

    def run(self) -> None:
        """Blockiert den Hauptthread, bis der Nutzer beendet."""
        import rumps

        self._ui = rumps.App(
            "FlowText",
            title=icon_for(self._state, self.app.paused, self._permissions_ok),
            quit_button=None,
        )
        self._build_menu(rumps)

        # FlowText wird hier bewusst NICHT gestartet, sondern erst im ersten
        # Timer-Tick. ``app.start()`` zieht pynput, PortAudio und MLX hoch;
        # jedes davon legt eigene CoreFoundation-Runloops an. Geschieht das,
        # bevor NSApplication den Hauptthread übernommen hat, kehrt
        # ``rumps.run()`` sofort zurück — kein Statusicon, und die App
        # beendet sich nach wenigen Sekunden scheinbar grundlos wieder.
        # Der erste Tick läuft bereits innerhalb der Cocoa-Runloop.
        rumps.Timer(self._tick, TICK_SECONDS).start()
        began = time.monotonic()
        try:
            self._ui.run()
        finally:
            elapsed = time.monotonic() - began
            if elapsed < MIN_RUNLOOP_SECONDS:
                log.error(
                    "rumps.run() returned after %.2fs — Cocoa runloop never ran.",
                    elapsed,
                )
            self.app.shutdown()

    # ------------------------------------------------------------- Aufbau

    def _build_menu(self, rumps: Any) -> None:
        status = rumps.MenuItem(label_for(self._state, self.app.paused, self._permissions_ok))
        status.set_callback(None)  # reine Anzeige
        trigger = rumps.MenuItem(f"Trigger: {self.app.hotkey.describe()}")
        trigger.set_callback(None)

        self._items = {
            "status": status,
            "pause": rumps.MenuItem("Pause", callback=self._toggle_pause),
        }
        self._ui.menu = [
            status,
            trigger,
            None,
            self._items["pause"],
            None,
            rumps.MenuItem("Grant Accessibility…", callback=self._open_accessibility),
            rumps.MenuItem("Grant Input Monitoring…", callback=self._open_input_monitoring),
            None,
            rumps.MenuItem("Open Configuration…", callback=self._open_config),
            rumps.MenuItem("Open Log File…", callback=self._open_log),
            None,
            rumps.MenuItem("Quit FlowText", callback=self._quit),
        ]

    # ----------------------------------------------------------- Callbacks

    def _remember_state(self, previous: State, current: State) -> None:
        """Observer aus dem Controller-Thread — hier nur merken."""
        self._state = current

    def _tick(self, _timer: Any) -> None:
        """Läuft im Hauptthread; nur hier wird die Oberfläche angefasst."""
        if not self._started:
            # Jetzt läuft die Cocoa-Runloop — ab hier ist der Start sicher.
            # Das Modell lädt nebenher, damit das Icon sofort erscheint.
            self._started = True
            self.app.start(background_warmup=True)

        if self.app.stop_requested:
            # SIGTERM/SIGINT setzen nur ein Event. rumps blockiert den
            # Hauptthread, also muss der Timer das Beenden auslösen —
            # sonst wirkt ein pkill folgenlos.
            import rumps

            rumps.quit_application()
            return

        self._ticks += 1
        if not self._permissions_ok and self._ticks % PERMISSION_RECHECK_TICKS == 0:
            # Erst nachsehen, wenn etwas fehlt: sobald der Nutzer die Freigabe
            # erteilt, soll das Schloss ohne Neustart verschwinden.
            self._permissions_ok = permissions.check().ok
            if self._permissions_ok:
                log.info("Permissions granted.")

        paused = self.app.paused
        self._ui.title = icon_for(self._state, paused, self._permissions_ok)
        self._items["status"].title = label_for(self._state, paused, self._permissions_ok)
        self._items["pause"].title = "Resume" if paused else "Pause"

    def _toggle_pause(self, _sender: Any) -> None:
        self.app.set_paused(not self.app.paused)

    def _open_accessibility(self, _sender: Any) -> None:
        _open(SETTINGS_URLS["Bedienungshilfen"])

    def _open_input_monitoring(self, _sender: Any) -> None:
        _open(SETTINGS_URLS["Eingabeüberwachung"])

    def _open_config(self, _sender: Any) -> None:
        path = self.cfg.source if self.cfg.source != "defaults" else None
        if path is None:
            existing = [p for p in candidate_paths() if p.is_file()]
            path = str(existing[0]) if existing else None
        if path is None:
            log.warning("Keine config.toml gefunden — es gelten die Defaults.")
            return
        _open(path)

    def _open_log(self, _sender: Any) -> None:
        _open(str(log_dir() / "flowtext.log"))

    def _quit(self, _sender: Any) -> None:
        import rumps

        self.app.request_stop()
        rumps.quit_application()
