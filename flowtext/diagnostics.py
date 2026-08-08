"""Diagnosefunktionen — gemeinsame Basis für ``--watch-keys`` und ``tools/diagnose.py``.

Die Reihenfolge folgt der Fehlersuche von außen nach innen: erst die
Umgebung, dann die Berechtigungen, dann Mikrofon und Tastatur. Die mit
Abstand häufigste Ursache ("es passiert gar nichts") ist eine fehlende
Berechtigung — deshalb beantwortet :func:`watch_keys` zuerst die Frage,
ob überhaupt Ereignisse ankommen.
"""

from __future__ import annotations

import queue
import sys
import time

from . import permissions
from .config import Config, conflicts
from .hotkey.service import TRIGGER, HotkeyService

OK = "✅"
FAIL = "❌"
WARN = "⚠️ "

_RULE = "─" * 68


def section(title: str) -> None:
    print(f"\n{_RULE}\n{title}\n{_RULE}")


# --------------------------------------------------------------------- Umgebung


def report_environment() -> None:
    section("Umgebung")
    print(f"  {permissions.environment_report()}")
    app = permissions.responsible_app()
    print(f"  Verantwortliche App: {app or 'nicht ermittelbar'}")
    print("\n  Prozesskette (von FlowText aufwärts):")
    for pid, command in permissions.process_chain():
        print(f"    {pid:>7}  {command[:84]}")
    if ".pyenv" in sys.executable or ".venv" in sys.executable:
        print(
            f"\n  {WARN}pyenv/venv erkannt. macOS vergibt Berechtigungen an die App\n"
            "     aus der Prozesskette oben, nicht an dieses Python-Binary."
        )


# ---------------------------------------------------------------- Berechtigungen


def report_permissions() -> bool:
    section("Berechtigungen")
    perms = permissions.check()
    print(f"  Bedienungshilfen   : {OK if perms.accessibility else FAIL}")
    granted = perms.input_monitoring is permissions.HIDAccess.GRANTED
    print(f"  Eingabeüberwachung : {OK if granted else FAIL} ({perms.input_monitoring.label})")
    if not perms.ok:
        print("\n" + permissions.help_text())
    return perms.ok


# --------------------------------------------------------------------- Mikrofon


def check_microphone(seconds: float = 2.0) -> bool:
    section("Mikrofon")
    try:
        import numpy as np
        import sounddevice as sd
    except ImportError as exc:
        print(f"  {FAIL}Import fehlgeschlagen: {exc}")
        return False

    try:
        from .audio.devices import list_input_devices

        devices = list_input_devices()
    except Exception as exc:  # noqa: BLE001 — Diagnose soll ALLES melden, nie abstürzen
        print(f"  {FAIL}Geräte konnten nicht abgefragt werden: {exc}")
        return False

    if not devices:
        print(f"  {FAIL}Kein Eingabegerät gefunden.")
        return False
    for device in devices:
        marker = " (Standard)" if device.is_default else ""
        print(f"  [{device.index}] {device.name} — {device.channels} Kanäle{marker}")

    print(f"\n  Nehme {seconds:.0f}s auf … bitte sprechen.")
    try:
        data = sd.rec(int(seconds * 16000), samplerate=16000, channels=1, dtype="float32")
        sd.wait()
    except Exception as exc:  # noqa: BLE001 — Diagnose soll ALLES melden, nie abstürzen
        print(f"  {FAIL}Aufnahme fehlgeschlagen: {exc}")
        print("     → Systemeinstellungen → Datenschutz & Sicherheit → Mikrofon")
        return False

    peak = float(np.max(np.abs(data)))
    print(f"  Pegel: {peak:.4f} |{'█' * int(min(peak, 1.0) * 40)}")
    if peak < 0.001:
        print(f"  {FAIL}Signal ist stumm — Mikrofonberechtigung oder falsches Gerät.")
        return False
    print(f"  {OK}Mikrofon liefert Signal.")
    return True


# --------------------------------------------------------------------- Tastatur


def watch_keys(cfg: Config, seconds: float = 60.0) -> bool:
    """Zeigt live, ob Tastaturereignisse ankommen und ob der Auslöser greift.

    Unterscheidet die beiden Fälle, die sich sonst gleich anfühlen:
    "gar keine Ereignisse" (Berechtigung) und "Ereignisse, aber falscher
    Tastenname" (Konfiguration).
    """
    events: queue.Queue[str] = queue.Queue()
    service = HotkeyService(cfg.hotkey, events)

    section("Tastatur")
    print(f"  Erwarteter Auslöser: {service.describe()}")
    for warning in conflicts(cfg):
        print(f"  {WARN}{warning}")
    print(f"  Beobachte {seconds:.0f}s — bitte jetzt Tasten drücken. (Ctrl+C beendet früher)\n")

    service.start()
    triggers = 0
    deadline = time.monotonic() + seconds
    try:
        while time.monotonic() < deadline:
            try:
                if events.get(timeout=0.2) == TRIGGER:
                    triggers += 1
                    print(f"  {OK} AUSGELÖST")
            except queue.Empty:
                continue
    except KeyboardInterrupt:
        pass
    finally:
        service.stop()

    section("Ergebnis")
    print(f"  Ereignisse gesamt: {service.event_count} | Auslösungen: {triggers}")
    if service.event_count == 0:
        print(f"\n  {FAIL}Es kam KEIN Tastaturereignis an — das ist eine Berechtigung.\n")
        print(permissions.help_text())
        return False
    if triggers == 0:
        print(
            f"\n  {WARN}Ereignisse kommen an, aber der Auslöser wurde nicht erkannt.\n"
            "     Mit --log-level DEBUG ablesen, welchen Namen deine Taste bekommt,\n"
            "     und hotkey.tap_key entsprechend setzen."
        )
        return False
    print(f"\n  {OK}Auslöser funktioniert.")
    return True


# ------------------------------------------------------------------ Komplettlauf


def run_all(cfg: Config, *, mic: bool = True, keys: bool = True, seconds: float = 60.0) -> int:
    """Vollständige Diagnose. Rückgabewert eignet sich als Exit-Code."""
    print("═" * 68)
    print("  FLOWTEXT DIAGNOSE")
    print("═" * 68)

    report_environment()
    ok = report_permissions()
    if mic:
        ok = check_microphone() and ok
    if keys:
        ok = watch_keys(cfg, seconds) and ok
    return 0 if ok else 1
