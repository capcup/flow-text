"""Text in die aktive Anwendung schreiben — Zwischenablage plus ``Cmd+V``.

Warum über die Zwischenablage und nicht Zeichen für Zeichen tippen?
Simuliertes Tippen ist bei längeren Diktaten quälend langsam, verliert
Zeichen und stolpert über Autokorrektur. Einfügen ist atomar.

Der Preis: die Zwischenablage wird überschrieben. Deshalb sichert dieses
Modul den vorherigen Inhalt und stellt ihn nach kurzer Verzögerung wieder
her — lange genug, dass das Einfügen durch ist.

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
from typing import Any

log = logging.getLogger(__name__)

#: Wartezeit zwischen Setzen der Zwischenablage und ``Cmd+V``. macOS braucht
#: einen Moment, bis der neue Inhalt systemweit sichtbar ist.
_PASTE_DELAY = 0.06


class InjectionError(RuntimeError):
    """Der Text konnte nicht eingefügt werden."""


class ClipboardInjector:
    def __init__(self, *, paste: bool = True, restore_after: float = 1.0) -> None:
        self._paste = paste
        self._restore_after = restore_after
        self._timer: threading.Timer | None = None

    # ------------------------------------------------------------------ API

    def warmup(self) -> None:
        try:
            import pyperclip

            pyperclip.paste()
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
        import pyperclip

        previous = self._read_clipboard()
        try:
            pyperclip.copy(text)
        except Exception as exc:
            raise InjectionError(f"Could not set clipboard: {exc}") from exc

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

        self._schedule_restore(previous)

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

    def _read_clipboard(self) -> str | None:
        try:
            import pyperclip

            return str(pyperclip.paste())
        except Exception as exc:  # noqa: BLE001 — dann eben nicht wiederherstellen
            log.debug("Zwischenablage konnte nicht gelesen werden: %s", exc)
            return None

    def _schedule_restore(self, previous: str | None) -> None:
        if previous is None or self._restore_after <= 0:
            return

        def restore() -> None:
            try:
                import pyperclip

                pyperclip.copy(previous)
            except Exception as exc:  # noqa: BLE001
                log.debug("Zwischenablage konnte nicht wiederhergestellt werden: %s", exc)

        self.shutdown()
        self._timer = threading.Timer(self._restore_after, restore)
        self._timer.daemon = True
        self._timer.start()
