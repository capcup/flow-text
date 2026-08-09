"""Tests des ``FlowText.app``-Launcher-Bundles.

Der Bau selbst ist plattformunabhängig (Verzeichnisse, plist, Shellskript) —
nur ``codesign`` braucht macOS. Deshalb lässt sich hier alles prüfen außer
der Signatur.
"""

from __future__ import annotations

import plistlib
import stat
from pathlib import Path

import pytest

from flowtext.bundle import (
    BUNDLE_ENV_VAR,
    DEFAULT_BUNDLE_ID,
    LAUNCH_SCRIPT,
    PYTHON_NAME,
    PYVENV_CFG,
    BundleSpec,
    build,
    codesign,
    host_architecture,
    info_plist,
    launcher_script,
    next_steps,
    pyvenv_config,
    stub_source,
    verify_bundle,
    verify_interpreter,
)


@pytest.fixture
def spec(tmp_path: Path) -> BundleSpec:
    """Ein ``.venv``-Abbild. ``build()`` kopiert den Interpreter, deshalb
    muss die Datei wirklich existieren."""
    venv = tmp_path / ".venv"
    (venv / "bin").mkdir(parents=True)
    python = venv / "bin" / "python3"
    python.write_text("#!/bin/sh\n", encoding="utf-8")
    python.chmod(0o755)
    (venv / PYVENV_CFG).write_text("home = /opt/base/bin\nversion = 3.12.3\n", encoding="utf-8")
    return BundleSpec(
        python=python,
        project=tmp_path / "flow-text",
        log_dir=tmp_path / "Logs" / "FlowText",
    )


# ------------------------------------------------------------------ Info.plist


class TestInfoPlist:
    def test_microphone_reason_is_present(self, spec: BundleSpec) -> None:
        """Ohne diesen Schlüssel beendet macOS die App beim Mikrofonzugriff."""
        assert info_plist(spec)["NSMicrophoneUsageDescription"]

    def test_is_a_background_app(self, spec: BundleSpec) -> None:
        """LSUIElement: Menüleisten-App ohne Dock-Icon."""
        assert info_plist(spec)["LSUIElement"] is True

    def test_bundle_id_is_stable_by_default(self, spec: BundleSpec) -> None:
        """Der TCC-Eintrag hängt daran — er darf sich nicht zufällig ändern."""
        assert info_plist(spec)["CFBundleIdentifier"] == DEFAULT_BUNDLE_ID

    def test_executable_matches_the_app_name(self, spec: BundleSpec) -> None:
        plist = info_plist(spec)
        assert plist["CFBundleExecutable"] == plist["CFBundleName"]

    def test_custom_bundle_id(self, tmp_path: Path) -> None:
        spec = BundleSpec(
            python=tmp_path / "p",
            project=tmp_path,
            log_dir=tmp_path,
            bundle_id="com.example.test",
        )
        assert info_plist(spec)["CFBundleIdentifier"] == "com.example.test"


# --------------------------------------------------------------------- Skript


