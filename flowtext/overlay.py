"""Aufnahme-Anzeige: eine kleine Pille am unteren Bildschirmrand (AppKit).

Zeigt auf einen Blick, dass FlowText zuhört und ob die Stimme ankommt: fünf
Balken, die der Lautstärke folgen. Während der Verarbeitung läuft eine graue
Welle. Aussehen und Zahlen: :mod:`flowtext.indicator`.

**Das Fenster darf nie den Fokus nehmen.** Sonst landet der diktierte Text
nicht mehr in dem Feld, in das man diktiert. Deshalb:

* ein ``NSPanel`` im Stil *non-activating* ohne Rahmen, das nie Key- oder
  Main-Window werden kann,
* Mausklicks gehen durch (``ignoresMouseEvents``),
* es wird mit ``orderFrontRegardless`` gezeigt, nicht über die App aktiviert,
* es erscheint auf allen Spaces und über Vollbild-Apps.

Läuft ausschließlich im Hauptthread (Cocoa).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import objc
from AppKit import (
    NSBackingStoreBuffered,
    NSBezierPath,
    NSBitmapImageFileTypePNG,
    NSColor,
    NSEvent,
    NSMakeRect,
    NSPanel,
    NSPointInRect,
    NSRectFill,
    NSScreen,
    NSStatusWindowLevel,
    NSView,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorIgnoresCycle,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
)
from Foundation import NSRunLoop, NSRunLoopCommonModes, NSTimer

from . import indicator

log = logging.getLogger(__name__)

_WIDTH = 84.0
_HEIGHT = 36.0
#: Abstand zur Unterkante des sichtbaren Bereichs (über dem Dock).
_BOTTOM_MARGIN = 28.0
_FPS = 30.0

_BAR_WIDTH = 4.0
_BAR_GAP = 8.0


def _srgb(r: float, g: float, b: float, a: float = 1.0) -> Any:
    return NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, a)


_PILL = _srgb(0.173, 0.173, 0.165, 0.94)  # #2C2C2A
_BORDER = _srgb(0.373, 0.369, 0.353, 1.0)  # #5F5E5A
_ACTIVE = _srgb(0.945, 0.937, 0.910, 1.0)  # #F1EFE8
_BUSY = _srgb(0.533, 0.529, 0.502, 1.0)  # #888780


class _BarsView(NSView):  # type: ignore[misc]
    """Zeichnet die Pille und die Balken aus ``heights`` und ``mode``."""

    def initWithFrame_(self, frame: Any) -> _BarsView | None:
        instance = objc.super(_BarsView, self).initWithFrame_(frame)
        if instance is None:
            return None
        instance.heights = [indicator.BASE] * indicator.BARS
        instance.mode = "recording"
        #: Nur für Vorschau-Bilder: füllt den Hintergrund, damit man die
        #: halbtransparente Pille sieht.
        instance.backdrop = None
        return instance  # type: ignore[no-any-return]

    def drawRect_(self, _rect: Any) -> None:
        bounds = self.bounds()
        if self.backdrop is not None:
            self.backdrop.setFill()
            NSRectFill(bounds)

        radius = bounds.size.height / 2
        inset = NSMakeRect(0.5, 0.5, bounds.size.width - 1, bounds.size.height - 1)
        pill = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(inset, radius, radius)
        _PILL.setFill()
        pill.fill()
        _BORDER.setStroke()
        pill.setLineWidth_(0.5)
        pill.stroke()

        total = indicator.BARS * _BAR_WIDTH + (indicator.BARS - 1) * _BAR_GAP
        x = (bounds.size.width - total) / 2
        (_BUSY if self.mode == "processing" else _ACTIVE).setFill()
        for height in self.heights:
            h = max(float(height), _BAR_WIDTH)
            rect = NSMakeRect(x, (bounds.size.height - h) / 2, _BAR_WIDTH, h)
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                rect, _BAR_WIDTH / 2, _BAR_WIDTH / 2
            ).fill()
            x += _BAR_WIDTH + _BAR_GAP


class RecordingOverlay:
    def __init__(self, level_source: Callable[[], float]) -> None:
        #: Liefert den aktuellen Mikrofonpegel (RMS). Wird ~30-mal pro Sekunde gefragt.
        self._source = level_source
        self._window: Any = None
        self._view: Any = None
        self._timer: Any = None
        self._mode: str | None = None
        self._level = 0.0

    # ------------------------------------------------------------------ API

    @property
    def visible(self) -> bool:
        return self._mode is not None

    def show(self, mode: str) -> None:
        """Zeigt die Anzeige im Modus ``"recording"`` oder ``"processing"``. Idempotent."""
        if mode == self._mode:
            return
        if self._window is None:
            self._build()
        first = self._mode is None
        self._mode = mode
        self._view.mode = mode
        if first:
            self._level = 0.0
            self._place()
            self._window.orderFrontRegardless()
            self._start_timer()
        self._frame()

    def hide(self) -> None:
        if self._mode is None:
            return
        self._mode = None
        if self._timer is not None:
            self._timer.invalidate()
            self._timer = None
        self._window.orderOut_(None)

    def render_png(self, path: Path, mode: str = "recording", level: float = 0.5) -> None:
        """Zeichnet die Anzeige in eine PNG-Datei (graue Fläche dahinter) — für Tests und Bilder."""
        if self._window is None:
            self._build()
        self._view.mode = mode
        self._view.heights = indicator.bar_heights(mode, level, 0.37)
        self._view.backdrop = _srgb(0.55, 0.55, 0.55)
        try:
            bounds = self._view.bounds()
            rep = self._view.bitmapImageRepForCachingDisplayInRect_(bounds)
            self._view.cacheDisplayInRect_toBitmapImageRep_(bounds, rep)
            data = rep.representationUsingType_properties_(NSBitmapImageFileTypePNG, {})
            data.writeToFile_atomically_(str(path), True)
        finally:
            self._view.backdrop = None

    # -------------------------------------------------------------- intern

    def _build(self) -> None:
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, _WIDTH, _HEIGHT),
            NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
            NSBackingStoreBuffered,
            False,
        )
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setHasShadow_(False)
        panel.setLevel_(NSStatusWindowLevel)
        panel.setIgnoresMouseEvents_(True)
        panel.setHidesOnDeactivate_(False)
        panel.setReleasedWhenClosed_(False)
        panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorIgnoresCycle
        )
        view = _BarsView.alloc().initWithFrame_(NSMakeRect(0, 0, _WIDTH, _HEIGHT))
        panel.setContentView_(view)
        self._window, self._view = panel, view

    def _place(self) -> None:
        """Unten mittig auf dem Bildschirm, auf dem gerade der Mauszeiger ist."""
        mouse = NSEvent.mouseLocation()
        screens = NSScreen.screens()
        screen = next((s for s in screens if NSPointInRect(mouse, s.frame())), None)
        screen = screen or NSScreen.mainScreen()
        if screen is None:
            return
        area = screen.visibleFrame()
        x = area.origin.x + (area.size.width - _WIDTH) / 2
        self._window.setFrameOrigin_((x, area.origin.y + _BOTTOM_MARGIN))

    def _start_timer(self) -> None:
        # Common modes: die Animation läuft auch, während ein Menü offen ist.
        timer = NSTimer.timerWithTimeInterval_repeats_block_(1.0 / _FPS, True, self._on_timer)
        NSRunLoop.currentRunLoop().addTimer_forMode_(timer, NSRunLoopCommonModes)
        self._timer = timer

    def _on_timer(self, _timer: Any) -> None:
        try:
            self._frame()
        except Exception:
            # Ein Fehler im Timer darf Cocoa nicht erreichen; ohne Animation
            # bleibt die Anzeige einfach stehen.
            log.exception("Overlay-Animation fehlgeschlagen — Anzeige wird abgeschaltet.")
            self.hide()

    def _frame(self) -> None:
        mode = self._mode
        if mode is None:
            return
        if mode == "recording":
            target = indicator.normalise(float(self._source()))
            self._level = indicator.smooth(self._level, target)
        self._view.heights = indicator.bar_heights(mode, self._level, time.monotonic())
        self._view.setNeedsDisplay_(True)
