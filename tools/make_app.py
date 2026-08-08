#!/usr/bin/env python3
"""Erzeugt ``FlowText.app`` — ein Launcher-Bundle mit eigener TCC-Identität.

Damit steht FlowText selbst in den Systemeinstellungen unter Bedienungshilfen
und Eingabeüberwachung, und das Terminal braucht diese Rechte nicht mehr.

    python tools/make_app.py                 # nach dist/ bauen
    python tools/make_app.py --dest ~/Applications
    python tools/make_app.py --open          # danach gleich starten

Es wird nichts eingepackt: das Bundle ruft dein vorhandenes ``.venv`` auf.
Details siehe :mod:`flowtext.bundle`.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flowtext.bundle import DEFAULT_BUNDLE_ID, BundleSpec, build, codesign, next_steps
from flowtext.config import log_dir

PROJECT = Path(__file__).resolve().parent.parent


def _find_python() -> Path:
    """Der Interpreter, der das Bundle starten soll.

    Bevorzugt das ``.venv`` des Projekts — dort liegen die Abhängigkeiten.
    Läuft dieses Skript bereits darin, ist es ohnehin dasselbe.
    """
    venv_python = PROJECT / ".venv" / "bin" / "python3"
    if venv_python.is_file():
        return venv_python.resolve()
    return Path(sys.executable).resolve()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FlowText.app erzeugen")
    parser.add_argument(
        "--dest",
        type=Path,
        default=PROJECT / "dist",
        help="Zielverzeichnis (Standard: dist/)",
    )
    parser.add_argument("--python", type=Path, help="Interpreter für das Bundle")
    parser.add_argument("--bundle-id", default=DEFAULT_BUNDLE_ID, help="CFBundleIdentifier")
    parser.add_argument("--no-sign", action="store_true", help="Ad-hoc-Signatur überspringen")
    parser.add_argument("--open", action="store_true", help="Bundle danach starten")
    args = parser.parse_args(argv)

    if sys.platform != "darwin":
        sys.stderr.write("FlowText.app lässt sich nur unter macOS erzeugen.\n")
        return 2

    python = (args.python or _find_python()).resolve()
    if not python.is_file():
        sys.stderr.write(f"Interpreter nicht gefunden: {python}\n")
        return 2

    spec = BundleSpec(
        python=python,
        project=PROJECT,
        log_dir=log_dir(),
        bundle_id=args.bundle_id,
    )

    args.dest.mkdir(parents=True, exist_ok=True)
    app_path = build(spec, args.dest)

    print(f"Interpreter : {python}")
    print(f"Projekt     : {PROJECT}")
    print(f"Bundle-ID   : {spec.bundle_id}")

    if args.no_sign:
        print("Signatur    : übersprungen (--no-sign)")
        print(
            "\n⚠️  Ohne Signatur identifiziert macOS das Bundle über seinen Hash.\n"
            "   Die Berechtigung geht dann bei jedem Neubau verloren."
        )
    else:
        ok, message = codesign(app_path, spec.bundle_id)
        print(f"Signatur    : {message}")
        if not ok:
            print("\n⚠️  Signieren fehlgeschlagen — das Bundle läuft, aber die")
            print("   Berechtigung überlebt einen Neubau womöglich nicht.")

    print("\n" + next_steps(app_path))

    if args.open:
        subprocess.Popen(["/usr/bin/open", str(app_path)])
        print(f"Gestartet: {app_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