class TestLauncherScript:
    def test_uses_exec(self, spec: BundleSpec, tmp_path: Path) -> None:
        """exec macht Python zum Prozess des Bundles — sonst erbt er die
        Berechtigungen nicht zuverlässig."""
        script = launcher_script(spec, tmp_path / "FlowText.app")
        assert "\nexec " in script

    def test_starts_the_menubar_variant(self, spec: BundleSpec, tmp_path: Path) -> None:
        assert "-m flowtext --menubar" in launcher_script(spec, tmp_path / "FlowText.app")

    def test_changes_into_the_project(self, spec: BundleSpec, tmp_path: Path) -> None:
        """Sonst findet candidate_paths() die config.toml nicht."""
        assert f"cd {spec.project}" in launcher_script(spec, tmp_path / "FlowText.app")

    def test_exports_the_bundle_path(self, spec: BundleSpec, tmp_path: Path) -> None:
        app = tmp_path / "FlowText.app"
        assert f"export {BUNDLE_ENV_VAR}={app}" in launcher_script(spec, app)

    def test_redirects_output_to_a_log(self, spec: BundleSpec, tmp_path: Path) -> None:
        """Aus dem Finder gestartet gäbe es sonst keinerlei Ausgabe."""
        assert "launcher.log" in launcher_script(spec, tmp_path / "FlowText.app")

    def test_interpreter_is_started_from_inside_the_bundle(
        self, spec: BundleSpec, tmp_path: Path
    ) -> None:
        """Regression: kein Menüleisten-Icon.

        AppKit bestimmt die Bundle-Identität aus dem Pfad der laufenden
        ausführbaren Datei. Zeigte launch.sh direkt auf das .venv, lag der
        Prozess nach dem exec außerhalb der .app — NSStatusBar zeichnete
        dann nichts.
        """
        script = launcher_script(spec, tmp_path / "FlowText.app")
        assert f'"$(dirname "$0")/{PYTHON_NAME}"' in script
        assert str(spec.python) not in script

    def test_pythonpath_carries_the_dependencies(self, tmp_path: Path) -> None:
        """Über den Symlink erkennt Python das .venv nicht mehr selbst."""
        spec = BundleSpec(
            python=tmp_path / ".venv" / "bin" / "python3",
            project=tmp_path,
            log_dir=tmp_path,
            site_packages=tmp_path / ".venv" / "lib" / "python3.12" / "site-packages",
        )
        script = launcher_script(spec, tmp_path / "FlowText.app")
        assert "export PYTHONPATH=" in script
        assert "site-packages" in script

    def test_without_site_packages_no_pythonpath(self, spec: BundleSpec, tmp_path: Path) -> None:
        assert "PYTHONPATH" not in launcher_script(spec, tmp_path / "FlowText.app")

    def test_paths_with_spaces_are_quoted(self, tmp_path: Path) -> None:
        spec = BundleSpec(
            python=tmp_path / "mein venv" / "python3",
            project=tmp_path / "flow text",
            log_dir=tmp_path / "log ordner",
        )
        script = launcher_script(spec, tmp_path / "Flow Text.app")
        assert "'" in script
        assert "cd '" in script


# ----------------------------------------------------------------------- Bau


