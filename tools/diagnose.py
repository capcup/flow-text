#!/usr/bin/env python3
"""FlowText-Diagnose.

Prüft von außen nach innen: Umgebung → Berechtigungen → Mikrofon → Tastatur.

    python tools/diagnose.py
    python tools/diagnose.py --no-mic --seconds 20

Die Logik liegt in ``flowtext.diagnostics``; für einen schnellen Tastaturtest
ohne dieses Skript genügt ``python -m flowtext --watch-keys``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flowtext import diagnostics
from flowtext.config import Config, ConfigError, load


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FlowText-Diagnose")
    parser.add_argument("--no-listen", action="store_true", help="Tastaturtest überspringen")
    parser.add_argument("--no-mic", action="store_true", help="Mikrofontest überspringen")
    parser.add_argument("--seconds", type=float, default=60.0, help="Dauer des Tastaturtests")
    args = parser.parse_args(argv)

    try:
        cfg = load()
    except ConfigError as exc:
        print(f"{diagnostics.WARN}Konfiguration ungültig ({exc}) — nutze Defaults.")
        cfg = Config()

    return diagnostics.run_all(
        cfg,
        mic=not args.no_mic,
        keys=not args.no_listen,
        seconds=args.seconds,
    )


if __name__ == "__main__":
    raise SystemExit(main())
