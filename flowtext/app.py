"""Controller: verbindet Hotkey, Aufnahme und Pipeline.

Threading-Modell (siehe docs/PROJECT_PLAN.md §3 "Threading model"):

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
from .hotkey.detectors import Signal
from .hotkey.service import HotkeyService
from .pipeline.base import Pipeline
from .pipeline.factory import build_pipeline
from .state import State, StateMachine

log = logging.getLogger(__name__)

#: Ältere temporäre Aufnahmen werden beim Start entfernt.
_STALE_TEMP_AGE_SECONDS = 24 * 3600

#: Sicherheitsnetz für den frühen Start: bleibt eine vorläufig gestartete
#: Aufnahme länger ohne Bestätigung/Abbruch offen (z. B. weil ein Ereignis
#: verloren ging), wird sie verworfen. Deutlich über ``max_tap_duration``.
_TENTATIVE_TIMEOUT_SECONDS = 2.0


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
        #: ``True``, wenn das Mikrofon schon läuft, der Doppel-Tap aber noch
        #: nicht bestätigt ist (siehe ``Signal.START``). Gehört dem Controller-Thread;
        #: nur ``set_paused`` greift von außen ein.
        self._tentative = False

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
        log.info("FlowText running. Trigger: %s.", self.hotkey.describe())

    def run(self) -> None:
        """Startet alles und blockiert bis zum Stopp (CLI-Variante)."""
        self.start()
        log.info("Press Ctrl+C to quit.")
        try:
            while not self._stop.wait(0.5):
                pass
        finally:
            self.shutdown()

    def _warmup(self) -> None:
        try:
            self.pipeline.warmup()
        except Exception:
            log.exception("Warmup failed — initial dictation will be delayed.")

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
        if value and self._tentative:
            self._discard_tentative("Pause")
        if value and self.state.current is State.RECORDING:
            self.recorder.abort()
            self.state.transition(State.IDLE)
            log.info("Recording discarded due to pause.")
        log.info("FlowText %s.", "paused" if value else "active again")

    def shutdown(self) -> None:
        if not self._stop.is_set():
            self._stop.set()
        self.hotkey.stop()
        self.recorder.abort()
        self.pipeline.shutdown()
        for thread in self._threads:
            thread.join(timeout=2.0)
        if self.hotkey.dropped_triggers:
            log.warning("%d triggers discarded (overload).", self.hotkey.dropped_triggers)
        log.info(
            "FlowText stopped. Slowest hotkey callback: %.2f ms.",
            self.hotkey.slowest_callback_ms,
        )

    # ------------------------------------------------------- Controller-Loop

    def _controller_loop(self) -> None:
        while not self._stop.is_set():
            try:
                event = self.events.get(timeout=0.2)
            except queue.Empty:
                self._check_max_duration()
                self._check_tentative_timeout()
                continue
            try:
                self._on_signal(event)
            except Exception:
                log.exception("Unexpected error in controller.")
                self._to_error()

    def _on_signal(self, event: str) -> None:
        """Verteilt ein Hotkey-Signal. Unbekannte Werte werden ignoriert."""
        if event == Signal.START:
            self._on_early_start()
        elif event == Signal.CANCEL:
            self._discard_tentative("kein Doppel-Tap")
        elif event == Signal.CONFIRM:
            self._on_trigger()

    # ------------------------------------------------------ früher Start

    def _on_early_start(self) -> None:
        """Öffnet das Mikrofon schon beim zweiten Tastendruck.

        Der Zustand bleibt ``IDLE`` (und es gibt keinen Ton): erst die
        Bestätigung beim Loslassen macht daraus eine echte Aufnahme. So
        geht der Anfang des Diktats nicht verloren, ein Fehlstart bleibt
        aber unsichtbar. Läuft schon etwas oder ist FlowText pausiert, wird
        das Signal ignoriert — im Zustand ``RECORDING`` ist es der zweite Tap
        des Stopp-Doppel-Taps.
        """
        if self._paused or self._tentative or self.state.current is not State.IDLE:
            return
        try:
            self.recorder.start()
        except AudioError as exc:
            # Kein Fehlalarm: vielleicht war es gar kein Doppel-Tap. Scheitert es
            # wirklich, wiederholt die Bestätigung den Start samt Fehlerbehandlung.
            log.debug("Früher Start fehlgeschlagen (%s) — Bestätigung versucht es erneut.", exc)
            return
        self._tentative = True
        log.debug("Mikrofon vorläufig geöffnet (früher Start).")

    def _discard_tentative(self, reason: str) -> None:
        """Verwirft eine vorläufig gestartete Aufnahme. Ohne Start: wirkungslos."""
        if not self._tentative:
            return
        self._tentative = False
        self.recorder.abort()
        log.debug("Vorläufige Aufnahme verworfen (%s).", reason)

    def _check_tentative_timeout(self) -> None:
        if self._tentative and self.recorder.elapsed >= _TENTATIVE_TIMEOUT_SECONDS:
            self._discard_tentative("Zeitüberschreitung ohne Bestätigung")

    def _on_trigger(self) -> None:
        if self._paused:
            log.debug("Trigger ignored — FlowText is paused.")
            return
        current = self.state.current
        if current is State.IDLE:
            self._begin_recording()
        elif current is State.RECORDING:
            self._finish_recording()
        else:
            log.info("Trigger ignored — state is %s.", current)

    def _begin_recording(self) -> None:
        already_running = self._tentative and self.recorder.is_recording
        self._tentative = False
        # Läuft das Mikrofon schon (früher Start), wird nur noch bestätigt.
        if not already_running and not self._start_recorder():
            return
        self.state.transition(State.RECORDING)
        log.info("🎤 Recording active — trigger again to stop.")

    def _start_recorder(self) -> bool:
        try:
            self.recorder.start()
        except AudioError as exc:
            log.error("Could not start recording: %s", exc)
            log.error(
                "Check: System Settings → Privacy & Security → Microphone "
                "for the starting app (Terminal, iTerm2, VS Code …)."
            )
            self._to_error()
            return False
        return True

    def _finish_recording(self, *, reason: str = "Hotkey") -> None:
        recording = self.recorder.stop()
        if recording is None:
            log.info("Recording discarded (%s).", reason)
            self.state.transition(State.IDLE)
            return

        self.state.transition(State.PROCESSING)
        log.info(
            "🛑 Recording stopped (%s): %.2fs, peak %.3f.",
            reason,
            recording.duration,
            recording.peak,
        )
        if recording.peak < 0.001:
            log.warning("Audio signal is practically silent — wrong input device?")

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
