#!/usr/bin/env python3
"""FlowText-Benchmark: Latenz und Wortfehlerrate auf eigenen Aufnahmen.

    python tools/bench.py diktate/
    python tools/bench.py diktate/ --plain --details
    python tools/bench.py diktate/ --model mlx-community/whisper-large-v3-turbo-q4

Der Ordner enthält ``*.wav`` und optional gleichnamige ``*.txt`` mit dem
tatsächlich Gesagten. Die Logik liegt in ``flowtext.bench``.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flowtext import bench
from flowtext.config import ConfigError, load


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FlowText-Benchmark")
    parser.add_argument("folder", type=Path, help="Ordner mit *.wav und optional *.txt")
    parser.add_argument(
        "--model",
        action="append",
        default=[],
        help="zusätzliches Whisper-Modell zum Vergleich (mehrfach möglich)",
    )
    parser.add_argument(
        "--plain",
        action="store_true",
        help="zusätzlich ohne Vokabeln, Bereinigung und Stille-Schwelle messen",
    )
    parser.add_argument("--repeats", type=int, default=3, help="Läufe pro Clip (Median)")
    parser.add_argument("--details", action="store_true", help="Ergebnis je Clip mit Text")
    parser.add_argument("--config", type=Path, default=None, help="alternative config.toml")
    args = parser.parse_args(argv)

    # Die Pipeline protokolliert Transkripte; im Bericht stehen sie ohnehin.
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    try:
        cfg = load(args.config)
        results = bench.run(
            cfg,
            args.folder,
            models=args.model,
            compare_plain=args.plain,
            repeats=args.repeats,
        )
    except (ConfigError, FileNotFoundError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1

    print(bench.format_report(results, details=args.details))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
