"""Anzeige-Logik des Aufnahme-Overlays — reine Rechnung, ohne AppKit.

Das Overlay zeigt fünf Balken: bei der Aufnahme folgen sie der Lautstärke,
während der Verarbeitung läuft eine gleichmäßige graue Welle. Die Zahlen
hier bestimmen, wie das aussieht; gezeichnet wird in :mod:`flowtext.overlay`.
"""

from __future__ import annotations

import math

#: Anzahl der Balken und ihre relative Höhe (die Mitte schlägt am stärksten aus).
BARS = 5
PROFILE = (0.45, 0.8, 1.0, 0.8, 0.45)

#: Mindesthöhe eines Balkens und maximaler Ausschlag darüber (in Punkten).
BASE = 5.0
AMPLITUDE = 22.0

#: Pegel (RMS), ab dem die Balken voll ausschlagen. Normale Sprache liegt
#: bei etwa 0,05 bis 0,15, Raumrauschen bei 0,003 (gemessen 2026-10-27).
REFERENCE_RMS = 0.1


def normalise(rms: float) -> float:
    """RMS-Pegel (0 bis ~1) → Ausschlag 0 bis 1.

    Mit Exponent < 1, damit leise Sprache sichtbar bewegt: bei der
    Stille-Schwelle (0,005) sind es ~0,12, bei Raumrauschen kaum etwas.
    """
    if rms <= 0.0:
        return 0.0
    return float(min(1.0, (rms / REFERENCE_RMS) ** 0.7))


def smooth(previous: float, target: float, *, attack: float = 0.6, decay: float = 0.2) -> float:
    """Schnell hoch, langsam runter — sonst flackern die Balken."""
    rate = attack if target > previous else decay
    return previous + (target - previous) * rate


def bar_heights(mode: str, level: float, t: float) -> list[float]:
    """Höhe jedes Balkens in Punkten.

    ``mode`` ist ``"recording"`` oder ``"processing"``; ``level`` der geglättete
    Ausschlag (0 bis 1), ``t`` die Zeit in Sekunden für die Bewegung.
    """
    if mode == "processing":
        return [BASE + 9.0 * (0.5 + 0.5 * math.sin(t * 6.0 - i * 0.9)) for i in range(BARS)]
    return [
        BASE + PROFILE[i] * level * AMPLITUDE * (0.7 + 0.3 * math.sin(t * 9.0 + i * 1.3))
        for i in range(BARS)
    ]
