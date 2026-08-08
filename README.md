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

# 5. Install all required dependencies
pip install -r requirements.txt
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
* **Start Recording:** Double-tap the **`⌘` Command key** (press it twice quickly). You will hear a short sound and see the menu bar icon turn red 🔴.
* **Speak:** Dictate your text into your microphone.
* **Stop Recording:** Double-tap the **`⌘` Command key** again. FlowText will transcribe your speech and automatically paste the text into your active app!

---

## ⚙️ Configuration (`config.toml`)

FlowText can be customized by editing the `config.toml` file in the project folder. You can open it in any text editor.

```toml
[hotkey]
mode = "double_tap"       # Options: "double_tap" or "combo"
tap_key = "cmd"           # Options: "cmd", "shift_r", "ctrl_r", "alt_l"
double_tap_window = 0.40  # Time allowed between two taps (seconds)
max_tap_duration = 0.35   # Maximum tap hold duration

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

---

## 🧪 Developer Testing & Verification

FlowText comes with a comprehensive test suite of 173 automated tests that run without requiring real microphone input or active macOS GUI permissions:

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
