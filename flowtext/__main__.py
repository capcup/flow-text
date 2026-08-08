"""Einstiegspunkt: ``python -m flowtext`` bzw. ``flowtext``."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from pathlib import Path

from . import __version__, permissions
from .config import (
    Config,
    ConfigError,
    candidate_paths,
    conflicts,
    load,
    log_dir,
    temp_dir,
)
from .logging_setup import setup as setup_logging


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="flowtext",
        description="Lokale Voice-to-Text-Engine für macOS.",
    )
    parser.add_argument("--config", type=Path, help="Pfad zu einer config.toml")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--hotkey-mode", choices=["double_tap", "combo"])
    parser.add_argument("--combo", help="Kombination, z. B. '<ctrl>+<shift>+d'")
    parser.add_argument("--tap-key", help="Taste für Doppel-Tap, z. B. cmd_r")
    parser.add_argument(
        "--menubar",
        action="store_true",
        help="Als Menüleisten-App starten (Statusicon statt Terminal-Log)",
    )
    parser.add_argument(
        "--watch-keys",
        action="store_true",
        help="Tastaturereignisse live anzeigen statt aufzunehmen (Diagnose)",
    )
    parser.add_argument(
        "--skip-permission-check",
        action="store_true",
        help="Berechtigungsprüfung überspringen (nur zum Debuggen)",
    )
    parser.add_argument("--version", action="version", version=f"FlowText {__version__}")
    return parser.parse_args(argv)


def _apply_overrides(cfg: Config, args: argparse.Namespace) -> None:
    if args.log_level:
        cfg.logging.level = args.log_level
    if args.hotkey_mode:
        cfg.hotkey.mode = args.hotkey_mode
    if args.combo:
        cfg.hotkey.combo = args.combo
        cfg.hotkey.mode = args.hotkey_mode or "combo"
    if args.tap_key:
        cfg.hotkey.tap_key = args.tap_key
        cfg.hotkey.mode = args.hotkey_mode or "double_tap"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    try:
        cfg = load(args.config)
    except ConfigError as exc:
        sys.stderr.write(f"Konfigurationsfehler: {exc}\n")
        return 2
    _apply_overrides(cfg, args)

    log = setup_logging(cfg.logging)
    log.info("=== FlowText %s ===", __version__)
    log.info(permissions.environment_report())
    log.info("Konfiguration: %s", cfg.source)
    if cfg.source == "defaults":
        log.debug("Gesucht in: %s", ", ".join(str(p) for p in candidate_paths()))
    for warning in conflicts(cfg):
        log.warning("⚠️  %s", warning)

    if args.watch_keys:
        from . import diagnostics

        diagnostics.report_environment()
        diagnostics.report_permissions()
        return 0 if diagnostics.watch_keys(cfg) else 1

    perms = permissions.check()
    log.info(perms.summary())
    if not perms.ok and not args.skip_permission_check:
        # In der Menüleisten-Variante darf das kein Abbruch sein: eine frisch
        # gebaute FlowText.app hat noch keine Rechte und könnte sie nie
        # anfordern, wenn sie sich vorher beendet. Sie startet also, zeigt 🔒
        # und bietet im Menü den direkten Weg in die Systemeinstellungen.
        # macOS fragt zusätzlich von selbst, sobald der Event-Tap entsteht.
        if not args.menubar:
            sys.stderr.write("\n" + permissions.help_text() + "\n")
            return 2
        log.warning("Berechtigungen fehlen — FlowText startet trotzdem und zeigt 🔒.")
        for line in permissions.help_text().splitlines():
            log.warning("%s", line)
    if perms.input_monitoring is permissions.HIDAccess.UNKNOWN:
        # UNKNOWN = macOS hat noch nie gefragt. Der Systemdialog erscheint beim
        # ersten Tap-Versuch; wird er weggeklickt, bleibt der Tap für immer
        # stumm. Deshalb hier ausdrücklich warnen statt stillschweigend zu starten.
        log.warning(
            "Eingabeüberwachung ist noch nicht erteilt (Status: unbekannt). macOS "
            "fragt gleich nach — der Dialog muss bestätigt werden, sonst bleibt "
            "der Hotkey wirkungslos."
        )

    temp_dir().mkdir(parents=True, exist_ok=True)
    log.debug("Temporäre Aufnahmen: %s | Logs: %s", temp_dir(), log_dir())

    # Import erst hier, damit --version und --help ohne sounddevice/pynput laufen.
    from .app import FlowTextApp

    try:
        app = FlowTextApp(cfg)
    except Exception:
        logging.getLogger("flowtext").exception("FlowText konnte nicht initialisiert werden.")
        return 1

    # Signal-Handler setzen NUR ein Event — kein Logging, kein I/O.
    # (logging ist nicht reentrant-sicher; ein Log-Aufruf aus dem
    #  Signal-Kontext kann den Prozess in einem Deadlock hinterlassen.)
    def _handler(signum: int, frame: object) -> None:
        app.request_stop()

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)

    if args.menubar:
        from . import menubar

        if not menubar.available():
            sys.stderr.write(
                "Die Menüleisten-Variante braucht rumps.\n"
                "Installation: pip install 'flowtext[menubar]'\n"
                "Oder ohne Icon starten: python -m flowtext\n"
            )
            return 2
        # rumps übernimmt den Hauptthread; app.start() läuft daneben.
        menubar.MenubarApp(cfg, app).run()
        return 0

    try:
        app.run()
    except KeyboardInterrupt:
        app.request_stop()
    return 0


def menubar_entry(argv: list[str] | None = None) -> int:
    """Einstiegspunkt ``flowtext-menubar`` — wie ``main`` mit ``--menubar``."""
    return main([*(argv or sys.argv[1:]), "--menubar"])


if __name__ == "__main__":
    raise SystemExit(main())
