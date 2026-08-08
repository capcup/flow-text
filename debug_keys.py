#!/usr/bin/env python3
"""Kompatibilitäts-Shim — das Diagnose-Tool liegt jetzt in ``tools/diagnose.py``."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "tools"))

from diagnose import main

if __name__ == "__main__":
    print("Hinweis: debug_keys.py ist veraltet — bitte 'python tools/diagnose.py' nutzen.\n")
    sys.exit(main())
