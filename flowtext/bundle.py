"""Erzeugt ein ``FlowText.app``-Launcher-Bundle.

**Zweck: eine eigene TCC-Identität.** macOS vergibt Bedienungshilfen und
Eingabeüberwachung an den *verantwortlichen Prozess*. Startet FlowText aus
dem Terminal, ist das iTerm oder Terminal.app — die Freigabe gilt dann jeder
Anwendung, die du dort startest. Startet FlowText dagegen aus einem
App-Bundle, taucht **FlowText selbst** in der Liste auf und das Terminal
braucht gar keine Rechte mehr.

**Warum kein py2app.** Es wird nichts eingepackt. Das Bundle zeigt lediglich
auf das vorhandene ``.venv``:

    FlowText.app/Contents/
    ├── Info.plist        Bundle-ID, LSUIElement, Mikrofon-Begründung
    └── MacOS/
        ├── FlowText      nativer arm64-Stub (CFBundleExecutable)
        ├── python3       Symlink auf den Interpreter des .venv
        └── launch.sh     zsh-Skript, das ./python3 -m flowtext --menubar startet

Das ``.venv`` bleibt, wo es ist; die 2 GB Modelldaten werden nicht kopiert.
Aktualisierst du den Code, ist das Bundle sofort aktuell.

**Vier Details, an denen es sonst scheitert:**

* ``NSMicrophoneUsageDescription`` ist Pflicht. Fehlt der Schlüssel, beendet
  macOS die App beim ersten Mikrofonzugriff kommentarlos.
* ``LSUIElement`` verhindert das Dock-Icon — bei einer Menüleisten-App
  unerwünscht.
* Das Bundle muss signiert sein, und sei es nur ad-hoc. Ohne Signatur hängt
  der TCC-Eintrag am Inhalts-Hash und geht bei jedem Neubau kaputt.
* **Der Haupteinstieg muss ein Mach-O-Binary sein, kein Shellskript.** Aus
  einer Textdatei kann Launch Services die CPU-Architektur nicht ablesen und
  nimmt x86_64 an. Auf Apple Silicon ohne Rosetta scheitert der Start dann
  mit ``-10669``. Deshalb wird ein winziger nativer Stub kompiliert, der nur
  das danebenliegende ``launch.sh`` ausführt.
* **Der laufende Interpreter muss INNERHALB des Bundles liegen.** AppKit
  bestimmt ``[NSBundle mainBundle]`` aus dem Pfad der laufenden ausführbaren
  Datei. Zeigte ``launch.sh`` direkt auf ``…/.venv/bin/python3``, lag der
  Prozess nach dem ``exec`` außerhalb von ``FlowText.app`` — die App verlor
  ihre Bundle-Identität und ``NSStatusBar`` zeichnete **kein Menüleisten-
  Icon**. Die TCC-Rechte überlebten das (sie hängen am verantwortlichen
  Prozess, der beim Start feststeht), die AppKit-Identität nicht.
  Deshalb liegt im Bundle ein Symlink ``python3``; die Abhängigkeiten des
  ``.venv`` kommen über ``PYTHONPATH``, weil die venv-Erkennung von Python
  am Symlink-Pfad im Bundle nicht mehr greift.
"""

from __future__ import annotations

import platform
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

#: Name des Shellskripts neben dem Stub.
LAUNCH_SCRIPT = "launch.sh"

#: Symlink auf den Interpreter — muss im Bundle liegen, sonst kein Statusicon.
PYTHON_LINK = "python3"

#: Der Launcher setzt diese Variable, damit die Diagnose das Bundle erkennt.
#: Nötig, weil ``exec`` die Shell ersetzt: in der Prozesskette steht danach
#: nur noch der Python-Pfad, das ``.app`` ist nicht mehr zu sehen.
BUNDLE_ENV_VAR = "FLOWTEXT_APP_BUNDLE"

_MIC_REASON = "FlowText nimmt dein Diktat auf und transkribiert es lokal auf diesem Mac."

