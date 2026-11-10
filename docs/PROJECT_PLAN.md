# PROJECT PLAN: FlowText (Local Voice-to-Text Engine)

> This file replaces the earlier `PROJECT_PLAN.md` and
> `ANALYSE_UND_UMSETZUNGSPLAN.md` (deleted 2026-08-10). Those documents
> tracked the project from a broken 289-line prototype through the fixes
> that made it work — that work is done and the history lives in `git log`.
> This file describes the **current** system and its **current** roadmap
> only. Known tooling/housekeeping issues live separately in `FIXES.md`.

## 1. Vision & Principles

**FlowText** is an ultra-fast, 100% local voice-to-text and dictation
application for macOS (Apple Silicon), enabling seamless voice dictation
into any application via a global keyboard shortcut.

- **Zero Cloud Dependency** — no audio or text ever leaves the Mac.
- **Apple Silicon Native** — `mlx-whisper` for hardware-accelerated inference.
- **Modular Event Architecture** — recording, transcription, refinement, and
  injection are decoupled stages behind `Protocol` interfaces.

## 2. Tech Stack

| Component | Library | Purpose |
| :--- | :--- | :--- |
| Hotkey | `pynput` | Global macOS event tap (double-tap / combo trigger) |
| Audio I/O | `sounddevice` | Microphone stream & buffering |
| Audio Storage | `soundfile` | Optional WAV dump for debugging (audio stays in memory otherwise) |
| STT | `mlx-whisper` | Apple MLX Whisper inference |
| LLM refinement (optional) | `ollama` | Local punctuation/grammar cleanup |
| Injection | `pyperclip` + `pynput` | Clipboard + simulated `Cmd+V` |
| Menu bar | `rumps` | Status icon UI |

## 3. Architecture

```
flowtext/
├── __main__.py          CLI entry point, argument parsing
├── app.py                FlowTextApp — wires hotkey, recorder, pipeline
├── state.py               State enum + thread-safe StateMachine
├── config.py               dataclasses, TOML loading, validation
├── permissions.py           Accessibility / Input Monitoring checks
├── diagnostics.py            shared logic for --watch-keys / tools/diagnose.py
├── feedback.py                 sound feedback (afplay)
├── logging_setup.py             console + rotating file logging
├── menubar.py                    rumps status-bar UI
├── bundle.py                      FlowText.app launcher-bundle builder
├── audio/
│   ├── recorder.py                deadlock-free AudioRecorder (queue-based)
│   └── devices.py                  input device listing
├── hotkey/
│   ├── service.py                   HotkeyService: event tap + supervisor
│   ├── detectors.py                  DoubleTapDetector, ComboDetector (pure logic)
│   └── keynames.py                    pynput key → canonical name
└── pipeline/
    ├── base.py                        Transcriber / Refiner / Injector protocols
    ├── factory.py                      builds the pipeline from config
    ├── transcribe_mlx.py                mlx-whisper transcription
    ├── refine_ollama.py                  Ollama-based text refinement
    ├── inject_macos.py                    clipboard + Cmd+V injection
    └── stub.py                             no-op stages for dry runs / tests
```

### Threading model

The one rule that matters: **the event-tap callback must return in under
1 ms.** macOS permanently disables a `CGEventTap` that's too slow to
respond, and pynput never re-enables it.

```
Event-Tap Thread    → queue.put_nowait only              (< 1 ms, hard rule)
Controller Thread    → state machine, start/stop recording
Worker Thread          → transcription, LLM, injection    (may take seconds)
Supervisor Thread        → restarts the listener if it dies
PortAudio Callback         → queue.put_nowait only        (no locks)
```

### State machine

```
IDLE ─(trigger)→ RECORDING ─(trigger|timeout)→ PROCESSING ─→ IDLE
                                                    │
                                                 ERROR ─→ IDLE
```

Triggers in any state other than `IDLE`/`RECORDING` are dropped
(single-flight — no overlapping recordings or pipeline runs).

## 4. macOS Permissions

FlowText needs two TCC permissions, granted to whichever app *launches* it
(Terminal, or `FlowText.app` if built via `tools/make_app.py`):

1. **Accessibility** — required for `pynput` to listen globally and simulate
   `Cmd+V`.
2. **Input Monitoring** — mandatory for the session-level event tap
   (`kTCCServiceListenEvent`); `AXIsProcessTrusted()` alone doesn't cover it.
3. **Microphone** — required for `sounddevice`.

`python tools/diagnose.py` checks all three and reports what's missing.
Recommended: `python tools/make_app.py` builds `FlowText.app`, giving
FlowText its own TCC identity so Terminal doesn't need these permissions.

## 5. Current Status (2026-08-10)

The core pipeline (hotkey → record → transcribe → optionally refine → inject)
and the menu-bar / app-bundle infrastructure are **complete and stable**.
223 automated tests, `ruff` and `mypy --strict` clean. No feature work is
currently in flight — the previous roadmap
(push-to-talk, streaming, model selection, prompt profiles, v2.0 items) was
discarded on 2026-08-10 to make room for a fresh brainstorm rather than
carrying forward an unreviewed backlog.

CI is green on both Python 3.11 and 3.12. There are no open housekeeping
items; `FIXES.md` keeps the record of what was fixed and why.

## 6. Roadmap

Open work lives in [`IMPROVEMENTS.md`](IMPROVEMENTS.md): a prioritised
backlog from the 2026-10-27 review with on-device latency/quality
measurements and a recommended order. Start there.
