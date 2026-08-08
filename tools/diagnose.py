#!/usr/bin/env python3
"""FlowText-Diagnose.

Prüft in dieser Reihenfolge (von außen nach innen):

1. Umgebung — welcher Interpreter, welche startende App?
2. Berechtigungen — Bedienungshilfen UND Eingabeüberwachung
3. Mikrofon — Geräte auflisten und 2 s testweise aufnehmen
4. Tastatur — Live-Anzeige aller Events inkl. normalisierter Namen
5. Trigger — meldet, wenn der konfigurierte Auslöser erkannt wurde

Aufruf:

    python tools/diagnose.py
    python tools/diagnose.py --no-listen     # ohne Schritt 4/5
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flowtext import permissions
from flowtext.config import ConfigError, load
from flowtext.hotkey.detectors import build_detector
from flowtext.hotkey.keynames import normalize

OK = "✅"
FAIL = "❌"
WARN = "⚠️ "


def _section(number: int, title: str) -> None:
    print(f"\n{'─' * 68}\n{number}. {title}\n{'─' * 68}")


def check_environment() -> None:
    _section(1, "Umgebung")
    print(f"   {permissions.environment_report()}")
    if ".pyenv" in sys.executable or ".venv" in sys.executable:
        print(
            f"   {WARN}pyenv/venv erkannt. macOS vergibt Berechtigungen an die\n"
            "      startende App (Terminal.app, iTerm2, VS Code …), nicht an\n"
            "      dieses Binary. Zum Einrichten Terminal.app verwenden."
        )


def check_permissions() -> bool:
    _section(2, "Berechtigungen")
    perms = permissions.check()
    print(f"   Bedienungshilfen    : {OK if perms.accessibility else FAIL}")
    print(
        f"   Eingabeüberwachung  : {OK if perms.input_monitoring.value == 0 else FAIL} "
        f"({perms.input_monitoring.label})"
    )
    if not perms.ok:
        print("\n" + permissions.HELP_TEXT)
    return perms.ok


def check_microphone(seconds: float = 2.0) -> bool:
    _section(3, "Mikrofon")
    try:
        import numpy as np
        import sounddevice as sd
    except ImportError as exc:
        print(f"   {FAIL}Import fehlgeschlagen: {exc}")
        return False

    try:
        from flowtext.audio.devices import list_input_devices

        devices = list_input_devices()
    except Exception as exc:  # noqa: BLE001
        print(f"   {FAIL}Geräte konnten nicht abgefragt werden: {exc}")
        return False

    if not devices:
        print(f"   {FAIL}Kein Eingabegerät gefunden.")
        return False
    for dev in devices:
        marker = " (Standard)" if dev.is_default else ""
        print(f"   [{dev.index}] {dev.name} — {dev.channels} Kanäle{marker}")

    print(f"\n   Nehme {seconds:.0f}s auf … bitte sprechen.")
    try:
        data = sd.rec(int(seconds * 16000), samplerate=16000, channels=1, dtype="float32")
        sd.wait()
    except Exception as exc:  # noqa: BLE001
        print(f"   {FAIL}Aufnahme fehlgeschlagen: {exc}")
        print("      → Systemeinstellungen → Datenschutz & Sicherheit → Mikrofon")
        return False

    peak = float(np.max(np.abs(data)))
    bar = "█" * int(min(peak, 1.0) * 40)
    print(f"   Pegel: {peak:.4f} |{bar}")
    if peak < 0.001:
        print(f"   {FAIL}Signal ist stumm — Mikrofonberechtigung oder falsches Gerät.")
        return False
    print(f"   {OK}Mikrofon liefert Signal.")
    return True


def listen(duration: float = 60.0) -> None:
    _section(4, "Tastatur-Events")
    try:
        cfg = load()
    except ConfigError as exc:
        print(f"   {WARN}Konfiguration ungültig ({exc}) — nutze Defaults.")
        from flowtext.config import Config

        cfg = Config()

    detector = build_detector(
        cfg.hotkey.mode,
        combo=cfg.hotkey.combo,
        tap_key=cfg.hotkey.tap_key,
        double_tap_window=cfg.hotkey.double_tap_window,
        max_tap_duration=cfg.hotkey.max_tap_duration,
    )
    print(f"   Erwarteter Auslöser: {detector.describe()}")
    print(f"   Beobachte {duration:.0f}s. Bitte den Auslöser jetzt betätigen.")
    print("   (Ctrl+C beendet früher)\n")

    from pynput import keyboard

    pressed: set[str] = set()
    hits = 0

    def on_press(key: object) -> None:
        nonlocal hits
        name = normalize(key)
        if name is None:
            return
        pressed.add(name)
        fired = detector.on_press(name, time.perf_counter())
        marker = "  ← AUSGELÖST" if fired else ""
        if fired:
            hits += 1
        print(f"   ↓ {name:<12} raw={key!r:<28} gedrückt={sorted(pressed)}{marker}")

    def on_release(key: object) -> None:
        nonlocal hits
        name = normalize(key)
        if name is None:
            return
        pressed.discard(name)
        fired = detector.on_release(name, time.perf_counter())
        if fired:
            hits += 1
            print(f"   ↑ {name:<12}  ← AUSGELÖST")

    listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.start()
    listener.wait()

    try:
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline and listener.running:
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        listener.stop()

    _section(5, "Ergebnis")
    if hits:
        print(f"   {OK}Auslöser wurde {hits}× korrekt erkannt.")
    elif not pressed and not hits:
        print(
            f"   {FAIL}Es kamen keine oder keine passenden Events an.\n"
            "      → Berechtigungen prüfen (Schritt 2), Terminal mit Cmd+Q neu starten."
        )
    else:
        print(
            f"   {WARN}Events kamen an, aber der Auslöser wurde nicht erkannt.\n"
            "      → Oben nachsehen, welchen Namen die Taste bekommt, und\n"
            "        hotkey.tap_key bzw. hotkey.combo entsprechend anpassen."
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FlowText-Diagnose")
    parser.add_argument("--no-listen", action="store_true", help="Tastaturtest überspringen")
    parser.add_argument("--no-mic", action="store_true", help="Mikrofontest überspringen")
    parser.add_argument("--seconds", type=float, default=60.0, help="Dauer des Tastaturtests")
    args = parser.parse_args(argv)

    print("═" * 68)
    print("  FLOWTEXT DIAGNOSE")
    print("═" * 68)

    check_environment()
    perms_ok = check_permissions()
    if not args.no_mic:
        check_microphone()
    if not args.no_listen:
        listen(args.seconds)

    return 0 if perms_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