#: Nativer Einstiegspunkt. Ermittelt den eigenen Pfad und startet das
#: Skript daneben — mehr braucht es nicht. Entscheidend ist allein, dass
#: hier ein echtes Mach-O-Binary mit Architekturangabe steht.
_STUB_SOURCE = r"""
#include <limits.h>
#include <libgen.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <mach-o/dyld.h>

int main(int argc, char *argv[]) {
    char path[PATH_MAX];
    uint32_t size = sizeof(path);
    if (_NSGetExecutablePath(path, &size) != 0) {
        return 1;
    }
    char resolved[PATH_MAX];
    if (realpath(path, resolved) == NULL) {
        return 1;
    }
    char script[PATH_MAX];
    snprintf(script, sizeof(script), "%s/%s", dirname(resolved), "LAUNCH_SCRIPT_NAME");
    execl("/bin/zsh", "zsh", script, (char *)NULL);
    return 1;
}
"""


@dataclass(frozen=True)
class BundleSpec:
    """Alles, was das Bundle über seine Umgebung wissen muss."""

    python: Path
    project: Path
    log_dir: Path
    #: site-packages des ``.venv``. Nötig, weil Python die virtuelle Umgebung
    #: über den Symlink im Bundle nicht mehr selbst erkennt.
    site_packages: Path | None = None
    bundle_id: str = DEFAULT_BUNDLE_ID
    app_name: str = APP_NAME
    version: str = __version__


@dataclass(frozen=True)
class BuildResult:
    app_path: Path
    #: ``False`` = Rückfall auf das Skript als Haupteinstieg. Dann kann
    #: macOS die Architektur nicht bestimmen und verlangt womöglich Rosetta.
    native_launcher: bool
    note: str


# --------------------------------------------------------------------- Inhalte


def host_architecture() -> str:
    """``arm64`` oder ``x86_64`` — für ``-arch`` und ``LSArchitecturePriority``."""
    machine = platform.machine()
    return "arm64" if machine in {"arm64", "aarch64"} else "x86_64"


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
        # Zusätzlicher Hinweis für Launch Services. Der native Stub sollte
        # genügen; das hier hilft, falls der Rückfall auf das Skript greift.
        "LSArchitecturePriority": [host_architecture()],
    }


def launcher_script(spec: BundleSpec, app_path: Path) -> str:
    """Startskript des Bundles.

    ``exec`` ist wichtig: dadurch *wird* der Python-Prozess der Prozess des
    Bundles, statt ein Kindprozess einer Shell zu sein. Nur so erbt er die
    Berechtigungen des Bundles zuverlässig.
    """
    project = shlex.quote(str(spec.project))
    log_dir = shlex.quote(str(spec.log_dir))
    bundle = shlex.quote(str(app_path))
    pythonpath = (
        f"export PYTHONPATH={shlex.quote(str(spec.site_packages))}${{PYTHONPATH:+:$PYTHONPATH}}\n"
        if spec.site_packages
        else ""
    )
    return f"""#!/bin/zsh
# FlowText-Launcher — erzeugt von tools/make_app.py. Nicht von Hand ändern;
# bei Änderungen am Projektpfad das Bundle neu erzeugen.

# In das Projekt wechseln, damit config.toml gefunden wird.
cd {project} || exit 1

mkdir -p {log_dir}

# Damit die Diagnose weiß, dass wir aus dem Bundle kommen (exec verdeckt das).
export {BUNDLE_ENV_VAR}={bundle}
export PYTHONUNBUFFERED=1
{pythonpath}
# WICHTIG: der Interpreter wird über den Symlink IM Bundle gestartet.
# Zeigte dieser Pfad nach draußen, läge der Prozess außerhalb von
# FlowText.app — AppKit verlöre die Bundle-Identität und es gäbe kein
# Menüleisten-Icon.
exec "$(dirname "$0")/{PYTHON_LINK}" -m flowtext --menubar >> {log_dir}/launcher.log 2>&1
"""


def stub_source() -> str:
    """C-Quelltext des nativen Einstiegspunkts."""
    return _STUB_SOURCE.replace("LAUNCH_SCRIPT_NAME", LAUNCH_SCRIPT)


# ---------------------------------------------------------------------- Bauen


