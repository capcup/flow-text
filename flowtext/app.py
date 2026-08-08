"""Controller: verbindet Hotkey, Aufnahme und Pipeline.

Threading-Modell (siehe ANALYSE_UND_UMSETZUNGSPLAN.md §5.2):

    Event-Tap-Thread   → nur ``queue.put_nowait``      (< 1 ms, Pflicht)
    Controller-Thread  → Zustandsautomat, Stream-Start/Stop
    Worker-Thread      → Transkription, LLM, Ausgabe   (darf Sekunden dauern)
    Supervisor-Thread  → Neustart eines gestorbenen Listeners
    PortAudio-Callback → nur ``queue.put_nowait``      (kein Lock)
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from datetime import datetime
from pathlib import Path

from .audio.recorder import AudioError, AudioRecorder
from .config import Config, temp_dir
from .feedback import Feedback
from .hotkey.service import TRIGGER, HotkeyService
from .pipeline.base import Pipeline
from .pipeline.factory import build_pipeline
from .state import State, StateMachine

log = logging.getLogger(__name__)

#: Ältere temporäre Aufnahmen werden beim Start entfernt.
_STALE_TEMP_AGE_SECONDS = 24 * 3600


class FlowTextApp:
    def __init__(
        self,
        cfg: Config,
        *,
        recorder: AudioRecorder | None = None,
        hotkey: HotkeyService | None = None,
        pipeline: Pipeline | None = None,
    ) -> None:
        self.cfg = cfg
        self.state = StateMachine()
        self.events: queue.Queue[str] = queue.Queue(maxsize=8)
        self.jobs: queue.Queue[Path] = queue.Queue(maxsize=4)

        self.recorder = recorder or AudioRecorder(cfg.audio)
        self.hotkey = hotkey or HotkeyService(cfg.hotkey, self.events)
        self.pipeline = pipeline or build_pipeline(cfg.pipeline)
        self.feedback = Feedback(cfg.feedback)
        self.state.subscribe(self.feedback.on_state_change)
        self.state.subscribe(self._log_state)

        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._paused = False

    # ------------------------------------------------------------------ API

    def start(self, *, background_warmup: bool = False) -> None:
        """Startet alle Threads und kehrt sofort zurück.

        Getrennt von :meth:`run`, weil die Menüleisten-Variante den
        Hauptthread für Cocoa braucht und nicht blockiert werden darf.

        ``background_warmup`` lädt das Whisper-Modell nebenher. Für die
        Menüleiste ist das entscheidend: sonst erscheint das Icon erst nach
        dem Laden — beim allerersten Start also erst nach dem kompletten
        Modell-Download, ohne jedes sichtbare Lebenszeichen.
        """
        if self._threads:
            raise RuntimeError("FlowText läuft bereits")

        self._cleanup_temp()
        if background_warmup:
            threading.Thread(target=self._warmup, name="pipeline-warmup", daemon=True).start()
        else:
            self._warmup()

        self._threads = [
            threading.Thread(target=self._controller_loop, name="controller", daemon=True),
            threading.Thread(target=self._worker_loop, name="pipeline-worker", daemon=True),
        ]
        for thread in self._threads:
            thread.start()
        self.hotkey.start()
        log.info("FlowText läuft. Auslöser: %s.", self.hotkey.describe())

    def run(self) -> None:
        """Startet alles und blockiert bis zum Stopp (CLI-Variante)."""
        self.start()
        log.info("Beenden mit Ctrl+C.")
        try:
            while not self._stop.wait(0.5):
                pass
        finally:
            self.shutdown()

    def _warmup(self) -> None:
        try:
            self.pipeline.warmup()
        except Exception:
            log.exception("Warmup fehlgeschlagen — das erste Diktat wird langsam.")

    def request_stop(self) -> None:
        """Signalsicher: setzt nur ein Event, kein Logging, keine I/O."""
        self._stop.set()

    @property
    def stop_requested(self) -> bool:
        """Wurde das Beenden angefordert (Ctrl+C, SIGTERM, Menü)?

        Die Menüleisten-Variante blockiert in ``rumps.run()`` und muss das
        selbst abfragen — sonst verpufft ein SIGTERM wirkungslos und
        ``pkill`` scheint nicht zu funktionieren.
        """
        return self._stop.is_set()

    # ------------------------------------------------------------------ Pause

    @property
    def paused(self) -> bool:
        return self._paused

    def set_paused(self, value: bool) -> None:
        """Hält den Auslöser an, ohne den Listener zu beenden.

        Der Event-Tap bleibt bewusst aktiv: ihn zu stoppen und neu zu starten
        wäre der riskantere Weg (Berechtigungen, Tap-Neuaufbau). Es werden
        lediglich die Auslöser verworfen.
        """
        if value == self._paused:
            return
        self._paused = value
        if value and self.state.current is State.RECORDING:
            self.recorder.abort()
            self.state.transition(State.IDLE)
            log.info("Laufende Aufnahme wegen Pause verworfen.")
        log.info("FlowText %s.", "pausiert" if value else "wieder aktiv")

    def shutdown(self) -> None:
        if not self._stop.is_set():
            self._stop.set()
        self.hotkey.stop()
        self.recorder.abort()
        self.pipeline.shutdown()
        for thread in self._threads:
            thread.join(timeout=2.0)
        if self.hotkey.dropped_triggers:
            log.warning("%d Auslöser wurden verworfen (Überlast).", self.hotkey.dropped_triggers)
        log.info(
            "FlowText beendet. Langsamster Hotkey-Callback: %.2f ms.",
            self.hotkey.slowest_callback_ms,
        )

    # ------------------------------------------------------- Controller-Loop

    def _controller_loop(self) -> None:
        while not self._stop.is_set():
            try:
                event = self.events.get(timeout=0.2)
            except queue.Empty:
                self._check_max_duration()
                continue
            if event == TRIGGER:
                try:
                    self._on_trigger()
                except Exception:
                    log.exception("Unerwarteter Fehler im Controller.")
                    self._to_error()

    def _on_trigger(self) -> None:
        if self._paused:
            log.debug("Auslöser ignoriert — FlowText ist pausiert.")
            return
        current = self.state.current
        if current is State.IDLE:
            self._begin_recording()
        elif current is State.RECORDING:
            self._finish_recording()
        else:
            log.info("Auslöser ignoriert — Zustand ist %s.", current)

    def _begin_recording(self) -> None:
        try:
            self.recorder.start()
        except AudioError as exc:
            log.error("Aufnahme konnte nicht gestartet werden: %s", exc)
            log.error(
                "Prüfen: Systemeinstellungen → Datenschutz & Sicherheit → Mikrofon "
                "für die startende App (Terminal, iTerm2, VS Code …)."
            )
            self._to_error()
            return
        self.state.transition(State.RECORDING)
        log.info("🎤 Aufnahme läuft — erneut auslösen zum Stoppen.")

    def _finish_recording(self, *, reason: str = "Hotkey") -> None:
        recording = self.recorder.stop()
        if recording is None:
            log.info("Aufnahme verworfen (%s).", reason)
            self.state.transition(State.IDLE)
            return

        self.state.transition(State.PROCESSING)
        log.info(
            "🛑 Aufnahme beendet (%s): %.2fs, Pegel %.3f.",
            reason,
            recording.duration,
            recording.peak,
        )
        if recording.peak < 0.001:
            log.warning("Signal ist praktisch stumm — falsches Eingabegerät?")

        try:
            path = self.recorder.save(recording, self._new_temp_path())
        except Exception as exc:  # noqa: BLE001 — auch ImportError (soundfile fehlt)
            log.error("Audiodatei konnte nicht geschrieben werden: %s", exc)
            self._to_error()
            return

        try:
            self.jobs.put_nowait(path)
        except queue.Full:
            log.error("Verarbeitungswarteschlange voll — Aufnahme verworfen.")
            path.unlink(missing_ok=True)
            self._to_error()

    def _check_max_duration(self) -> None:
        if self.state.current is not State.RECORDING:
            return
        if self.recorder.elapsed >= self.cfg.audio.max_seconds:
            log.warning("Maximale Aufnahmedauer erreicht — automatischer Stopp.")
            self._finish_recording(reason="Zeitlimit")

    # ----------------------------------------------------------- Worker-Loop

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                path = self.jobs.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self.pipeline.run(path)
            except Exception:
                log.exception("Verarbeitung fehlgeschlagen.")
                self._to_error()
            else:
                self.state.transition(State.IDLE)
            finally:
                if self.cfg.audio.delete_after_use:
                    path.unlink(missing_ok=True)

    # --------------------------------------------------------------- Helfer

    def _to_error(self) -> None:
        self.state.transition(State.ERROR)
        self.state.transition(State.IDLE)

    def _log_state(self, previous: State, current: State) -> None:
        log.debug("Zustand: %s → %s", previous, current)

    def _new_temp_path(self) -> Path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        return temp_dir() / f"recording-{stamp}.wav"

    def _cleanup_temp(self) -> None:
        directory = temp_dir()
        if not directory.is_dir():
            return
        now = time.time()
        for path in directory.glob("recording-*.wav"):
            try:
                if now - path.stat().st_mtime > _STALE_TEMP_AGE_SECONDS:
                    path.unlink()
            except OSError:
                pass
