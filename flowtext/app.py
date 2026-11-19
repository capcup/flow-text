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
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import preferences
from .audio.recorder import AudioArray, AudioError, AudioRecorder, Recording
from .audio.segmenter import Segmenter
from .config import Config, temp_dir
from .feedback import Feedback
from .hotkey.detectors import Signal
from .hotkey.service import HotkeyService
from .pipeline.base import Pipeline
from .pipeline.factory import build_cleaner, build_pipeline
from .pipeline.guard import speech_level
from .state import State, StateMachine

log = logging.getLogger(__name__)

#: Behaltene Aufnahmen (``audio.delete_after_use = false``) werden nach
#: dieser Zeit beim Start entfernt.
_STALE_TEMP_AGE_SECONDS = 24 * 3600

#: Sicherheitsnetz für den frühen Start: bleibt eine vorläufig gestartete
#: Aufnahme länger ohne Bestätigung/Abbruch offen (z. B. weil ein Ereignis
#: verloren ging), wird sie verworfen. Deutlich über ``max_tap_duration``.
_TENTATIVE_TIMEOUT_SECONDS = 2.0

#: Pausenpegel der Abschnittserkennung, wenn ``pipeline.silence_threshold = 0``.
_DEFAULT_PAUSE_LEVEL = 0.005

#: So viel vom bisherigen Text geht als Kontext an den nächsten Abschnitt.
_CONTEXT_CHARS = 200


@dataclass(frozen=True)
class Notice:
    """Kurzlebige Meldung für die Oberfläche (Fehler oder Hinweis).

    Der Zustand ``ERROR`` dauert nur Mikrosekunden — die Menüleiste, die alle
    200 ms nachsieht, bekäme ihn nie zu Gesicht. Die Meldung bleibt deshalb
    getrennt vom Zustand stehen, bis sie abläuft oder ein neues Diktat beginnt.
    """

    message: str
    is_error: bool = True
    at: float = field(default_factory=time.monotonic)


@dataclass
class StreamRun:
    """Zustand eines Diktats im Abschnittsmodus.

    Wird beim Start der Aufnahme angelegt und danach nur vom Stream-Worker
    verändert, der die Abschnitte der Reihe nach abarbeitet.
    """

    #: Bisher eingefügter Text.
    text: str = ""
    #: Erster Fehler eines Abschnitts, falls einer scheiterte.
    failed: str | None = None


@dataclass(frozen=True)
class SegmentJob:
    run: StreamRun
    #: ``None`` = nichts mehr zu transkribieren (nur der Abschluss).
    recording: Recording | None
    final: bool = False
    #: Die komplette Aufnahme, nur beim letzten Abschnitt (für die Fehlersuche).
    full: Recording | None = None


