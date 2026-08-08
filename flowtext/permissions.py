"""macOS-Berechtigungsprüfung für den globalen Tastatur-Event-Tap.

Zwei getrennte TCC-Dienste sind relevant und werden hier BEIDE geprüft:

* ``kTCCServiceAccessibility``  — "Bedienungshilfen"     (AXIsProcessTrusted)
* ``kTCCServiceListenEvent``    — "Eingabeüberwachung"   (IOHIDCheckAccess)

Bekannte Fallstricke, die dieses Modul bewusst vermeidet:

1. ``AXIsProcessTrusted()`` liefert einen ``Boolean`` (1 Byte). ctypes nimmt
   ohne ``restype`` einen ``c_int`` (4 Byte) an — die oberen drei Byte sind
   undefiniert und können den Wert fälschlich zu ``True`` machen.
2. Ein fehlgeschlagener Check darf niemals "alles in Ordnung" melden
   (fail-loud statt fail-open).
"""

from __future__ import annotations

import ctypes
import os
import sys
from dataclasses import dataclass
from enum import IntEnum

_APPLICATION_SERVICES = (
    "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
)
_IOKIT = "/System/Library/Frameworks/IOKit.framework/IOKit"

#: kIOHIDRequestTypeListenEvent aus <IOKit/hidsystem/IOHIDLib.h>
_K_IOHID_REQUEST_TYPE_LISTEN_EVENT = 1


class HIDAccess(IntEnum):
    """Rückgabewerte von ``IOHIDCheckAccess``."""

    GRANTED = 0
    DENIED = 1
    UNKNOWN = 2

    @property
    def label(self) -> str:
        return {
            HIDAccess.GRANTED: "erteilt",
            HIDAccess.DENIED: "verweigert",
            HIDAccess.UNKNOWN: "unbekannt (noch nie angefragt)",
        }[self]


@dataclass(frozen=True)
class Permissions:
    """Ergebnis der Berechtigungsprüfung."""

    accessibility: bool
    input_monitoring: HIDAccess
    checked: bool = True

    @property
    def ok(self) -> bool:
        """Reicht das für einen funktionierenden Event-Tap?

        ``UNKNOWN`` gilt als ausreichend: macOS zeigt beim ersten Tap-Versuch
        den Systemdialog an. ``DENIED`` ist ein hartes Nein.
        """
        return self.accessibility and self.input_monitoring is not HIDAccess.DENIED

    def summary(self) -> str:
        if not self.checked:
            return "Berechtigungen: nicht geprüft (kein macOS)"
        return (
            f"Bedienungshilfen: {'erteilt' if self.accessibility else 'FEHLT'} | "
            f"Eingabeüberwachung: {self.input_monitoring.label}"
        )


def check() -> Permissions:
    """Prüft beide Berechtigungen. Wirft nicht — Fehler werden zu ``DENIED``."""
    if sys.platform != "darwin":
        return Permissions(accessibility=True, input_monitoring=HIDAccess.GRANTED, checked=False)

    accessibility = False
    try:
        apps = ctypes.cdll.LoadLibrary(_APPLICATION_SERVICES)
        # WICHTIG: Boolean == 1 Byte. Ohne restype liest ctypes 4 Byte Müll.
        apps.AXIsProcessTrusted.restype = ctypes.c_bool
        apps.AXIsProcessTrusted.argtypes = []
        accessibility = bool(apps.AXIsProcessTrusted())
    except OSError:
        accessibility = False

    hid = HIDAccess.UNKNOWN
    try:
        iokit = ctypes.cdll.LoadLibrary(_IOKIT)
        iokit.IOHIDCheckAccess.restype = ctypes.c_int
        iokit.IOHIDCheckAccess.argtypes = [ctypes.c_uint32]
        hid = HIDAccess(iokit.IOHIDCheckAccess(_K_IOHID_REQUEST_TYPE_LISTEN_EVENT))
    except (OSError, AttributeError, ValueError):
        hid = HIDAccess.UNKNOWN

    return Permissions(accessibility=accessibility, input_monitoring=hid)


