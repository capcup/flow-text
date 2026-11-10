# ⚡ FlowText — Local Voice-to-Text Engine for macOS

**FlowText** is an ultra-fast, **100% local voice-to-text dictation application** built specifically for macOS (optimized for Apple Silicon M1/M2/M3/M4 with Unified Memory).

Unlike cloud-based speech-to-text tools, **FlowText runs 100% offline on your Mac**. No audio data, recordings, or transcribed text ever leave your computer.

> 💡 **Standalone & Lightweight out-of-the-box:** FlowText works completely standalone using Apple's MLX Whisper engine (`refiner = "none"`). You do **NOT** need Ollama or any external tools to run dictation!

---

## 🌟 Features

* 🔒 **100% Offline & Private**: Zero cloud APIs. Maximum data privacy.
* 🚀 **Apple Silicon Accelerated**: Uses Apple's `mlx-whisper` framework for fast, GPU/Neural Engine-accelerated transcription.
* ⌨️ **Double-Tap Hotkey**: Simply double-tap the **`⌘` Command key** (or custom shortcuts) anywhere on macOS to start and stop dictation.
* 🤖 **Optional Power-User LLM Refinement**: Optional grammar correction and formatting via local `Ollama` models (disabled by default, no setup required).
* 📋 **Universal Text Injection**: Automatically pastes transcribed text into whatever text field or application you are currently typing in (`Cmd+V`).
* 🖥️ **Menu Bar Integration**: Displays live status (Ready ⚪, Recording 🔴, Processing 🟡, Paused ⏸️) in your macOS menu bar.
* 🛡️ **Sub-Millisecond Engine**: Non-blocking architecture (< 1ms callback latency) with an automated watchdog that keeps keyboard listeners alive.
* 🔐 **Standalone App Bundle**: Build `FlowText.app` in one command so macOS permissions belong to FlowText — not to your Terminal.

---

## 🐣 Beginner's Guide: Step-by-Step Installation

Don't worry if you have never used Terminal, Python, or Homebrew before! Follow these exact steps to get FlowText running on your Mac.

### Step 1: Open Terminal on your Mac
1. Press `Cmd + Space` on your keyboard to open Spotlight.
2. Type **Terminal** and press `Enter`. A black or white text window will open.

### Step 2: Ensure Python 3 is installed
In the Terminal window, paste the following command and press `Enter`:
```bash
python3 --version
```
* If it prints something like `Python 3.10.x` or `Python 3.12.x`, Python is ready!
* If macOS asks you to install **Command Line Developer Tools**, click **Install** and follow the on-screen prompts.

### Step 3: Clone the project & setup
Copy and paste the following commands one line at a time into your Terminal, pressing `Enter` after each line:

```bash
# 1. Download FlowText into your user directory
git clone https://github.com/capcup/flow-text.git

# 2. Enter the project folder
cd flow-text

# 3. Create an isolated Python environment
python3 -m venv .venv

# 4. Activate the environment
source .venv/bin/activate

# 5. Install all required dependencies (or pip install -r requirements.txt)
pip install -e ".[pipeline,menubar,dev]"
```

---

## 🔒 Step 4: Grant macOS System Permissions (Required!)

macOS has strict security settings for reading keyboard shortcuts and accessing the microphone. FlowText requires three permissions in **System Settings**:

1. Click the **Apple logo ()** in the top-left corner of your screen $\rightarrow$ **System Settings**.
2. Click **Privacy & Security** in the left sidebar.
3. Grant the following permissions to your Terminal app (e.g. **Terminal** or **iTerm**):
   * 🎛️ **Accessibility**: Turn ON the switch for **Terminal**.
   * 👁️ **Input Monitoring**: Turn ON the switch for **Terminal**.
   * 🎤 **Microphone**: Turn ON the switch for **Terminal**.

> ⚠️ **CRITICAL STEP:** After toggling these switches, you **MUST completely quit Terminal** by pressing **`Cmd + Q`** while Terminal is open, then re-open Terminal. macOS will not apply keyboard permissions until the terminal app is fully restarted!

---

## 🎯 Step 5: Test & Run FlowText

1. Re-open Terminal and enter the project folder:
   ```bash
   cd flow-text
   source .venv/bin/activate
   ```
2. **Run the Diagnostic Tool** to verify your permissions and microphone:
   ```bash
   python tools/diagnose.py
   ```
   * It will display green checkmarks `✅` for Accessibility, Input Monitoring, and Microphone.

3. **Start FlowText**:
   ```bash
   python -m flowtext
   ```

### 🎙️ How to Dictate:
* **Start Recording:** Double-tap the **`⌘` Command key** (press it twice quickly). You will hear a short sound and see the menu bar icon turn red 🔴. (With `early_start = true` the microphone already opens on the second key *press*, so you can start speaking right away; the sound follows on release. If it turns out not to be a double-tap — e.g. you hold ⌘ or press `⌘V` — the short recording is discarded silently.)
* **Speak:** Dictate your text into your microphone.
* **Stop Recording:** Double-tap the **`⌘` Command key** again. FlowText will transcribe your speech and automatically paste the text into your active app!

