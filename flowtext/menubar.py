"""Menüleisten-Oberfläche (rumps).

Schließt die auffälligste Lücke der CLI-Variante: man hört zwar Töne, sieht
aber nie, ob gerade aufgenommen wird oder die Transkription noch läuft.

**Zwei Regeln bestimmen den Aufbau:**

1. *Cocoa gehört der Hauptthread.* ``rumps`` blockiert ihn, deshalb startet
   :class:`~flowtext.app.FlowTextApp` hier über ``start()`` statt ``run()``.
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
from typing import Any

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

LABELS: dict[State, str] = {
    State.IDLE: "Bereit",
    State.RECORDING: "Nimmt auf …",
    State.PROCESSING: "Verarbeitet …",
    State.ERROR: "Fehler — siehe Log",
}

#: Wie oft die Oberfläche den Zustand abholt. 0,2 s ist unauffällig und
#: kostet praktisch nichts.
TICK_SECONDS = 0.2


def available() -> bool:
    """Ist ``rumps`` installiert?"""
    if "rumps" in sys.modules:
        return True
    try:
        return importlib.util.find_spec("rumps") is not None
    except (ImportError, ValueError):
        return False


def icon_for(state: State, paused: bool) -> str:
    return PAUSED_ICON if paused else ICONS.get(state, ICONS[State.IDLE])


def label_for(state: State, paused: bool) -> str:
    if paused:
        return "Pausiert"
    return LABELS.get(state, str(state))


def _open(path: str) -> None:
    """Datei in der Standardanwendung öffnen."""
    try:
        subprocess.Popen(["/usr/bin/open", path])
    except OSError as exc:
        log.warning("Konnte %s nicht öffnen: %s", path, exc)


class MenubarApp:
    """Verbindet :class:`FlowTextApp` mit einem rumps-Statusicon."""

    def __init__(self, cfg: Config, app: FlowTextApp) -> None:
        self.cfg = cfg
        self.app = app
        self._state = State.IDLE
        self._ui: Any = None
        self._items: dict[str, Any] = {}
        # Läuft im Controller-Thread: NUR schreiben, kein Cocoa-Zugriff.
        app.state.subscribe(self._remember_state)

    # ------------------------------------------------------------------ API

    def run(self) -> None:
        """Blockiert den Hauptthread, bis der Nutzer beendet."""
        import rumps

        self._ui = rumps.App(
            "FlowText",
            title=icon_for(self._state, self.app.paused),
            quit_button=None,
        )
        self._build_menu(rumps)

        self.app.start()
        rumps.Timer(self._tick, TICK_SECONDS).start()
        try:
            self._ui.run()
        finally:
            self.app.shutdown()

    # ------------------------------------------------------------- Aufbau

    def _build_menu(self, rumps: Any) -> None:
        status = rumps.MenuItem(label_for(self._state, self.app.paused))
        status.set_callback(None)  # reine Anzeige
        trigger = rumps.MenuItem(f"Auslöser: {self.app.hotkey.describe()}")
        trigger.set_callback(None)

        self._items = {
            "status": status,
            "pause": rumps.MenuItem("Pausieren", callback=self._toggle_pause),
        }
        self._ui.menu = [
            status,
            trigger,
            None,
            self._items["pause"],
            None,
            rumps.MenuItem("Konfiguration öffnen …", callback=self._open_config),
            rumps.MenuItem("Logdatei öffnen …", callback=self._open_log),
            None,
            rumps.MenuItem("FlowText beenden", callback=self._quit),
        ]

    # ----------------------------------------------------------- Callbacks

    def _remember_state(self, previous: State, current: State) -> None:
        """Observer aus dem Controller-Thread — hier nur merken."""
        self._state = current

    def _tick(self, _timer: Any) -> None:
        """Läuft im Hauptthread; nur hier wird die Oberfläche angefasst."""
        paused = self.app.paused
        self._ui.title = icon_for(self._state, paused)
        self._items["status"].title = label_for(self._state, paused)
        self._items["pause"].title = "Fortsetzen" if paused else "Pausieren"

    def _toggle_pause(self, _sender: Any) -> None:
        self.app.set_paused(not self.app.paused)

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
