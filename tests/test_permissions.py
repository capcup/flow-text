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
