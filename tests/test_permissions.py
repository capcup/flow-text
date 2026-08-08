from __future__ import annotations

from flowtext.permissions import HIDAccess, Permissions, environment_report


def test_denied_input_monitoring_is_not_ok() -> None:
    assert not Permissions(True, HIDAccess.DENIED).ok


def test_missing_accessibility_is_not_ok() -> None:
    assert not Permissions(False, HIDAccess.GRANTED).ok


def test_unknown_is_acceptable() -> None:
    """UNKNOWN heißt 'noch nie gefragt' — macOS zeigt dann den Dialog."""
    assert Permissions(True, HIDAccess.UNKNOWN).ok


def test_granted_is_ok() -> None:
    assert Permissions(True, HIDAccess.GRANTED).ok


def test_summary_is_readable() -> None:
    summary = Permissions(False, HIDAccess.DENIED).summary()
    assert "FEHLT" in summary
    assert "verweigert" in summary


def test_environment_report_mentions_interpreter() -> None:
    report = environment_report()
    assert "python=" in report
    assert "executable=" in report


class TestResponsibleApp:
    def test_process_chain_starts_with_this_process(self) -> None:
        import os

        from flowtext.permissions import process_chain

        chain = process_chain()
        assert chain, "Prozesskette darf nicht leer sein"
        assert chain[0][0] == os.getpid()

    def test_responsible_app_extracts_bundle_path(self, monkeypatch) -> None:
        """Genau dieser Pfad muss in die TCC-Listen eingetragen werden."""
        from flowtext import permissions as perms

        monkeypatch.setattr(
            perms,
            "process_chain",
            lambda: [
                (100, "/Users/x/.pyenv/versions/3.12.3/bin/python3 -m flowtext"),
                (90, "-zsh"),
                (80, "/System/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal"),
                (1, "/sbin/launchd"),
            ],
        )
        assert perms.responsible_app() == "/System/Applications/Utilities/Terminal.app"

    def test_responsible_app_none_without_bundle(self, monkeypatch) -> None:
        from flowtext import permissions as perms

        monkeypatch.setattr(perms, "process_chain", lambda: [(1, "/sbin/launchd")])
        assert perms.responsible_app() is None

    def test_help_text_names_the_app(self, monkeypatch) -> None:
        from flowtext import permissions as perms

        monkeypatch.setattr(perms.sys, "platform", "darwin")
        monkeypatch.setattr(perms, "responsible_app", lambda: "/Applications/iTerm.app")
        text = perms.help_text()
        assert "/Applications/iTerm.app" in text
        assert "tccutil reset ListenEvent" in text