class TestBuild:
    def test_creates_the_bundle_layout(self, spec: BundleSpec, tmp_path: Path) -> None:
        app = build(spec, tmp_path / "dist").app_path
        assert app.name == "FlowText.app"
        assert (app / "Contents" / "Info.plist").is_file()
        assert (app / "Contents" / "MacOS" / "FlowText").is_file()
        assert (app / "Contents" / "MacOS" / LAUNCH_SCRIPT).is_file()

    def test_entry_point_is_executable(self, spec: BundleSpec, tmp_path: Path) -> None:
        app = build(spec, tmp_path / "dist").app_path
        for name in ("FlowText", LAUNCH_SCRIPT):
            assert (app / "Contents" / "MacOS" / name).stat().st_mode & stat.S_IXUSR

    def test_plist_is_valid(self, spec: BundleSpec, tmp_path: Path) -> None:
        app = build(spec, tmp_path / "dist").app_path
        with (app / "Contents" / "Info.plist").open("rb") as fh:
            plist = plistlib.load(fh)
        assert plist["CFBundleIdentifier"] == DEFAULT_BUNDLE_ID
        assert plist["LSUIElement"] is True

    def test_rebuild_replaces_the_old_bundle(self, spec: BundleSpec, tmp_path: Path) -> None:
        app = build(spec, tmp_path / "dist").app_path
        stray = app / "Contents" / "leftover.txt"
        stray.write_text("alt", encoding="utf-8")
        build(spec, tmp_path / "dist")
        assert not stray.exists()

    def test_bundle_stays_small(self, spec: BundleSpec, tmp_path: Path) -> None:
        """Der Kern der Idee: nur das Startbinary, keine Bibliothek."""
        app = build(spec, tmp_path / "dist").app_path
        files = sorted(p.name for p in app.rglob("*") if p.is_file())
        assert files == sorted(["Info.plist", "FlowText", LAUNCH_SCRIPT, PYTHON_NAME, PYVENV_CFG])

    def test_interpreter_is_a_real_file_not_a_symlink(
        self, spec: BundleSpec, tmp_path: Path
    ) -> None:
        """Regression: kein Menüleisten-Icon.

        Einem Symlink folgt ``realpath`` zurück ins ``.venv`` und weiter zum
        Basis-Interpreter. AppKit sähe den Prozess dann außerhalb der ``.app``
        und ``NSStatusBar`` zeichnete nichts. Nur eine echte Datei im Bundle
        hält die Bundle-Identität.
        """
        app = build(spec, tmp_path / "dist").app_path
        interpreter = app / "Contents" / "MacOS" / PYTHON_NAME
        assert interpreter.is_file()
        assert not interpreter.is_symlink()
        assert interpreter.read_bytes() == spec.python.read_bytes()

    def test_pyvenv_cfg_travels_with_the_interpreter(
        self, spec: BundleSpec, tmp_path: Path
    ) -> None:
        """Ohne sie findet die Kopie ihre Standardbibliothek nicht."""
        app = build(spec, tmp_path / "dist").app_path
        cfg = app / "Contents" / "MacOS" / PYVENV_CFG
        assert "home = /opt/base/bin" in cfg.read_text(encoding="utf-8")

    def test_pyvenv_cfg_is_synthesised_without_a_venv(self, tmp_path: Path) -> None:
        """Ein Interpreter außerhalb eines ``.venv`` ist sein eigenes Zuhause."""
        base = tmp_path / "bin"
        base.mkdir()
        python = base / "python3"
        python.write_text("#!/bin/sh\n", encoding="utf-8")
        spec = BundleSpec(python=python, project=tmp_path, log_dir=tmp_path)
        assert f"home = {base}" in pyvenv_config(spec)

    def test_falls_back_to_the_script_without_a_compiler(
        self, spec: BundleSpec, tmp_path: Path, monkeypatch
    ) -> None:
        from flowtext import bundle

        monkeypatch.setattr(bundle, "compile_stub", lambda dest: (False, "kein clang"))
        result = build(spec, tmp_path / "dist")
        assert result.native_launcher is False
        entry = result.app_path / "Contents" / "MacOS" / "FlowText"
        assert entry.read_text(encoding="utf-8").startswith("#!/bin/zsh")


# ---------------------------------------------------------------- Signatur/Doku


class TestVerifyBundle:
    """Die Kopie kann kaputt sein, ohne dass man es beim Bauen merkt."""

    def test_refuses_outside_macos(self, monkeypatch, tmp_path: Path) -> None:
        from flowtext import bundle

        monkeypatch.setattr(bundle.sys, "platform", "linux")
        ok, message = verify_bundle(tmp_path / "FlowText.app", tmp_path, None)
        assert ok is False
        assert "macOS" in message

    def test_reports_a_missing_interpreter(self, monkeypatch, tmp_path: Path) -> None:
        from flowtext import bundle

        monkeypatch.setattr(bundle.sys, "platform", "darwin")
        ok, message = verify_bundle(tmp_path / "FlowText.app", tmp_path, None)
        assert ok is False
        assert "fehlt" in message


def test_codesign_refuses_outside_macos(monkeypatch, tmp_path: Path) -> None:
    from flowtext import bundle

    monkeypatch.setattr(bundle.sys, "platform", "linux")
    ok, message = codesign(tmp_path / "FlowText.app")
    assert ok is False
    assert "macOS" in message


def test_next_steps_mentions_both_permissions(spec: BundleSpec, tmp_path: Path) -> None:
    text = next_steps(build(spec, tmp_path / "dist"))
    assert "Bedienungshilfen" in text
    assert "Eingabeüberwachung" in text
    assert "Terminal" in text


def test_next_steps_warns_without_a_native_entry_point(
    spec: BundleSpec, tmp_path: Path, monkeypatch
) -> None:
    """Sonst rätselt man über Fehler -10669."""
    from flowtext import bundle

    monkeypatch.setattr(bundle, "compile_stub", lambda dest: (False, "kein clang"))
    text = next_steps(build(spec, tmp_path / "dist"))
    assert "-10669" in text
    assert "xcode-select --install" in text


