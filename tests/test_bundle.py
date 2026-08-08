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
    BundleSpec,
    build,
    codesign,
    info_plist,
    launcher_script,
    next_steps,
)


@pytest.fixture
def spec(tmp_path: Path) -> BundleSpec:
    return BundleSpec(
        python=tmp_path / ".venv" / "bin" / "python3",
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
        app = build(spec, tmp_path / "dist")
        assert app.name == "FlowText.app"
        assert (app / "Contents" / "Info.plist").is_file()
        assert (app / "Contents" / "MacOS" / "FlowText").is_file()

    def test_launcher_is_executable(self, spec: BundleSpec, tmp_path: Path) -> None:
        app = build(spec, tmp_path / "dist")
        mode = (app / "Contents" / "MacOS" / "FlowText").stat().st_mode
        assert mode & stat.S_IXUSR

    def test_plist_is_valid(self, spec: BundleSpec, tmp_path: Path) -> None:
        app = build(spec, tmp_path / "dist")
        with (app / "Contents" / "Info.plist").open("rb") as fh:
            plist = plistlib.load(fh)
        assert plist["CFBundleIdentifier"] == DEFAULT_BUNDLE_ID
        assert plist["LSUIElement"] is True

    def test_rebuild_replaces_the_old_bundle(self, spec: BundleSpec, tmp_path: Path) -> None:
        app = build(spec, tmp_path / "dist")
        stray = app / "Contents" / "leftover.txt"
        stray.write_text("alt", encoding="utf-8")
        build(spec, tmp_path / "dist")
        assert not stray.exists()

    def test_bundle_does_not_copy_the_venv(self, spec: BundleSpec, tmp_path: Path) -> None:
        """Der Kern der Idee: nichts einpacken, nur darauf zeigen."""
        app = build(spec, tmp_path / "dist")
        files = [p for p in app.rglob("*") if p.is_file()]
        assert len(files) == 2  # Info.plist + Launcher


# ---------------------------------------------------------------- Signatur/Doku


def test_codesign_refuses_outside_macos(monkeypatch, tmp_path: Path) -> None:
    from flowtext import bundle

    monkeypatch.setattr(bundle.sys, "platform", "linux")
    ok, message = codesign(tmp_path / "FlowText.app")
    assert ok is False
    assert "macOS" in message


def test_next_steps_mentions_both_permissions(tmp_path: Path) -> None:
    text = next_steps(tmp_path / "FlowText.app")
    assert "Bedienungshilfen" in text
    assert "Eingabeüberwachung" in text
    assert "Terminal" in text


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
