"""Erzeugt ein ``FlowText.app``-Launcher-Bundle.

**Zweck: eine eigene TCC-Identität.** macOS vergibt Bedienungshilfen und
Eingabeüberwachung an den *verantwortlichen Prozess*. Startet FlowText aus
dem Terminal, ist das iTerm oder Terminal.app — die Freigabe gilt dann jeder
Anwendung, die du dort startest. Startet FlowText dagegen aus einem
App-Bundle, taucht **FlowText selbst** in der Liste auf und das Terminal
braucht gar keine Rechte mehr.

**Warum kein py2app.** Es wird nichts eingepackt. Das Bundle enthält nur eine
``Info.plist`` und ein Startskript, das dein vorhandenes ``.venv`` aufruft:

    FlowText.app/Contents/
    ├── Info.plist        Bundle-ID, LSUIElement, Mikrofon-Begründung
    └── MacOS/FlowText    #!/bin/zsh … exec …/python -m flowtext --menubar

Das ``.venv`` bleibt, wo es ist; die 2 GB Modelldaten werden nicht kopiert.
Aktualisierst du den Code, ist das Bundle sofort aktuell — es zeigt ja nur
darauf.

**Drei Details, an denen es sonst scheitert:**

* ``NSMicrophoneUsageDescription`` ist Pflicht. Fehlt der Schlüssel, beendet
  macOS die App beim ersten Mikrofonzugriff kommentarlos.
* ``LSUIElement`` verhindert das Dock-Icon — bei einer Menüleisten-App
  unerwünscht.
* Das Bundle muss signiert sein, und sei es nur ad-hoc. Ohne Signatur hängt
  der TCC-Eintrag am Inhalts-Hash und geht bei jedem Neubau kaputt.
"""

from __future__ import annotations

import plistlib
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import __version__

#: Muss stabil bleiben — daran hängt der TCC-Eintrag.
DEFAULT_BUNDLE_ID = "dev.local.flowtext"
APP_NAME = "FlowText"

#: Der Launcher setzt diese Variable, damit die Diagnose das Bundle erkennt.
#: Nötig, weil ``exec`` die Shell ersetzt: in der Prozesskette steht danach
#: nur noch der Python-Pfad, das ``.app`` ist nicht mehr zu sehen.
BUNDLE_ENV_VAR = "FLOWTEXT_APP_BUNDLE"

_MIC_REASON = "FlowText nimmt dein Diktat auf und transkribiert es lokal auf diesem Mac."


@dataclass(frozen=True)
class BundleSpec:
    """Alles, was das Bundle über seine Umgebung wissen muss."""

    python: Path
    project: Path
    log_dir: Path
    bundle_id: str = DEFAULT_BUNDLE_ID
    app_name: str = APP_NAME
    version: str = __version__


# --------------------------------------------------------------------- Inhalte


def info_plist(spec: BundleSpec) -> dict[str, Any]:
    return {
        "CFBundleName": spec.app_name,
        "CFBundleDisplayName": spec.app_name,
        "CFBundleExecutable": spec.app_name,
        "CFBundleIdentifier": spec.bundle_id,
        "CFBundlePackageType": "APPL",
        "CFBundleVersion": spec.version,
        "CFBundleShortVersionString": spec.version,
        "CFBundleInfoDictionaryVersion": "6.0",
        "LSMinimumSystemVersion": "13.0",
        # Menüleisten-App: kein Dock-Icon, kein Programmumschalter-Eintrag.
        "LSUIElement": True,
        "NSHighResolutionCapable": True,
        # Ohne diesen Schlüssel beendet macOS die App beim Mikrofonzugriff.
        "NSMicrophoneUsageDescription": _MIC_REASON,
    }