# --------------------------------------------- Regression: Architektur/Rosetta


class TestNativeEntryPoint:
    """Regression zu Fehler -10669.

    Ist der Haupteinstieg ein Shellskript, kann Launch Services die
    CPU-Architektur nicht ablesen, nimmt x86_64 an und verlangt Rosetta.
    Deshalb muss dort ein echtes Mach-O-Binary stehen.
    """

    def test_stub_execs_the_launch_script(self) -> None:
        source = stub_source()
        assert LAUNCH_SCRIPT in source
        assert "execl" in source
        assert "_NSGetExecutablePath" in source

    def test_stub_has_no_placeholder_left(self) -> None:
        assert "LAUNCH_SCRIPT_NAME" not in stub_source()

    def test_host_architecture_is_supported(self) -> None:
        assert host_architecture() in {"arm64", "x86_64"}

    def test_plist_declares_the_architecture(self, spec: BundleSpec) -> None:
        assert info_plist(spec)["LSArchitecturePriority"] == [host_architecture()]

    def test_compile_refuses_outside_macos(self, monkeypatch, tmp_path: Path) -> None:
        from flowtext import bundle

        monkeypatch.setattr(bundle.sys, "platform", "linux")
        ok, message = bundle.compile_stub(tmp_path / "stub")
        assert ok is False
        assert "macOS" in message


# ------------------------------------- Regression: venv darf nicht aufgelöst werden


class TestInterpreterSelection:
    """Regression: ``.venv/bin/python3`` ist ein Symlink auf den
    Basis-Interpreter. Wird er aufgelöst, zeigt das Bundle am ``.venv``
    vorbei und findet keine einzige Abhängigkeit."""

    def test_venv_symlink_is_not_resolved(self, tmp_path: Path, monkeypatch) -> None:
        import sys as _sys

        _sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
        from make_app import find_python

        base = tmp_path / "pyenv" / "bin"
        base.mkdir(parents=True)
        real = base / "python3.12"
        real.write_text("#!/bin/sh\n", encoding="utf-8")

        venv_bin = tmp_path / "project" / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        link = venv_bin / "python3"
        link.symlink_to(real)

        chosen = find_python(tmp_path / "project")
        assert chosen == link
        assert chosen != real
        assert ".venv" in str(chosen)

    def test_falls_back_to_the_running_interpreter(self, tmp_path: Path) -> None:
        import sys as _sys

        _sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
        from make_app import find_python

        assert find_python(tmp_path / "leer") == Path(_sys.executable)


class TestVerifyInterpreter:
    """Diese Prüfung hätte den venv-Fehler sofort aufgedeckt."""

    def test_missing_interpreter(self, tmp_path: Path) -> None:
        ok, message = verify_interpreter(tmp_path / "gibt-es-nicht", tmp_path)
        assert ok is False
        assert "nicht gefunden" in message

    def test_interpreter_without_dependencies(self, tmp_path: Path) -> None:
        import sys as _sys

        ok, message = verify_interpreter(Path(_sys.executable), tmp_path)
        assert ok is False
        assert "rumps" in message or "ModuleNotFoundError" in message


# ------------------------------------------------------- Erkennung zur Laufzeit


def test_responsible_app_prefers_the_own_bundle(monkeypatch) -> None:
    """Regression: exec verdeckt das Bundle in der Prozesskette."""
    from flowtext import permissions

    monkeypatch.setenv(BUNDLE_ENV_VAR, "/Applications/FlowText.app")
    monkeypatch.setattr(
        permissions,
        "process_chain",
        lambda: [(1, "/System/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal")],
    )
    assert permissions.responsible_app() == "/Applications/FlowText.app"


def test_responsible_app_falls_back_to_the_chain(monkeypatch) -> None:
    from flowtext import permissions

    monkeypatch.delenv(BUNDLE_ENV_VAR, raising=False)
    monkeypatch.setattr(
        permissions,
        "process_chain",
        lambda: [(1, "/Applications/iTerm.app/Contents/MacOS/iTerm2")],
    )
    assert permissions.responsible_app() == "/Applications/iTerm.app"
