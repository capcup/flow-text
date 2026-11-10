"""Text in die aktive Anwendung schreiben — Zwischenablage plus ``Cmd+V``.

Warum über die Zwischenablage und nicht Zeichen für Zeichen tippen?
Simuliertes Tippen ist bei längeren Diktaten quälend langsam, verliert
Zeichen und stolpert über Autokorrektur. Einfügen ist atomar.

Der Preis: die Zwischenablage wird überschrieben. Deshalb sichert dieses
Modul den vorherigen Inhalt und stellt ihn nach kurzer Verzögerung wieder
her — lange genug, dass das Einfügen durch ist.

Gesichert wird über ``NSPasteboard`` mit **allen** Datentypen. Ein reiner
Textzugriff (pyperclip) liefert für Bilder, Dateien und formatierten Text
``""`` — die Wiederherstellung hätte den Inhalt dann durch einen leeren
String ersetzt. Zwei weitere Regeln:

* Wiederhergestellt wird nur, wenn die Zwischenablage noch unser Transkript
  enthält (``changeCount`` unverändert). Hat der Nutzer inzwischen selbst
  etwas kopiert, bleibt das stehen.
* Das Transkript wird als *transient* markiert (nspasteboard.org), damit
  Zwischenablage-Manager es nicht in ihren Verlauf aufnehmen.

``Cmd+V`` wird mit ``pynput`` simuliert (nicht pyautogui): die Abhängigkeit
ist ohnehin da, und ein pyenv-Python ohne Framework-Build macht mit
pyautogui erfahrungsgemäß Ärger.

Hinweis zum eigenen Hotkey: das simulierte ``Cmd+V`` sieht unser Listener
selbst. Es löst aber nichts aus, weil der Tastendruck auf ``v`` die
Doppel-Tap-Sequenz verwirft — siehe Regressionstest in ``test_injector.py``.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Protocol

log = logging.getLogger(__name__)

#: Wartezeit zwischen Setzen der Zwischenablage und ``Cmd+V``. macOS braucht
#: einen Moment, bis der neue Inhalt systemweit sichtbar ist.
_PASTE_DELAY = 0.06


#: Konvention von nspasteboard.org: Inhalt ist nur kurz da, nicht archivieren.
_TRANSIENT_TYPE = "org.nspasteboard.TransientType"


class InjectionError(RuntimeError):
    """Der Text konnte nicht eingefügt werden."""


class Pasteboard(Protocol):
    """Zugriff auf die Zwischenablage — für Tests austauschbar."""

    def snapshot(self) -> Any:
        """Sichert den aktuellen Inhalt; ``None`` = nicht wiederherstellbar."""

    def write_text(self, text: str, *, transient: bool) -> None: ...

    def change_count(self) -> int | None:
        """Zähler, der sich bei jeder Änderung erhöht; ``None`` = unbekannt."""

    def restore(self, snapshot: Any) -> None: ...


class NativePasteboard:
    """``NSPasteboard`` — sichert alle Datentypen, nicht nur Text."""

    def __init__(self, pasteboard: Any = None) -> None:
        import AppKit

        self._kit: Any = AppKit
        self._pb: Any = pasteboard or AppKit.NSPasteboard.generalPasteboard()

    def snapshot(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for item in self._pb.pasteboardItems() or []:
            data = {}
            for kind in item.types():
                value = item.dataForType_(kind)
                if value is not None:
                    data[str(kind)] = value
            if data:
                items.append(data)
        return items

    def write_text(self, text: str, *, transient: bool) -> None:
        item = self._kit.NSPasteboardItem.alloc().init()
        item.setString_forType_(text, self._kit.NSPasteboardTypeString)
        if transient:
            item.setData_forType_(self._kit.NSData.data(), _TRANSIENT_TYPE)
        self._pb.clearContents()
        if not self._pb.writeObjects_([item]):
            raise RuntimeError("NSPasteboard hat den Text nicht angenommen")

    def change_count(self) -> int:
        return int(self._pb.changeCount())

    def restore(self, snapshot: list[dict[str, Any]]) -> None:
        self._pb.clearContents()
        items = []
        for data in snapshot:
            item = self._kit.NSPasteboardItem.alloc().init()
            for kind, value in data.items():
                item.setData_forType_(value, kind)
            items.append(item)
        if items:
            self._pb.writeObjects_(items)


class PyperclipPasteboard:
    """Rückfall ohne AppKit: kann nur Text sichern.

    Ein leerer String ist hier mehrdeutig (leer *oder* Bild/Datei) und wird
    deshalb nicht wiederhergestellt — lieber das Transkript liegen lassen
    als fremden Inhalt löschen.
    """

    def snapshot(self) -> str | None:
        import pyperclip

        text = str(pyperclip.paste())
        return text or None

    def write_text(self, text: str, *, transient: bool) -> None:
        import pyperclip

        pyperclip.copy(text)

    def change_count(self) -> int | None:
        return None

    def restore(self, snapshot: str) -> None:
        import pyperclip

        pyperclip.copy(snapshot)


def _default_pasteboard() -> Pasteboard:
    try:
        return NativePasteboard()
    except Exception as exc:  # noqa: BLE001 — AppKit fehlt oder kein Fensterserver
        log.debug("NSPasteboard nicht verfügbar (%s) — nutze pyperclip.", exc)
        return PyperclipPasteboard()


class ClipboardInjector:
    def __init__(
        self,
        *,
        paste: bool = True,
        restore_after: float = 1.0,
        pasteboard: Pasteboard | None = None,
    ) -> None:
        self._paste = paste
        self._restore_after = restore_after
        self._pasteboard = pasteboard
        self._timer: threading.Timer | None = None
        #: Ausstehende Wiederherstellung: (Sicherung, Zählerstand nach unserem
        #: Schreiben). Schützt den Originalinhalt bei schnell aufeinander
        #: folgenden Diktaten.
        self._pending: tuple[Any, int | None] | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ API

    def warmup(self) -> None:
        try:
            self._board().change_count()
        except Exception as exc:  # noqa: BLE001 — nur eine Frühwarnung
            log.warning("Zwischenablage nicht verfügbar (%s) — Ausgabe wird scheitern.", exc)
            return
        if self._paste:
            log.info("Output: Clipboard + Cmd+V.")
        else:
            log.info("Output: Clipboard only (manual paste).")

    def inject(self, text: str) -> None:
        if not text:
            return
        board = self._board()
        will_restore = self._paste and self._restore_after > 0
        previous = self._previous_content(board) if will_restore else None

        try:
            board.write_text(text, transient=will_restore)
        except Exception as exc:
            raise InjectionError(f"Could not set clipboard: {exc}") from exc
        written_at = self._change_count(board)

        if not self._paste:
            log.info("Text copied to clipboard (%d characters).", len(text))
            return

        time.sleep(_PASTE_DELAY)
        try:
            self._press_paste_shortcut()
        except Exception as exc:  # noqa: BLE001 — Text ist trotzdem in der Ablage
            log.warning(
                "Cmd+V konnte nicht ausgelöst werden (%s). Der Text liegt in der "
                "Zwischenablage — bitte von Hand einfügen.",
                exc,
            )
            return

        self._schedule_restore(board, previous, written_at)

    def shutdown(self) -> None:
        """Wartenden Wiederherstellungs-Timer abbrechen."""
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    # -------------------------------------------------------------- intern

    def _press_paste_shortcut(self) -> None:
        import subprocess

        try:
            subprocess.run(
                [
                    "/usr/bin/osascript",
                    "-e",
                    'tell application "System Events" to keystroke "v" using command down',
                ],
                check=True,
                capture_output=True,
                timeout=3.0,
            )
        except Exception as exc:  # noqa: BLE001 — jeder Fehler soll den Rückfall auslösen
            log.debug("AppleScript Cmd+V fehlgeschlagen (%s), versuche pynput...", exc)
            from pynput.keyboard import Controller, Key

            controller: Any = Controller()
            with controller.pressed(Key.cmd):
                controller.press("v")
                controller.release("v")

    def _board(self) -> Pasteboard:
        if self._pasteboard is None:
            self._pasteboard = _default_pasteboard()
        return self._pasteboard

    @staticmethod
    def _change_count(board: Pasteboard) -> int | None:
        try:
            return board.change_count()
        except Exception:  # noqa: BLE001
            return None

    def _previous_content(self, board: Pasteboard) -> Any:
        """Sichert den Inhalt, der nach dem Einfügen zurückkommen soll."""
        with self._lock:
            pending = self._pending
        if pending is not None:
            snapshot, written_at = pending
            if written_at is not None and self._change_count(board) == written_at:
                # Das vorige Transkript liegt noch in der Ablage, seine
                # Wiederherstellung steht aus: der Originalinhalt ist die
                # ältere Sicherung, nicht das Transkript.
                return snapshot
        try:
            return board.snapshot()
        except Exception as exc:  # noqa: BLE001 — dann eben nicht wiederherstellen
            log.debug("Zwischenablage konnte nicht gesichert werden: %s", exc)
            return None

    def _schedule_restore(self, board: Pasteboard, previous: Any, written_at: int | None) -> None:
        if previous is None or self._restore_after <= 0:
            return

        def restore() -> None:
            with self._lock:
                self._pending = None
            try:
                if written_at is not None and board.change_count() != written_at:
                    log.debug("Zwischenablage wurde inzwischen geändert — bleibt unangetastet.")
                    return
                board.restore(previous)
            except Exception as exc:  # noqa: BLE001
                log.debug("Zwischenablage konnte nicht wiederhergestellt werden: %s", exc)

        self.shutdown()
        with self._lock:
            self._pending = (previous, written_at)
        self._timer = threading.Timer(self._restore_after, restore)
        self._timer.daemon = True
        self._timer.start()
