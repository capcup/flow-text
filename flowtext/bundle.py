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
        ├── python3       Kopie des Interpreters (~10 kB, ohne Bibliothek)
        ├── pyvenv.cfg    macht diese Kopie zu einer gültigen venv
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
* **Der laufende Interpreter muss INNERHALB des Bundles liegen — als echte
  Datei.** AppKit bestimmt ``[NSBundle mainBundle]`` aus dem Pfad der
  laufenden ausführbaren Datei, und zwar über den *aufgelösten* Pfad.
  Zeigte ``launch.sh`` direkt auf ``…/.venv/bin/python3``, lag der Prozess
  nach dem ``exec`` außerhalb von ``FlowText.app``. Ein Symlink im Bundle
  reicht ebenfalls nicht: ``realpath`` folgt ihm ins ``.venv`` und von dort
  weiter zum Basis-Interpreter, der Prozess landet also wieder außerhalb.
  In beiden Fällen verliert die App ihre Bundle-Identität und
  ``NSStatusBar`` zeichnet **kein Menüleisten-Icon**. Die TCC-Rechte
  überleben das (sie hängen am verantwortlichen Prozess, der beim Start
  feststeht), die AppKit-Identität nicht.

  Deshalb wird der Interpreter **kopiert** und ein ``pyvenv.cfg`` daneben
  gelegt — genau der Aufbau, den ``python -m venv --copies`` erzeugt.
  Damit ist ``Contents/MacOS`` selbst eine gültige virtuelle Umgebung, die
  ihre Standardbibliothek über ``home =`` findet. Die Pakete des ``.venv``
  kommen zusätzlich über ``PYTHONPATH``, weil ``sys.prefix`` jetzt ins
  Bundle zeigt und die dortigen ``site-packages`` nicht existieren.
"""

from __future__ import annotations

import json
import os
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

#: Kopie des Interpreters. Muss eine **echte Datei** im Bundle sein, kein
#: Symlink — sonst zeichnet ``NSStatusBar`` kein Icon (siehe Moduldoku).
PYTHON_NAME = "python3"

#: Alias für ältere Aufrufer. Der Name stammt aus der Symlink-Zeit.
PYTHON_LINK = PYTHON_NAME

#: Macht die Interpreter-Kopie im Bundle zu einer gültigen virtuellen
#: Umgebung. CPython sucht die Datei neben der ausführbaren Datei und eine
#: Ebene darüber; ``home =`` darin verweist auf den Basis-Interpreter und
#: damit auf die Standardbibliothek.
PYVENV_CFG = "pyvenv.cfg"

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
    char *dir = dirname(resolved);

    char project_dir[PATH_MAX];
    snprintf(project_dir, sizeof(project_dir), "%s/../../..", dir);
    chdir(project_dir);

    char bundle_path[PATH_MAX];
    snprintf(bundle_path, sizeof(bundle_path), "%s/../..", dir);

    setenv("PATH", "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin", 1);
    setenv("FLOWTEXT_APP_BUNDLE", bundle_path, 1);
    setenv("PYTHONUNBUFFERED", "1", 1);

    char script[PATH_MAX];
    snprintf(script, sizeof(script), "%s/%s", dir, "LAUNCH_SCRIPT_NAME");
    execl("/bin/zsh", "zsh", script, (char *)NULL);
    return 1;
}
"""


