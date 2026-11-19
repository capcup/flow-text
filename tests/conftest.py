from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


import pytest


class FakeOverlay:
    """Ersatz für die AppKit-Anzeige: merkt sich nur, was die Menüleiste verlangt."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.visible = False

    def show(self, mode: str) -> None:
        self.calls.append(f"show:{mode}")
        self.visible = True

    def hide(self) -> None:
        self.calls.append("hide")
        self.visible = False


@pytest.fixture(autouse=True)
def _no_real_overlay(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests sollen nie ein echtes Fenster öffnen oder in den Hauptthread dispatchen."""
    from flowtext import menubar

    monkeypatch.setattr(menubar, "create_overlay", lambda app: FakeOverlay())
    monkeypatch.setattr(menubar, "_on_main", lambda func: None)
