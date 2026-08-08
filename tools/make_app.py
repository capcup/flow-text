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

from flowtext.bundle import (
    DEFAULT_BUNDLE_ID,
    BundleSpec,
    build,
    codesign,
    next_steps,
    site_packages_of,
    verify_interpreter,
)
from flowtext.config import log_dir

PROJECT = Path(__file__).resolve().parent.parent


def find_python(project: Path = PROJECT) -> Path:
    """Der Interpreter, der das Bundle starten soll.

    **Nicht ``resolve()`` verwenden.** ``.venv/bin/python3`` ist ein Symlink
    auf den Basis-Interpreter (z. B. pyenv). Wird er aufgelöst, zeigt das
    Bundle am ``.venv`` vorbei — und findet keine einzige Abhängigkeit mehr.
    Der Symlink-Pfad ist genau das, was die virtuelle Umgebung ausmacht.
    """
    for name in ("python3", "python"):
        candidate = project / ".venv" / "bin" / name
        if candidate.is_file():
            return candidate
    return Path(sys.executable)


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
    parser.add_argument("--no-verify", action="store_true", help="Importprüfung überspringen")
    parser.add_argument("--open", action="store_true", help="Bundle danach starten")
    args = parser.parse_args(argv)

    if sys.platform != "darwin":
        sys.stderr.write("FlowText.app lässt sich nur unter macOS erzeugen.\n")
        return 2

    python = args.python.expanduser() if args.python else find_python()
    if not python.is_absolute():
        python = python.absolute()

    print(f"Interpreter : {python}")
    print(f"Projekt     : {PROJECT}")
    print(f"Bundle-ID   : {args.bundle_id}")

    if not args.no_verify:
        ok, message = verify_interpreter(python, PROJECT)
        print(f"Abhängigkeiten: {message}")
        if not ok:
            sys.stderr.write(
                "\n❌ Dieser Interpreter kann FlowText nicht starten — Abbruch.\n\n"
                "   Meistens fehlt das .venv oder es ist unvollständig:\n"
                f"       cd {PROJECT}\n"
                "       python3 -m venv .venv\n"
                "       source .venv/bin/activate\n"
                "       pip install -r requirements.txt\n\n"
                "   Prüfen ohne Abbruch: --no-verify\n"
            )
            return 1

    site_packages = site_packages_of(python)
    print(f"site-packages: {site_packages or 'nicht ermittelbar'}")

    spec = BundleSpec(
        python=python,
        project=PROJECT,
        log_dir=log_dir(),
        site_packages=site_packages,
        bundle_id=args.bundle_id,
    )

    args.dest.mkdir(parents=True, exist_ok=True)
    result = build(spec, args.dest)
    print(f"Einstiegspunkt: {result.note}")

    if args.no_sign:
        print("Signatur    : übersprungen (--no-sign)")
        print(
            "\n⚠️  Ohne Signatur identifiziert macOS das Bundle über seinen Hash.\n"
            "   Die Berechtigung geht dann bei jedem Neubau verloren."
        )
    else:
        ok, message = codesign(result.app_path, spec.bundle_id)
        print(f"Signatur    : {message}")
        if not ok:
            print("\n⚠️  Signieren fehlgeschlagen — das Bundle läuft, aber die")
            print("   Berechtigung überlebt einen Neubau womöglich nicht.")

    print("\n" + next_steps(result))

    if args.open:
        subprocess.Popen(["/usr/bin/open", str(result.app_path)])
        print(f"Gestartet: {result.app_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