#: Stub **ohne** ``exec``: lädt libpython und führt Python im eigenen
#: Prozess aus. Platzhalter ``@…@`` werden beim Bauen eingesetzt.
#:
#: Ohne Argumente startet er ``-m flowtext --menubar`` und leitet
#: stdout/stderr ins ``launcher.log``. Mit ``--python …`` reicht er die
#: restlichen Argumente unverändert an Python weiter (Selbsttest beim
#: Bauen) und lässt die Ausgabe auf dem Terminal.
_EMBEDDED_STUB_SOURCE = r"""
#include <dlfcn.h>
#include <fcntl.h>
#include <limits.h>
#include <libgen.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include <mach-o/dyld.h>

typedef int (*py_bytes_main_t)(int, char **);

static void prepend_env(const char *name, const char *value) {
    const char *old = getenv(name);
    if (old != NULL && old[0] != '\0') {
        char joined[PATH_MAX * 2];
        snprintf(joined, sizeof(joined), "%s:%s", value, old);
        setenv(name, joined, 1);
    } else {
        setenv(name, value, 1);
    }
}

int main(int argc, char *argv[]) {
    char path[PATH_MAX];
    uint32_t size = sizeof(path);
    if (_NSGetExecutablePath(path, &size) != 0) {
        return 1;
    }
    char self[PATH_MAX];
    if (realpath(path, self) == NULL) {
        return 1;
    }
    char self_copy[PATH_MAX];
    snprintf(self_copy, sizeof(self_copy), "%s", self);
    char *dir = dirname(self_copy);

    char bundle_path[PATH_MAX];
    snprintf(bundle_path, sizeof(bundle_path), "%s/../..", dir);

    int diagnostic = (argc > 1 && strcmp(argv[1], "--python") == 0);

    if (chdir("@PROJECT@") != 0) {
        perror("chdir @PROJECT@");
        return 1;
    }

    setenv("PATH", "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin", 1);
    setenv("FLOWTEXT_APP_BUNDLE", bundle_path, 1);
    setenv("PYTHONUNBUFFERED", "1", 1);
    if (@HAS_SITE_PACKAGES@) {
        prepend_env("PYTHONPATH", "@SITE_PACKAGES@");
    }

    if (!diagnostic) {
        mkdir("@LOG_DIR@", 0755);
        int fd = open("@LOG_DIR@/launcher.log", O_WRONLY | O_CREAT | O_APPEND, 0644);
        if (fd >= 0) {
            dup2(fd, STDOUT_FILENO);
            dup2(fd, STDERR_FILENO);
            if (fd > STDERR_FILENO) {
                close(fd);
            }
        }
    }

    void *lib = dlopen("@LIBPYTHON@", RTLD_NOW | RTLD_GLOBAL);
    if (lib == NULL) {
        fprintf(stderr, "FlowText: libpython nicht ladbar: %s\n", dlerror());
        return 1;
    }
    py_bytes_main_t py_main = (py_bytes_main_t)dlsym(lib, "Py_BytesMain");
    if (py_main == NULL) {
        fprintf(stderr, "FlowText: Py_BytesMain fehlt: %s\n", dlerror());
        return 1;
    }

    /* argv[0] = absoluter Pfad dieser Binary: daran findet Python die
       pyvenv.cfg und damit seine Standardbibliothek. */
    char *py_argv[64];
    int n = 0;
    py_argv[n++] = self;
    if (diagnostic) {
        for (int i = 2; i < argc && n < 63; i++) {
            py_argv[n++] = argv[i];
        }
    } else {
        py_argv[n++] = "-m";
        py_argv[n++] = "flowtext";
        py_argv[n++] = "--menubar";
    }
    py_argv[n] = NULL;
    return py_main(n, py_argv);
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
    #: ``True`` = Python läuft eingebettet im Stub, ohne ``exec`` (macOS 26).
    embedded: bool = False


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

export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
export {BUNDLE_ENV_VAR}={bundle}
export PYTHONUNBUFFERED=1
{pythonpath}
LOGFILE={log_dir}/launcher.log
touch "$LOGFILE" 2>/dev/null || LOGFILE="/dev/null"
exec "$(dirname "$0")/{PYTHON_NAME}" -m flowtext --menubar >> "$LOGFILE" 2>&1
"""


def pyvenv_config(spec: BundleSpec) -> str:
    """Inhalt der ``pyvenv.cfg``, die neben die Interpreter-Kopie kommt.

    Die Datei des ``.venv`` wird unverändert übernommen: ihr ``home =``
    zeigt auf den Basis-Interpreter, über den die Kopie im Bundle ihre
    Standardbibliothek findet. Ohne sie startet die Kopie nicht, weil sie
    an ihrem neuen Ort kein ``lib/pythonX.Y`` neben sich hat.
    """
    existing = spec.python.parent.parent / PYVENV_CFG
    if existing.is_file():
        return existing.read_text(encoding="utf-8")
    # Kein .venv — dann ist der Basis-Interpreter selbst das Zuhause.
    home = spec.python.resolve().parent
    return f"home = {home}\ninclude-system-site-packages = false\n"


def stub_source() -> str:
    """C-Quelltext des Rückfall-Stubs (``exec`` auf ``launch.sh``)."""
    return _STUB_SOURCE.replace("LAUNCH_SCRIPT_NAME", LAUNCH_SCRIPT)


def _c_escape(text: str) -> str:
    """Macht einen Pfad zum Inhalt eines C-Stringliterals."""
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "")