---

## 🖥️ Step 6: Run FlowText from the Menu Bar (Recommended)

Instead of keeping a Terminal window open, FlowText can live in your menu bar:

```bash
python -m flowtext --menubar
```

The icon shows you what FlowText is doing at a glance:

| Icon | Meaning |
| :---: | :--- |
| 🎙 | Ready and waiting |
| 🔴 | Recording |
| ⏳ | Transcribing |
| ⏸ | Paused |
| 🔒 | Permissions missing — use the menu to grant them |
| ⚠️ | Error — check the log |

Click the icon for: pause/resume, open `config.toml`, open the log file, quit.

---

## 🔐 Step 7: Free Your Terminal (Optional but Recommended)

In Step 4 you gave **Terminal** permission to watch your keyboard. That permission
applies to *everything* you ever run from that Terminal — not just FlowText.

You can fix that by turning FlowText into its own small macOS app:

```bash
python tools/make_app.py     # creates dist/FlowText.app
open dist/FlowText.app
```

The menu bar icon appears right away. On a fresh app it shows **🔒 "Berechtigungen
fehlen"** — that's expected: a brand-new app has no permissions yet. Use the menu
entries **"Bedienungshilfen freigeben …"** and **"Eingabeüberwachung freigeben …"** to
jump straight to the right settings pane, then add `FlowText.app` there. The lock
disappears within a couple of seconds — no restart needed.

Once it works, go back to **System Settings → Privacy & Security** and switch those
permissions **off for your Terminal**. FlowText keeps working — your Terminal no longer
has access to your keystrokes.

> 💡 **Nothing gets copied.** The app is just a few small files pointing at your existing
> `.venv`. The 2 GB of model data stays where it is, and code changes take effect
> immediately — no rebuild needed.

**Autostart:** drag `FlowText.app` into **System Settings → General → Login Items**.

<details>
<summary>What's inside the app bundle, and why</summary>

```
FlowText.app/Contents/
├── Info.plist        Bundle ID, LSUIElement, microphone reason
└── MacOS/
    ├── FlowText      tiny native arm64 binary (CFBundleExecutable)
    ├── python3       copy of your interpreter's launcher binary (~10 kB)
    ├── pyvenv.cfg    makes that copy a valid virtual environment
    └── launch.sh     fallback only: zsh script → ./python3 -m flowtext --menubar
```

| Detail | Why it matters |
| :--- | :--- |
| `NSMicrophoneUsageDescription` | Without this key, macOS silently kills the app on first microphone access |
| `LSUIElement = true` | Keeps a Dock icon from appearing next to the menu bar icon |
| Ad-hoc signature (`codesign -s -`) | Without a signature, macOS identifies the app by content hash and forgets your permission on every rebuild |
| **Native binary entry point** | macOS cannot read a CPU architecture from a text file. With a shell script as the entry point it assumes x86_64 and demands Rosetta — launch fails with **error -10669** |
| Unresolved `.venv` symlink | `.venv/bin/python3` is a symlink to your base interpreter. Resolving it bypasses the virtual environment and no dependency is found |
| **Interpreter must be a real file inside the bundle** | AppKit derives `[NSBundle mainBundle]` from the *resolved* path of the running executable. Pointing straight at `.venv/bin/python3` puts the process outside `FlowText.app` after `exec` — and a symlink is no better, because `realpath` follows it back out to your base interpreter. Either way the app loses its bundle identity and `NSStatusBar` draws **no menu bar icon**. So the launcher binary is *copied* in, with a `pyvenv.cfg` beside it (exactly what `python -m venv --copies` produces) and dependencies supplied via `PYTHONPATH` |
| **No `exec` in the entry point (macOS 26)** | If the entry binary replaces itself via `exec` (stub → `zsh` → Python), macOS 26 draws **no menu bar icon** even though the app runs fine. So the stub *embeds* Python: it `dlopen`s `libpython` and calls `Py_BytesMain`, and the process stays the `FlowText` binary throughout. This needs a Python built with a shared library (`--enable-shared`); `make_app.py` self-tests the result and otherwise falls back to the `exec` chain and tells you |

`tools/make_app.py` handles all of these. It verifies up front that the chosen
interpreter can import FlowText and its dependencies, and afterwards that the *copy
inside the finished bundle* still starts. If you rebuild the app, macOS may reset the
permission — just approve it once more.

> **Rebuild after a Python update.** The bundle tracks your source code live, but the
> interpreter is a copy. Upgrade Python or recreate `.venv`, and you need to run
> `python tools/make_app.py` again.

If the app doesn't launch, the reason is in `~/Library/Logs/FlowText/launcher.log`.
</details>