def launcher_script(spec: BundleSpec, app_path: Path) -> str:
    """Startskript des Bundles.

    ``exec`` ist wichtig: dadurch *wird* der Python-Prozess der Prozess des
    Bundles, statt ein Kindprozess einer Shell zu sein. Nur so erbt er die
    Berechtigungen des Bundles zuverlässig.
    """
    python = shlex.quote(str(spec.python))
    project = shlex.quote(str(spec.project))
    log_dir = shlex.quote(str(spec.log_dir))
    bundle = shlex.quote(str(app_path))
    return f"""#!/bin/zsh
# FlowText-Launcher — erzeugt von tools/make_app.py. Nicht von Hand ändern;
# bei Änderungen am Projektpfad das Bundle neu erzeugen.

# In das Projekt wechseln, damit config.toml gefunden wird.
cd {project} || exit 1

mkdir -p {log_dir}

# Damit die Diagnose weiß, dass wir aus dem Bundle kommen (exec verdeckt das).
export {BUNDLE_ENV_VAR}={bundle}
export PYTHONUNBUFFERED=1

# exec: der Python-Prozess ersetzt die Shell und wird damit der Prozess des
# Bundles. Ausgaben vor dem Logging-Setup landen in launcher.log.
exec {python} -m flowtext --menubar >> {log_dir}/launcher.log 2>&1
"""


# ---------------------------------------------------------------------- Bauen


def build(spec: BundleSpec, dest_dir: Path) -> Path:
    """Legt ``<dest_dir>/<app_name>.app`` an und gibt den Pfad zurück."""
    app_path = dest_dir / f"{spec.app_name}.app"
    contents = app_path / "Contents"
    macos = contents / "MacOS"

    if app_path.exists():
        shutil.rmtree(app_path)
    macos.mkdir(parents=True)

    with (contents / "Info.plist").open("wb") as fh:
        plistlib.dump(info_plist(spec), fh)

    executable = macos / spec.app_name
    executable.write_text(launcher_script(spec, app_path), encoding="utf-8")
    executable.chmod(0o755)

    return app_path


def codesign(app_path: Path, bundle_id: str = DEFAULT_BUNDLE_ID) -> tuple[bool, str]:
    """Signiert ad-hoc. Gibt ``(erfolg, meldung)`` zurück.

    Ad-hoc reicht für den lokalen Gebrauch. Entscheidend ist nur, dass
    überhaupt eine Signatur vorhanden ist — sonst identifiziert TCC das
    Bundle über seinen Inhalts-Hash und vergisst die Freigabe bei jedem
    Neubau.
    """
    if sys.platform != "darwin":
        return False, "codesign gibt es nur unter macOS"
    try:
        result = subprocess.run(
            [
                "/usr/bin/codesign",
                "--force",
                "--sign",
                "-",
                "--identifier",
                bundle_id,
                str(app_path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"codesign konnte nicht ausgeführt werden: {exc}"

    if result.returncode != 0:
        return False, (result.stderr or result.stdout).strip()
    return True, "ad-hoc signiert"


def next_steps(app_path: Path) -> str:
    """Anleitung für die Berechtigungen nach dem Bau."""
    return f"""\
Bundle erzeugt: {app_path}

So geht es weiter:

  1. Einmal per Doppelklick oder mit
         open {shlex.quote(str(app_path))}
     starten. In der Menüleiste erscheint 🎙.

  2. macOS fragt nach Bedienungshilfen und Eingabeüberwachung — bestätigen.
     Falls kein Dialog kommt, die App von Hand eintragen:
         Systemeinstellungen → Datenschutz & Sicherheit → Bedienungshilfen
         Systemeinstellungen → Datenschutz & Sicherheit → Eingabeüberwachung
     Dort "+" klicken und {app_path.name} auswählen.

  3. Erst wenn FlowText in beiden Listen steht und läuft: dem Terminal
     (iTerm/Terminal.app) die Rechte wieder wegnehmen. Es braucht sie nicht
     mehr.

  4. Autostart: Systemeinstellungen → Allgemein → Anmeldeobjekte → "+".

Gut zu wissen:
  * Das Bundle zeigt nur auf dein .venv — Codeänderungen wirken sofort,
    ohne Neubau.
  * Nach einem Neubau kann macOS die Freigabe zurücksetzen, weil sich die
    Signatur ändert. Dann Schritt 2 wiederholen.
  * Startprobleme stehen in launcher.log neben dem normalen Logfile.
"""