def compile_stub(destination: Path) -> tuple[bool, str]:
    """Kompiliert den nativen Einstiegspunkt nach ``destination``."""
    if sys.platform != "darwin":
        return False, "nativer Stub braucht macOS"

    arch = host_architecture()
    for compiler in (["/usr/bin/xcrun", "clang"], ["/usr/bin/clang"]):
        try:
            result = subprocess.run(
                [*compiler, "-arch", arch, "-O2", "-o", str(destination), "-x", "c", "-"],
                input=stub_source(),
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode == 0:
            return True, f"nativer {arch}-Stub kompiliert"
        last_error = (result.stderr or result.stdout).strip().splitlines()
        return False, last_error[-1] if last_error else "clang meldete einen Fehler"
    return False, "clang nicht gefunden — Command Line Tools installiert? (xcode-select --install)"


def build(spec: BundleSpec, dest_dir: Path) -> BuildResult:
    """Legt ``<dest_dir>/<app_name>.app`` an."""
    app_path = dest_dir / f"{spec.app_name}.app"
    contents = app_path / "Contents"
    macos = contents / "MacOS"

    if app_path.exists():
        shutil.rmtree(app_path)
    macos.mkdir(parents=True)

    with (contents / "Info.plist").open("wb") as fh:
        plistlib.dump(info_plist(spec), fh)

    # Interpreter ins Bundle holen — nur ein Symlink, nichts wird kopiert.
    (macos / PYTHON_LINK).symlink_to(spec.python)

    script = macos / LAUNCH_SCRIPT
    script.write_text(launcher_script(spec, app_path), encoding="utf-8")
    script.chmod(0o755)

    entry = macos / spec.app_name
    native, note = compile_stub(entry)
    if not native:
        # Rückfall: Skript direkt als Haupteinstieg. Startet aus dem Terminal,
        # aber Launch Services kann die Architektur nicht bestimmen.
        shutil.copy2(script, entry)
        entry.chmod(0o755)

    return BuildResult(app_path=app_path, native_launcher=native, note=note)


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
                "--deep",
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


# ----------------------------------------------------------------- Prüfungen


def verify_interpreter(python: Path, project: Path) -> tuple[bool, str]:
    """Kann dieser Interpreter FlowText überhaupt starten?

    Fängt den häufigsten Fehlgriff ab: einen Interpreter *außerhalb* des
    ``.venv``. Der findet die Abhängigkeiten nicht, und die App würde
    kommentarlos scheitern.
    """
    if not python.is_file():
        return False, f"Interpreter nicht gefunden: {python}"

    modules = ["flowtext", "pynput", "sounddevice", "soundfile", "numpy", "rumps"]
    code = "import " + ", ".join(modules)
    try:
        result = subprocess.run(
            [str(python), "-c", code],
            cwd=project,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"Interpreter nicht ausführbar: {exc}"

    if result.returncode == 0:
        return True, "alle Abhängigkeiten importierbar"

    detail = (result.stderr or result.stdout).strip().splitlines()
    return False, detail[-1] if detail else "Import fehlgeschlagen"


def site_packages_of(python: Path) -> Path | None:
    """site-packages des Interpreters — für ``PYTHONPATH`` im Bundle.

    Python erkennt die virtuelle Umgebung nicht mehr, wenn es über den
    Symlink im Bundle gestartet wird: es sucht ``pyvenv.cfg`` neben der
    ausführbaren Datei, und die liegt dann in ``Contents/MacOS``. Deshalb
    wird der Pfad hier einmal beim Bauen ermittelt und fest eingetragen.
    """
    code = "import sysconfig; print(sysconfig.get_paths()['purelib'])"
    try:
        result = subprocess.run(
            [str(python), "-c", code],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    path = Path(result.stdout.strip())
    return path if path.is_dir() else None


def next_steps(result: BuildResult) -> str:
    """Anleitung für die Berechtigungen nach dem Bau."""
    warning = ""
    if not result.native_launcher:
        warning = f"""
⚠️  Kein nativer Einstiegspunkt ({result.note}).
    macOS kann die Architektur dann nicht bestimmen und verlangt beim
    Start womöglich Rosetta (Fehler -10669). Abhilfe:
        xcode-select --install     # danach make_app.py erneut ausführen
    oder ersatzweise:
        softwareupdate --install-rosetta --agree-to-license
"""
    return f"""\
Bundle erzeugt: {result.app_path}
{warning}
So geht es weiter:

  1. Einmal per Doppelklick oder mit
         open {shlex.quote(str(result.app_path))}
     starten. In der Menüleiste erscheint 🎙.

  2. macOS fragt nach Bedienungshilfen und Eingabeüberwachung — bestätigen.
     Falls kein Dialog kommt, die App von Hand eintragen:
         Systemeinstellungen → Datenschutz & Sicherheit → Bedienungshilfen
         Systemeinstellungen → Datenschutz & Sicherheit → Eingabeüberwachung
     Dort "+" klicken und {result.app_path.name} auswählen.

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