---

## ⚙️ Configuration (`config.toml`)

FlowText can be customized by editing the `config.toml` file in the project folder. You can open it in any text editor.

```toml
[hotkey]
mode = "double_tap"       # Options: "double_tap" or "combo"
tap_key = "cmd"           # Options: "cmd", "shift_r", "ctrl_r", "alt_l"
double_tap_window = 0.40  # Time allowed between two taps (seconds)
max_tap_duration = 0.35   # Maximum tap hold duration
early_start = true        # Open the mic on the 2nd key *press* so the first words aren't cut off

[audio]
sample_rate = 16000
max_seconds = 300.0        # Maximum recording length (5 minutes)
min_seconds = 0.3          # Recordings shorter than 0.3s are ignored

[pipeline]
transcriber = "mlx"       # Uses mlx-whisper (or "stub" for dry-runs)
whisper_model = "mlx-community/whisper-large-v3-turbo"
language = "de"           # Language code ("de", "en", "fr", "es", etc.)
refiner = "none"          # Standalone default ("none"). Set to "ollama" if desired.
injector = "clipboard"    # Copies text and performs Cmd+V
```

### Recommended Whisper Models

| Model Name | Download Size | Best For |
| :--- | :--- | :--- |
| `mlx-community/whisper-large-v3-turbo` | ~1.5 GB | **Default** — Best accuracy, fast on Apple Silicon M-series |
| `mlx-community/whisper-small-mlx` | ~0.5 GB | Recommended if RAM is low |
| `mlx-community/whisper-tiny` | ~0.1 GB | Testing / low hardware usage |

---

## 🤖 Optional Power-User Feature: Local LLM Refinement (Ollama)

FlowText works 100% standalone out-of-the-box (`refiner = "none"`). Installing Ollama is **completely optional**.

If you wish to automatically clean up filler words ("um", "uh"), fix complex grammar, or reformat speech into bullet points using a local LLM:

1. Download and install [Ollama](https://ollama.com).
2. Download a model in your terminal:
   ```bash
   ollama pull qwen2.5:7b
   ```
3. Set `refiner = "ollama"` in your `config.toml`.

> 🛡️ **Fail-Safe Guarantee:** If Ollama is enabled but not running or unresponsive, FlowText automatically falls back to raw Whisper transcription — your dictation will never fail or drop text.

---

## 🛠️ Troubleshooting & FAQ

| Problem | Cause | Solution |
| :--- | :--- | :--- |
| **Double-tap does nothing** | Missing macOS Accessibility or Input Monitoring permissions. | Run `python -m flowtext --watch-keys` to test. Ensure Terminal is enabled in **System Settings $\rightarrow$ Privacy & Security**, then press **`Cmd + Q`** to restart Terminal. |
| **Hotkey triggers another app** | Shortcut conflict (e.g., Claude for Mac uses double-tap Option). | Default is set to `cmd` (Command key). You can change `tap_key` in `config.toml` to `shift_r` or `ctrl_r`. |
| **Recording starts, but output is empty** | Microphone permission missing or wrong audio input device. | Run `python tools/diagnose.py` to test your microphone input level. |
| **First dictation takes long** | Whisper model downloading on first launch (~1.5 GB). | FlowText warms up the model automatically during startup. |
| **Do I need Ollama installed?** | No! | FlowText runs 100% standalone with Whisper by default (`refiner = "none"`). |
| **I don't want Terminal to read my keystrokes** | Permissions belong to whichever app starts FlowText. | Build the standalone app: `python tools/make_app.py` (see Step 7), then switch the permissions off for Terminal. |
| **App bundle won't start** | Wrong project path, or the `.venv` moved. | Check `~/Library/Logs/FlowText/launcher.log`, then rebuild with `python tools/make_app.py`. |
| **App blocked by Gatekeeper (`spctl: rejected`)** | macOS blocks ad-hoc signed `.app` bundles from running via LaunchServices / Finder. | Run `xattr -cr dist/FlowText.app && codesign --force --deep --sign - dist/FlowText.app && spctl --add dist/FlowText.app` to clear quarantine and authorize. |
| **`error -10669` when opening the app** | Missing Command Line Tools, so no native entry point could be compiled — macOS then wants Rosetta. | `xcode-select --install`, then rebuild. Fallback: `softwareupdate --install-rosetta --agree-to-license` |

---

## 🧪 Developer Testing & Verification

FlowText comes with a comprehensive test suite of 223 automated tests that run without requiring real microphone input or active macOS GUI permissions:

```bash
# Run test suite
pytest

# Code formatting and linting
ruff check . && ruff format --check .
mypy --strict
```

---

## 📄 License & Credits

Built with ❤️ for Apple Silicon Mac users. 
* Speech Recognition powered by [Apple MLX Whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper).
* Text Refinement powered by [Ollama](https://ollama.com).
