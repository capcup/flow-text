# PROJECT PLAN: FlowText (Local Voice-to-Text Engine)

## 1. Project Purpose & Vision
**FlowText** is an ultra-fast, 100% local voice-to-text and dictation application for macOS (optimized for Apple Silicon / M-series chips, specifically Mac Mini M4 with 16 GB RAM).
Similar to *Superwhisper*, FlowText enables seamless voice dictation into any application via a global keyboard shortcut.

### Core Principles:
* **Zero Cloud Dependency**: No data ever leaves your local Mac. Maximum privacy.
* **Apple Silicon Native Performance**: Uses Apple's MLX Framework (`mlx-whisper`) for hardware-accelerated inference on Neural Engine / GPU cores.
* **Modular Event Architecture**: Decoupling audio recording, STT transcription, LLM refinement, and output injection.

---

## 2. Tech Stack (100% Local)

| Component | Library / Tool | Purpose |
| :--- | :--- | :--- |
| **Hotkey System** | `pynput` | Global system hotkeys under macOS (Toggle Start/Stop) |
| **Audio I/O** | `sounddevice` | Microphone stream & buffer handling |
| **Audio Storage** | `soundfile` | Export audio data as uncompressed `.wav` files |
| **STT Engine** | `mlx-whisper` | Apple's MLX inference engine for Whisper (`whisper-large-v3-turbo` / medium) |
| **LLM Engine** | `ollama` (Python SDK) | Local text refinement & punctuation correction via Ollama (e.g., `qwen2.5:7b` / `llama3.2`) |
| **System Injection**| `pyperclip`, `pynput` | Write transcription to clipboard and trigger automated paste (`Cmd+V`) |

---

## 3. Modular Architecture

```
+-------------------+      Hotkey Event      +-----------------------+
|  pynput Hotkey    | ---------------------> | AudioRecorder Module  |
|  (Global Listener)|                        | (sounddevice/soundfile|
+-------------------+                        +-----------------------+
                                                         |
                                             Recording Finished Event
                                             (temp_audio.wav path)
                                                         v
                                             +-----------------------+
                                             |  Event Hook Handler   |
                                             +-----------------------+
                                                         |
                                 +-----------------------+-----------------------+
                                 | (Future Step 2)                               | (Future Step 2)
                                 v                                               v
                     +-----------------------+                       +-----------------------+
                     | MLX-Whisper Engine    |                       | Ollama LLM Service    |
                     +-----------------------+                       +-----------------------+
                                                                                 |
                                                                                 v
                                                                     +-----------------------+
                                                                     | Output Injector       |
                                                                     | (pyperclip/pynput)    |
                                                                     +-----------------------+
```

---

## 4. Iterative Roadmap

### Version 1.0 (MVP Step 1) - Completed
- [x] Basic project structure & `.venv` setup
- [x] `PROJECT_PLAN.md` & `requirements.txt`
- [x] Background loop & `pynput` global hotkey listener
- [x] Audio recording stream via `sounddevice` with toggle logic
- [x] Storage of temporary audio files via `soundfile`
- [x] Modular event callback interface prepared for STT pipeline

### Version 1.1 (Stabilization & Modularization) - Completed
> Details & Rationale: [`ANALYSE_UND_UMSETZUNGSPLAN.md`](ANALYSE_UND_UMSETZUNGSPLAN.md)

- [x] Git repository, `.gitignore`, `pyproject.toml`
- [x] Non-blocking hotkey callback (avoid event tap timeout)
- [x] Supervisor thread with `join()` — no silent listener deaths
- [x] Collision-free trigger: double-tap Command key instead of `Ctrl+Option+Space`
- [x] Correct permissions check (`c_bool` restype **and** `IOHIDCheckAccess`)
- [x] Deadlock-free `AudioRecorder` (queue instead of lock inside audio callback)
- [x] Min/max recording duration, device error handling, temp files in Application Support
- [x] Package structure `flowtext/`, state machine, `config.toml`
- [x] Audio feedback, rotating log file
- [x] `tools/diagnose.py`, automated tests, `ruff` + `mypy --strict`
- [x] Conflict detection for occupied triggers (Claude for Mac, macOS system shortcuts)
- [x] Event watchdog + `--watch-keys`: missing permissions report themselves
- [x] Centralized diagnostics in `flowtext/diagnostics.py`, identifying responsible app

### Version 1.3 (MVP Step 2) - Completed
- [x] Integration of `mlx-whisper` (load model during warmup, fixed language setting)
- [x] Connection of local `ollama` library for text formatting
      (reachability check on startup, fallback to raw transcript)
- [x] Text injection into active window via `pyperclip` + `pynput` (`Cmd+V`),
      including saving and restoring clipboard state
- [x] System sound audio notifications (system sounds via `afplay`)

### Version 1.4 - UX & Performance Refinements
- [x] Menu bar app via `rumps` with status icon, pause, and quick actions
- [x] `FlowText.app` launcher bundle (`tools/make_app.py`): standalone TCC identity,
      eliminating the need to give Terminal Accessibility/Input Monitoring rights
- [x] **Test Requirement:** Gatekeeper & LaunchServices validation (`spctl -a -vvv -t exec dist/FlowText.app` passed; launch via `open dist/FlowText.app` or Finder without block)
- [ ] Push-to-Talk as an alternative to toggle mode
- [ ] Stream/chunk-based buffer management
- [ ] Dynamic Whisper model selection (`tiny`, `base`, `small`, `large-v3-turbo`)
- [ ] Configurable system prompts for Ollama (e.g. "Format as bullet points", "Correct grammar")

---

## 5. Important macOS Permissions (Accessibility & Microphone)

Due to macOS security architecture, Python / Terminal requires the following permissions in System Settings (**System Settings -> Privacy & Security**):

1. **Accessibility**: Required for `pynput` to listen to global hotkeys and to simulate `Cmd+V`.
2. **Microphone**: Required for `sounddevice` to capture microphone input.
3. **Input Monitoring**: **Mandatory**, not optional. Session-level event taps require `kTCCServiceListenEvent`; `AXIsProcessTrusted()` alone does not cover this.

**Important:** Permission belongs to the *launching app* (Terminal.app, iTerm2, VS Code, Cursor) — not to the Python binary itself. After granting permissions, completely quit the app with **`Cmd+Q`** and restart it. `python tools/diagnose.py` checks all three permissions and reports what is missing.

**Recommended approach since v1.4:** `python tools/make_app.py` generates `FlowText.app`. This gives FlowText its *own* TCC identity — permissions then apply directly to FlowText, allowing Terminal to revoke its access. The bundle packs no external copies; it simply references your existing `.venv`.