class FlowTextApp:
    def __init__(
        self,
        cfg: Config,
        *,
        recorder: AudioRecorder | None = None,
        hotkey: HotkeyService | None = None,
        pipeline: Pipeline | None = None,
        prefs: preferences.Preferences | None = None,
    ) -> None:
        self.cfg = cfg
        self._prefs = prefs
        self.state = StateMachine()
        self.events: queue.Queue[str] = queue.Queue(maxsize=8)
        self.jobs: queue.Queue[Recording] = queue.Queue(maxsize=4)
        #: Abschnitte der laufenden Aufnahme (nur bei ``streaming.enabled``).
        self.segments: queue.Queue[SegmentJob] = queue.Queue(maxsize=8)

        self.recorder = recorder or AudioRecorder(cfg.audio)
        self.hotkey = hotkey or HotkeyService(cfg.hotkey, self.events)
        self.pipeline = pipeline or build_pipeline(cfg.pipeline, cfg.cleanup)
        self.feedback = Feedback(cfg.feedback)
        self.state.subscribe(self.feedback.on_state_change)
        self.state.subscribe(self._log_state)

        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._paused = False
        #: Nur im Controller-Thread: Abschnittserkennung des laufenden Diktats.
        self._segmenter: Segmenter | None = None
        self._stream_run: StreamRun | None = None
        #: Letzte Meldung für die Oberfläche; wird aus Controller- und
        #: Worker-Thread gesetzt, vom Hauptthread nur gelesen.
        self.notice: Notice | None = None
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
        # Immer starten: die Option lässt sich zur Laufzeit einschalten, der
        # wartende Thread kostet nichts.
        self._threads.append(
            threading.Thread(target=self._stream_loop, name="stream-worker", daemon=True)
        )
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

    # ----------------------------------------------------------- Einstellungen

    def set_option(self, section: str, key: str, value: Any, *, persist: bool = True) -> None:
        """Ändert eine Einstellung des Settings-Fensters — wirkt ab dem nächsten Diktat.

        Läuft im Hauptthread (Cocoa). Die betroffenen Stufen werden durch
        Austausch einer Referenz umgestellt, das ist gegenüber dem Worker-Thread
        unkritisch: ein laufendes Diktat nutzt noch den alten Stand.
        """
        option = preferences.find(section, key)
        value = preferences.check(option, value)
        preferences.set_value(self.cfg, option, value)

        if (section, key) == ("cleanup", "remove_fillers"):
            self.pipeline.cleaner = build_cleaner(self.cfg.pipeline, self.cfg.cleanup)
        elif (section, key) == ("pipeline", "vocabulary"):
            apply = getattr(self.pipeline.transcriber, "set_vocabulary", None)
            if apply is not None:
                apply(value)

        log.info("Setting %s.%s = %r", section, key, value)
        if persist:
            if self._prefs is None:
                self._prefs = preferences.Preferences()
            try:
                self._prefs.save(option, value)
            except OSError as exc:
                log.warning("Einstellung konnte nicht gespeichert werden: %s", exc)

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
            self._drop_streaming()
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
                self._poll_streaming()
                self._check_max_duration()
                self._check_tentative_timeout()
                continue
            try:
                self._on_signal(event)
            except Exception:
                log.exception("Unexpected error in controller.")
                self._to_error("Internal error — see log")

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
        self.notice = None  # neue Aufnahme: alte Meldung ist erledigt
        self._start_streaming()
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
            self._to_error("Microphone could not be opened")
            return False
        return True

    def _finish_recording(self, *, reason: str = "Hotkey") -> None:
        recording = self.recorder.stop()
        run, segmenter = self._stream_run, self._segmenter
        self._drop_streaming()
        if recording is None:
            log.info("Recording discarded (%s).", reason)
            self.state.transition(State.IDLE)
            return

        self.state.transition(State.PROCESSING)
        log.info(
            "🛑 Recording stopped (%s): %.2fs, peak %.3f, speech level %.4f.",
            reason,
            recording.duration,
            recording.peak,
            speech_level(recording),
        )
        if recording.peak < 0.001:
            log.warning("Audio signal is practically silent — wrong input device?")

        if run is not None and segmenter is not None:
            self._finish_streaming(run, segmenter, recording)
            return

        # Die Samples gehen direkt aus dem Speicher an die Pipeline — keine
        # Datei, kein ffmpeg.
        try:
            self.jobs.put_nowait(recording)
        except queue.Full:
            log.error("Verarbeitungswarteschlange voll — Aufnahme verworfen.")
            self._to_error("Still busy — recording discarded")

    # ------------------------------------------------ abschnittsweise Ausgabe

    def _start_streaming(self) -> None:
        cfg = self.cfg.streaming
        if not cfg.enabled:
            return
        self._segmenter = Segmenter(
            self.cfg.audio.sample_rate,
            level=self.cfg.pipeline.silence_threshold or _DEFAULT_PAUSE_LEVEL,
            pause_seconds=cfg.pause_seconds,
            min_seconds=cfg.min_segment_seconds,
            max_seconds=cfg.max_segment_seconds,
        )
        self._stream_run = StreamRun()

    def _drop_streaming(self) -> None:
        self._segmenter = None
        self._stream_run = None

    def _poll_streaming(self) -> None:
        """Gibt fertige Abschnitte der laufenden Aufnahme an den Stream-Worker."""
        run, segmenter = self._stream_run, self._segmenter
        if run is None or segmenter is None or self.state.current is not State.RECORDING:
            return
        new = self.recorder.take_new()
        if new is None:
            return
        for samples in segmenter.feed(new):
            self._submit_segment(run, samples)

    def _finish_streaming(self, run: StreamRun, segmenter: Segmenter, recording: Recording) -> None:
        """Beim Stopp: den noch offenen Rest als letzten Abschnitt abschicken."""
        rest = self.recorder.take_new()
        if rest is not None:
            for samples in segmenter.feed(rest):
                self._submit_segment(run, samples)
        self._submit_segment(run, segmenter.flush(), final=True, full=recording)

    def _submit_segment(
        self,
        run: StreamRun,
        samples: AudioArray,
        *,
        final: bool = False,
        full: Recording | None = None,
    ) -> None:
        recording = None
        if len(samples) > 0:
            recording = Recording(samples=samples, sample_rate=self.cfg.audio.sample_rate)
        job = SegmentJob(run, recording, final=final, full=full)
        try:
            self.segments.put(job, timeout=1.0)
        except queue.Full:
            log.error("Abschnittswarteschlange voll — Abschnitt verworfen.")
            run.failed = run.failed or "Still busy — part of the dictation was dropped"
            if final:
                self._to_error(run.failed)

    def _stream_loop(self) -> None:
        while not self._stop.is_set():
            try:
                job = self.segments.get(timeout=0.2)
            except queue.Empty:
                continue
            self._process_segment(job)

    def _process_segment(self, job: SegmentJob) -> None:
        run = job.run
        if job.recording is not None:
            prefix = " " if run.text and not run.text[-1].isspace() else ""
            try:
                text = self.pipeline.run(
                    job.recording, context=run.text[-_CONTEXT_CHARS:], prefix=prefix
                )
            except Exception:
                log.exception("Verarbeitung eines Abschnitts fehlgeschlagen.")
                run.failed = run.failed or "Transcription failed — see log"
                if not job.final:
                    # Die Aufnahme läuft noch: nur melden, den Zustand nicht anfassen.
                    self.notice = Notice(run.failed)
            else:
                if text:
                    run.text += prefix + text
        if job.final:
            self._finish_stream_run(job, run)

    def _finish_stream_run(self, job: SegmentJob, run: StreamRun) -> None:
        if job.full is not None and not self.cfg.audio.delete_after_use:
            self._keep_recording(job.full, run.text)
        if run.failed:
            self._to_error(run.failed)
            return
        if not run.text:
            self.notice = Notice("No speech detected — nothing inserted", is_error=False)
        self.state.transition(State.IDLE)

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
                recording = self.jobs.get(timeout=0.2)
            except queue.Empty:
                continue
            self._process(recording)

    def _process(self, recording: Recording) -> None:
        text = ""
        try:
            text = self.pipeline.run(recording)
        except Exception:
            log.exception("Verarbeitung fehlgeschlagen.")
            self._to_error("Transcription failed — see log")
        else:
            if not text:
                # Kein Fehler, aber ohne Hinweis wirkt es wie "tut nichts".
                self.notice = Notice("No speech detected — nothing inserted", is_error=False)
            self.state.transition(State.IDLE)
        finally:
            if not self.cfg.audio.delete_after_use:
                self._keep_recording(recording, text)

    def _keep_recording(self, recording: Recording, text: str) -> None:
        """Legt Aufnahme und Transkript ab — nur zur Fehlersuche und als
        Material für ``tools/bench.py``, nie im Normalbetrieb."""
        try:
            path = self.recorder.save(recording, self._new_temp_path())
            path.with_suffix(".txt").write_text(text + "\n", encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 — auch ImportError (soundfile fehlt)
            log.warning("Aufnahme konnte nicht abgelegt werden: %s", exc)
            return
        log.info("Aufnahme behalten: %s", path)

    # --------------------------------------------------------------- Helfer

    def _to_error(self, message: str = "Error — see log") -> None:
        self.notice = Notice(message)
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
        for path in directory.glob("recording-*.*"):
            try:
                if now - path.stat().st_mtime > _STALE_TEMP_AGE_SECONDS:
                    path.unlink()
            except OSError:
                pass