_HELP_TEMPLATE = """\
FlowText braucht zwei macOS-Berechtigungen — und zwar für die App, aus der es
gestartet wurde, NICHT für das Python-Binary.

Freizugebende App: {app}

  1) Systemeinstellungen → Datenschutz & Sicherheit → Bedienungshilfen
  2) Systemeinstellungen → Datenschutz & Sicherheit → Eingabeüberwachung

In BEIDEN Listen muss diese App stehen und der Schalter aktiv sein.
Fehlt sie: "+" klicken, mit Cmd+Shift+G den Pfad oben eingeben, hinzufügen.

Danach die App mit Cmd+Q VOLLSTÄNDIG beenden (Fenster schließen genügt
nicht) und neu starten.

Hilft das nicht, die Einträge zurücksetzen und neu erteilen lassen:

    tccutil reset Accessibility
    tccutil reset ListenEvent

Danach FlowText erneut starten und den Systemdialog bestätigen.

Weitere Stolpersteine:
  * Das IDE-Terminal (VS Code, Cursor) braucht eine EIGENE Freigabe.
    Zum Einrichten Terminal.app verwenden.
  * tmux, screen oder Wrapper wie "uv run" können die Zuordnung
    verschieben — zum Testen direkt in einem frischen Terminal-Fenster
    starten.
"""


def help_text() -> str:
    """Anleitung mit konkret benannter App statt allgemeiner Aufzählung."""
    app = responsible_app() if sys.platform == "darwin" else None
    return _HELP_TEMPLATE.format(
        app=app or "nicht ermittelbar — Terminal.app, iTerm2, VS Code oder Cursor"
    )


def process_chain(max_depth: int = 12) -> list[tuple[int, str]]:
    """Prozesskette von diesem Prozess aufwärts: ``[(pid, Kommando), …]``.

    macOS vergibt TCC-Rechte an den *verantwortlichen* Prozess — also an die
    App-Bundle-Vorfahren dieses Python-Prozesses, nicht an Python selbst.
    """
    import subprocess

    chain: list[tuple[int, str]] = []
    pid = os.getpid()
    for _ in range(max_depth):
        if pid <= 1:
            break
        try:
            result = subprocess.run(
                ["/bin/ps", "-o", "ppid=,command=", "-p", str(pid)],
                capture_output=True,
                text=True,
                timeout=2.0,
            )
            line = result.stdout.strip()
            if not line or result.returncode != 0:
                break
            ppid_text, _, command = line.partition(" ")
            chain.append((pid, command.strip()))
            try:
                pid = int(ppid_text)
            except ValueError:
                break
        except (OSError, subprocess.SubprocessError):
            break

    if not chain:
        chain.append((os.getpid(), sys.executable))
    return chain


def responsible_app() -> str | None:
    """Das App-Bundle, das die Berechtigungen braucht — oder ``None``.

    Genau dieser Pfad muss in Bedienungshilfen und Eingabeüberwachung stehen.
    """
    marker = ".app/Contents/MacOS/"
    for _, command in process_chain():
        if marker in command:
            return command.split(".app/", 1)[0] + ".app"
    return None


def environment_report() -> str:
    """Diagnosezeile für das Log — welcher Interpreter läuft eigentlich?"""
    parts = [
        f"python={sys.version.split()[0]}",
        f"executable={sys.executable}",
        f"platform={sys.platform}",
        f"pid={os.getpid()}",
    ]
    term = os.environ.get("TERM_PROGRAM")
    if term:
        parts.append(f"term_program={term}")
    return " | ".join(parts)


def require() -> Permissions:
    """Prüft die Berechtigungen und beendet den Prozess bei Verweigerung."""
    perms = check()
    if not perms.ok:
        sys.stderr.write("\n" + perms.summary() + "\n\n" + help_text() + "\n")
        raise SystemExit(2)
    return perms
