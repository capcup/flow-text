"""Zwischenablage: Sicherung aller Datentypen und sichere Wiederherstellung."""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from flowtext.pipeline import inject_macos
from flowtext.pipeline.inject_macos import ClipboardInjector, PyperclipPasteboard


class FakeBoard:
    """Zwischenablage mit Änderungszähler; Inhalt ist ein beliebiges Objekt."""

    def __init__(self, content: Any = "vorher") -> None:
        self.content = content
        self.count = 1
        self.transient: list[bool] = []

    def snapshot(self) -> Any:
        return self.content

    def write_text(self, text: str, *, transient: bool) -> None:
        self.content = text
        self.count += 1
        self.transient.append(transient)

    def change_count(self) -> int:
        return self.count

    def restore(self, snapshot: Any) -> None:
        self.content = snapshot
        self.count += 1

    def user_copies(self, content: Any) -> None:
        self.content = content
        self.count += 1


@pytest.fixture(autouse=True)
def _no_real_paste(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ClipboardInjector, "_press_paste_shortcut", lambda self: None)
    monkeypatch.setattr(inject_macos, "_PASTE_DELAY", 0.0)


def _wait(injector: ClipboardInjector) -> None:
    timer = injector._timer
    assert timer is not None
    timer.join(timeout=2.0)


def test_non_text_content_survives_a_dictation() -> None:
    """Regression B1: ein Bild wurde durch einen leeren String ersetzt."""
    image = {"public.png": b"\x89PNG..."}
    board = FakeBoard(image)
    injector = ClipboardInjector(restore_after=0.01, pasteboard=board)
    injector.inject("diktat")
    assert board.content == "diktat"
    _wait(injector)
    assert board.content == image


def test_user_copy_during_the_delay_is_not_overwritten() -> None:
    board = FakeBoard("vorher")
    injector = ClipboardInjector(restore_after=0.05, pasteboard=board)
    injector.inject("diktat")
    board.user_copies("neu kopiert")
    _wait(injector)
    assert board.content == "neu kopiert"


def test_back_to_back_dictations_keep_the_original() -> None:
    """Das zweite Diktat darf nicht das erste Transkript als 'vorher' sichern."""
    board = FakeBoard("original")
    injector = ClipboardInjector(restore_after=5.0, pasteboard=board)
    injector.inject("erstes")
    injector._restore_after = 0.01
    injector.inject("zweites")
    _wait(injector)
    assert board.content == "original"


def test_transcript_is_transient_only_when_it_will_be_removed() -> None:
    board = FakeBoard()
    ClipboardInjector(restore_after=0.01, pasteboard=board).inject("a")
    ClipboardInjector(restore_after=0, pasteboard=board).inject("b")
    ClipboardInjector(paste=False, pasteboard=board).inject("c")
    assert board.transient == [True, False, False]


def test_copy_only_mode_leaves_the_transcript() -> None:
    board = FakeBoard("vorher")
    injector = ClipboardInjector(paste=False, pasteboard=board)
    injector.inject("diktat")
    assert injector._timer is None
    assert board.content == "diktat"


def test_pyperclip_fallback_never_restores_an_empty_string(monkeypatch) -> None:
    """Ohne AppKit ist "" mehrdeutig (leer oder Bild) — nicht zurückschreiben."""
    state = {"content": ""}
    fake = types.ModuleType("pyperclip")
    fake.copy = lambda text: state.update(content=text)  # type: ignore[attr-defined]
    fake.paste = lambda: state["content"]  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pyperclip", fake)

    injector = ClipboardInjector(restore_after=0.01, pasteboard=PyperclipPasteboard())
    injector.inject("diktat")
    assert injector._timer is None
    assert state["content"] == "diktat"


def test_native_pasteboard_round_trips_all_types() -> None:
    """Echtes NSPasteboard, aber ein privates — nie die Ablage des Rechners."""
    appkit = pytest.importorskip("AppKit")
    private = appkit.NSPasteboard.pasteboardWithUniqueName()
    try:
        item = appkit.NSPasteboardItem.alloc().init()
        png = appkit.NSData.dataWithBytes_length_(b"\x89PNG-fake", 9)
        item.setData_forType_(png, "public.png")
        item.setString_forType_("Bildbeschreibung", appkit.NSPasteboardTypeString)
        private.clearContents()
        private.writeObjects_([item])

        board = inject_macos.NativePasteboard(private)
        injector = ClipboardInjector(restore_after=0.01, pasteboard=board)
        injector.inject("diktat")
        assert private.stringForType_(appkit.NSPasteboardTypeString) == "diktat"
        assert inject_macos._TRANSIENT_TYPE in list(private.types())

        _wait(injector)
        assert bytes(private.dataForType_("public.png")) == b"\x89PNG-fake"
        assert private.stringForType_(appkit.NSPasteboardTypeString) == "Bildbeschreibung"
        assert inject_macos._TRANSIENT_TYPE not in list(private.types())
    finally:
        private.releaseGlobally()