def embedded_stub_source(spec: BundleSpec, libpython: Path) -> str:
    """C-Quelltext des Stubs, der Python einbettet (kein ``exec``)."""
    site = str(spec.site_packages) if spec.site_packages else ""
    replacements = {
        "@PROJECT@": str(spec.project),
        "@LOG_DIR@": str(spec.log_dir),
        "@LIBPYTHON@": str(libpython),
        "@SITE_PACKAGES@": site,
        "@HAS_SITE_PACKAGES@": "1" if site else "0",
    }
    source = _EMBEDDED_STUB_SOURCE
    for key, value in replacements.items():
        source = source.replace(key, value if key == "@HAS_SITE_PACKAGES@" else _c_escape(value))
    return source


def find_libpython(python: Path) -> Path | None:
    """Gemeinsam genutzte ``libpython`` des Interpreters — oder ``None``.

    ``None`` heißt: statischer Build (z. B. pyenv ohne ``--enable-shared``).
    Dann lässt sich Python nicht einbetten und der Rückfall greift.
    """
    code = (
        "import json, sysconfig as s;"
        "g = s.get_config_var;"
        "print(json.dumps({k: g(k) for k in "
        "('Py_ENABLE_SHARED','LIBDIR','LDLIBRARY','INSTSONAME',"
        "'PYTHONFRAMEWORKPREFIX','PYTHONFRAMEWORKINSTALLDIR')}))"
    )
    try:
        result = subprocess.run(
            [str(python), "-c", code], capture_output=True, text=True, timeout=60
        )
        info = json.loads(result.stdout.strip().splitlines()[-1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None
    if result.returncode != 0 or not isinstance(info, dict):
        return None
    return _pick_libpython(info)


def _pick_libpython(info: dict[str, Any]) -> Path | None:
    """Wählt aus den ``sysconfig``-Werten die Bibliothek, die wirklich existiert."""
    if not info.get("Py_ENABLE_SHARED"):
        return None
    candidates: list[Path] = []
    libdir = info.get("LIBDIR")
    framework = info.get("PYTHONFRAMEWORKPREFIX")
    for name in (info.get("INSTSONAME"), info.get("LDLIBRARY")):
        if not name:
            continue
        if libdir:
            candidates.append(Path(libdir) / name)
        if framework:
            candidates.append(Path(framework) / name)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


# ---------------------------------------------------------------------- Bauen


def compile_stub(destination: Path, source: str | None = None) -> tuple[bool, str]:
    """Kompiliert den nativen Einstiegspunkt nach ``destination``.

    Ohne ``source`` entsteht der Rückfall-Stub mit ``exec``.
    """
    if sys.platform != "darwin":
        return False, "nativer Stub braucht macOS"

    arch = host_architecture()
    for compiler in (["/usr/bin/xcrun", "clang"], ["/usr/bin/clang"]):
        try:
            result = subprocess.run(
                [*compiler, "-arch", arch, "-O2", "-o", str(destination), "-x", "c", "-"],
                input=source if source is not None else stub_source(),
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


def verify_embedded(entry: Path) -> tuple[bool, str]:
    """Selbsttest des eingebetteten Stubs: startet er Python und findet alles?"""
    modules = ["flowtext", "pynput", "sounddevice", "soundfile", "numpy", "rumps"]
    code = "import sys, " + ", ".join(modules) + "; print(sys.prefix)"
    try:
        result = subprocess.run(
            [str(entry), "--python", "-c", code],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"nicht ausführbar: {exc}"
    if result.returncode == 0:
        return True, "Python startet eingebettet im Stub"
    detail = (result.stderr or result.stdout).strip().splitlines()
    return False, detail[-1] if detail else f"Exit-Code {result.returncode}"


def _build_entry(spec: BundleSpec, entry: Path) -> tuple[bool, str, bool]:
    """Erzeugt den Einstieg. Gibt ``(nativ, Hinweis, eingebettet)`` zurück.

    Bevorzugt den eingebetteten Stub ohne ``exec``; bei jedem Problem
    (kein Shared-Build, Compilerfehler, Selbsttest) bleibt der alte Weg.
    """
    reason = ""
    libpython = find_libpython(spec.python)
    if libpython is None:
        reason = "Python ist kein Shared-Build (keine libpython) — Rückfall auf exec-Stub"
    else:
        ok, message = compile_stub(entry, embedded_stub_source(spec, libpython))
        if ok:
            ok, message = verify_embedded(entry)
            if ok:
                return True, f"nativer Stub mit eingebettetem Python ({libpython.name})", True
        reason = f"Einbetten gescheitert ({message}) — Rückfall auf exec-Stub"

    native, note = compile_stub(entry)
    return native, f"{note}. {reason}" if native else note, False


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

    # Interpreter ins Bundle holen. Bewusst eine Kopie: einem Symlink folgt
    # realpath() zurück ins .venv, und AppKit sähe den Prozess dann wieder
    # außerhalb des Bundles — ohne Bundle-Identität kein Statusicon.
    # Kopiert wird nur das Startbinary (~10 kB), nicht die Bibliothek.
    interpreter = macos / PYTHON_NAME
    shutil.copy2(spec.python.resolve(), interpreter)
    interpreter.chmod(0o755)
    (macos / PYVENV_CFG).write_text(pyvenv_config(spec), encoding="utf-8")

    script = macos / LAUNCH_SCRIPT
    script.write_text(launcher_script(spec, app_path), encoding="utf-8")
    script.chmod(0o755)

    entry = macos / spec.app_name
    native, note, embedded = _build_entry(spec, entry)
    if not native:
        # Rückfall: Skript direkt als Haupteinstieg. Startet aus dem Terminal,
        # aber Launch Services kann die Architektur nicht bestimmen.
        shutil.copy2(script, entry)
        entry.chmod(0o755)

    return BuildResult(app_path=app_path, native_launcher=native, note=note, embedded=embedded)


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


def verify_bundle(app_path: Path, project: Path, site_packages: Path | None) -> tuple[bool, str]:
    """Startet die Interpreter-Kopie *im Bundle* und prüft ihre Importe.

    Fängt den Preis der Kopie ab: findet das kopierte Startbinary seine
    Standardbibliothek nicht (fehlerhafte ``pyvenv.cfg``) oder verweist es
    relativ auf sein Framework, scheitert es erst beim Doppelklick — und
    dort sieht man nichts.
    """
    if sys.platform != "darwin":
        return False, "nur unter macOS prüfbar"

    interpreter = app_path / "Contents" / "MacOS" / PYTHON_NAME
    if not interpreter.is_file():
        return False, f"Interpreter fehlt im Bundle: {interpreter}"

    env = dict(os.environ)
    if site_packages:
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = f"{site_packages}:{existing}" if existing else str(site_packages)

    modules = ["flowtext", "pynput", "sounddevice", "soundfile", "numpy", "rumps"]
    code = "import " + ", ".join(modules)
    try:
        result = subprocess.run(
            [str(interpreter), "-c", code],
            cwd=project,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"Interpreter im Bundle nicht ausführbar: {exc}"

    if result.returncode == 0:
        return True, "Interpreter im Bundle startet und findet alles"

    detail = (result.stderr or result.stdout).strip().splitlines()
    return False, detail[-1] if detail else "Start im Bundle fehlgeschlagen"


def site_packages_of(python: Path) -> Path | None:
    """site-packages des Interpreters — für ``PYTHONPATH`` im Bundle.

    Im Bundle zeigt ``sys.prefix`` auf ``Contents/MacOS`` (dort liegt die
    ``pyvenv.cfg``), und dortige ``site-packages`` gibt es nicht. Deshalb
    wird der echte Pfad hier einmal beim Bauen ermittelt und fest ins
    Startskript eingetragen.
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
    if result.native_launcher and not result.embedded:
        warning = f"""
ℹ️  Python läuft NICHT eingebettet ({result.note}).
    Auf macOS 26 kann dann das Menüleisten-Icon fehlen. Abhilfe ist ein
    Python mit gemeinsam genutzter Bibliothek, z. B. mit pyenv:
        PYTHON_CONFIGURE_OPTS="--enable-shared" pyenv install 3.12.3
    danach .venv neu anlegen und make_app.py erneut ausführen.
"""
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
    ohne Neubau. Nur nach einem Python-Update oder einem neuen .venv muss
    das Bundle neu erzeugt werden (die Interpreter-Kopie wird dann alt).
  * Nach einem Neubau kann macOS die Freigabe zurücksetzen, weil sich die
    Signatur ändert. Dann Schritt 2 wiederholen.
  * Startprobleme stehen in launcher.log neben dem normalen Logfile.
"""
