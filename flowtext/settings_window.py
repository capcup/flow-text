"""Settings-Fenster der Menüleisten-App (AppKit).

Ersetzt "Open Configuration…", das nur ``config.toml`` im Editor öffnete.
Das Fenster zeigt die Schalter, die man im Alltag ändert; jede Änderung gilt
sofort (kein Speichern-Knopf, kein Neustart) und wird in
``preferences.json`` gemerkt, siehe :mod:`flowtext.preferences`. Alles
Übrige bleibt in ``config.toml`` — dafür gibt es unten einen Knopf.

Aufbau aus :data:`flowtext.preferences.OPTIONS`: kommt dort ein Schalter dazu,
erscheint er hier ohne weiteren Code.

Läuft ausschließlich im Hauptthread (Cocoa). Die Logik steckt in
``FlowTextApp.set_option``; dieses Modul ist nur die Ansicht.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import objc
from AppKit import (
    NSApplication,
    NSBackingStoreBuffered,
    NSBezelStyleRounded,
    NSBitmapImageFileTypePNG,
    NSButton,
    NSButtonTypeSwitch,
    NSColor,
    NSControlStateValueOff,
    NSControlStateValueOn,
    NSFont,
    NSMakeRect,
    NSObject,
    NSTextField,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskTitled,
)

from . import preferences

if TYPE_CHECKING:
    from .app import FlowTextApp

log = logging.getLogger(__name__)

_WIDTH = 460.0
_MARGIN = 20.0
_INDENT = 19.0  # Einrückung des Hinweistextes unter einem Kästchen
_HINT_HEIGHT = 30.0
_ROW_GAP = 14.0
_BAR_HEIGHT = 52.0


class _Actions(NSObject):  # type: ignore[misc]
    """Ziel der Bedienelemente; leitet an :class:`SettingsWindow` weiter."""

    def initWithWindow_(self, window: SettingsWindow) -> _Actions | None:
        instance = objc.super(_Actions, self).init()
        if instance is None:
            return None
        instance._window = window
        return instance  # type: ignore[no-any-return]

    @objc.IBAction  # type: ignore[untyped-decorator]
    def optionToggled_(self, sender: Any) -> None:
        self._window.option_toggled(int(sender.tag()), sender.state() == NSControlStateValueOn)

    @objc.IBAction  # type: ignore[untyped-decorator]
    def openConfig_(self, _sender: Any) -> None:
        self._window.open_config()

    @objc.IBAction  # type: ignore[untyped-decorator]
    def done_(self, _sender: Any) -> None:
        self._window.close()

    def controlTextDidEndEditing_(self, notification: Any) -> None:
        field = notification.object()
        self._window.text_committed(int(field.tag()), str(field.stringValue()))

    def windowWillClose_(self, _notification: Any) -> None:
        self._window.commit_text_fields()


class SettingsWindow:
    def __init__(self, app: FlowTextApp, open_config: Callable[[], None]) -> None:
        self._app = app
        self._open_config = open_config
        self._window: Any = None
        self._actions: Any = None
        #: Index in ``OPTIONS`` → Bedienelement
        self._controls: dict[int, Any] = {}

    # ------------------------------------------------------------------ API

    def show(self) -> None:
        if self._window is None:
            self._build()
        self.refresh()
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self._window.makeKeyAndOrderFront_(None)

    def close(self) -> None:
        if self._window is not None:
            self._window.close()

    def refresh(self) -> None:
        """Zeigt die aktuellen Werte an (etwa nachdem config.toml neu geladen wurde)."""
        for index, option in enumerate(preferences.OPTIONS):
            control = self._controls[index]
            value = preferences.get(self._app.cfg, option)
            if option.kind is bool:
                control.setState_(NSControlStateValueOn if value else NSControlStateValueOff)
            else:
                control.setStringValue_(", ".join(value))

    def render_png(self, path: Path) -> None:
        """Zeichnet das Fenster in eine PNG-Datei — für Tests und Screenshots ohne Bildschirm."""
        if self._window is None:
            self._build()
        self.refresh()
        # Der Rahmen (mit Titelleiste und Fensterhintergrund), nicht nur der Inhalt:
        # sonst fehlt der Hintergrund und heller Text steht auf weißem Grund.
        view = self._window.contentView().superview()
        rep = view.bitmapImageRepForCachingDisplayInRect_(view.bounds())
        view.cacheDisplayInRect_toBitmapImageRep_(view.bounds(), rep)
        data = rep.representationUsingType_properties_(NSBitmapImageFileTypePNG, {})
        data.writeToFile_atomically_(str(path), True)

    # ------------------------------------------------- Rückmeldungen der Ansicht

    def option_toggled(self, index: int, value: bool) -> None:
        option = preferences.OPTIONS[index]
        self._apply(option, value)

    def text_committed(self, index: int, text: str) -> None:
        option = preferences.OPTIONS[index]
        terms = preferences.parse_terms(text)
        if terms == preferences.get(self._app.cfg, option):
            return
        if self._apply(option, terms):
            # Zeigt die bereinigte Fassung (ohne Leere und Doppelte).
            self._controls[index].setStringValue_(", ".join(terms))

    def commit_text_fields(self) -> None:
        """Beim Schließen: ein noch nicht bestätigter Text soll nicht verloren gehen."""
        for index, option in enumerate(preferences.OPTIONS):
            if option.kind is list:
                self.text_committed(index, str(self._controls[index].stringValue()))

    def open_config(self) -> None:
        self._open_config()

    # -------------------------------------------------------------- intern

    def _apply(self, option: preferences.Option, value: Any) -> bool:
        try:
            self._app.set_option(option.section, option.key, value)
        except Exception:
            # Ein Fehler im Callback darf Cocoa nicht erreichen.
            log.exception(
                "Einstellung %s.%s konnte nicht gesetzt werden", option.section, option.key
            )
            self.refresh()
            return False
        return True

    def _build(self) -> None:
        options = preferences.OPTIONS
        heights = [self._row_height(o) for o in options]
        total = _MARGIN + sum(heights) + _BAR_HEIGHT

        self._actions = _Actions.alloc().initWithWindow_(self)
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, _WIDTH, total),
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable,
            NSBackingStoreBuffered,
            False,
        )
        window.setTitle_("FlowText Settings")
        window.setReleasedWhenClosed_(False)  # sonst ist das Fenster nach dem Schließen weg
        window.setDelegate_(self._actions)
        content = window.contentView()

        top = total - _MARGIN
        for index, option in enumerate(options):
            top = self._add_row(content, index, option, top)

        self._add_button(content, "Open config.toml…", _MARGIN, 14, 150, "openConfig:")
        done = self._add_button(content, "Done", _WIDTH - _MARGIN - 80, 14, 80, "done:")
        done.setKeyEquivalent_("\r")

        window.center()
        self._window = window

    @staticmethod
    def _row_height(option: preferences.Option) -> float:
        if option.kind is bool:
            return 20 + 2 + _HINT_HEIGHT + _ROW_GAP
        return 18 + 4 + 24 + 4 + _HINT_HEIGHT + _ROW_GAP

    def _add_row(self, content: Any, index: int, option: preferences.Option, top: float) -> float:
        """Legt eine Zeile an und gibt die Oberkante der nächsten zurück."""
        width = _WIDTH - 2 * _MARGIN

        if option.kind is bool:
            box = NSButton.alloc().initWithFrame_(NSMakeRect(_MARGIN, top - 20, width, 20))
            box.setButtonType_(NSButtonTypeSwitch)
            box.setTitle_(option.label)
            box.setTag_(index)
            box.setTarget_(self._actions)
            box.setAction_("optionToggled:")
            content.addSubview_(box)
            self._controls[index] = box
            hint_top = top - 22
            hint_x = _MARGIN + _INDENT
        else:
            title = NSTextField.labelWithString_(option.label)
            title.setFrame_(NSMakeRect(_MARGIN, top - 18, width, 18))
            content.addSubview_(title)
            field = NSTextField.alloc().initWithFrame_(NSMakeRect(_MARGIN, top - 46, width, 24))
            field.setTag_(index)
            field.setDelegate_(self._actions)
            field.setPlaceholderString_("e.g. FlowText, Ollama")
            content.addSubview_(field)
            self._controls[index] = field
            hint_top = top - 50
            hint_x = _MARGIN

        hint = NSTextField.wrappingLabelWithString_(option.hint)
        hint.setFont_(NSFont.systemFontOfSize_(11))
        hint.setTextColor_(NSColor.secondaryLabelColor())
        hint.setFrame_(
            NSMakeRect(hint_x, hint_top - _HINT_HEIGHT, width - (hint_x - _MARGIN), _HINT_HEIGHT)
        )
        content.addSubview_(hint)
        return top - self._row_height(option)

    def _add_button(
        self, content: Any, title: str, x: float, y: float, width: float, action: str
    ) -> Any:
        button = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, width, 28))
        button.setBezelStyle_(NSBezelStyleRounded)
        button.setTitle_(title)
        button.setTarget_(self._actions)
        button.setAction_(action)
        content.addSubview_(button)
        return button
