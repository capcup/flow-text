#!/usr/bin/env python3
"""Kompatibilitäts-Shim.

Die Implementierung lebt seit v1.1 im Paket ``flowtext``. Diese Datei bleibt
erhalten, damit ``python main.py`` weiterhin funktioniert.

    python -m flowtext              # bevorzugt
    python tools/diagnose.py        # Diagnose bei Problemen
"""

from __future__ import annotations

import sys

from flowtext.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
