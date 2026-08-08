# PROJECT PLAN: FlowText (Local Voice-to-Text Engine)

## 1. Projektzweck & Vision
**FlowText** ist eine extrem schnelle, 100% lokale Voice-to-Text- und Diktat-Applikation für macOS (optimiert für Apple Silicon / M-Chips, speziell Mac Mini M4 mit 16 GB RAM).
Ähnlich wie *Superwhisper* ermöglicht FlowText nahtlose Spracheingabe in jede Anwendung durch ein globales Tastenkürzel.

### Kernprinzipien:
* **Zero Cloud Dependency**: Keine Daten verlassen jemals den lokalen Mac. Maximale Privatsphäre.
* **Apple Silicon Native Performance**: Nutzung von MLX Framework (`mlx-whisper`) für Hardware-beschleunigte Inferenz auf den Unified Memory Cores der Neural Engine / GPU.
* **Modulare Event-Architektur**: Entkopplung von Audioaufnahme, STT-Transkription, LLM-Nachbearbeitung und Output-Injektion.

---

## 2. Tech Stack (100% Lokal)

| Komponente | Bibliothek / Tool | Zweck |
| :--- | :--- | :--- |
| **Hotkey System** | `pynput` | Globale System-Hotkeys unter macOS (Toggle Start/Stop) |
| **Audio I/O** | `sounddevice` | Mikrofon-Stream & Buffer-Handling |
| **Audio Storage** | `soundfile` | Export der Audiodaten als unkomprimierte `.wav`-Dateien |
| **STT Engine** | `mlx-whisper` | Apples MLX-Inferenz-Engine für Whisper (Whisper-large-v3-turbo / medium) |
| **LLM Engine** | `ollama` (Python SDK) | Lokale Text-Verfeinerung & Interpunktions-Korrektur über Ollama (z. B. `qwen2.5:7b` / `llama3.2`) |
| **System Injection**| `pyperclip`, `pyautogui` | Schreiben der Transkription in Clipboard und automatisiertes Einfügen (`Cmd+V`) |

---

## 3. Modulare Architektur

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
                                                                     | (pyperclip/pyautogui) |
                                                                     +-----------------------+
```

---

## 4. Iterative Roadmap

### Version 1.0 (MVP Step 1) - Abgeschlossen
- [x] Grundlegende Projektstruktur & `.venv` Setup
- [x] `PROJECT_PLAN.md` & `requirements.txt`
- [x] Background-Loop & `pynput` Global Hotkey Listener
- [x] Audio-Aufnahme-Stream via `sounddevice` mit Toggle-Logik
- [x] Speicherung temporärer Audio-Dateien via `soundfile`
- [x] Modulare Event-Callback-Schnittstelle für STT-Pipeline vorbereiten

### Version 1.1 (Stabilisierung & Modularisierung) - Current Phase
> Details und Begründungen: [`ANALYSE_UND_UMSETZUNGSPLAN.md`](ANALYSE_UND_UMSETZUNGSPLAN.md)

- [x] Git-Repository, `.gitignore`, `pyproject.toml`
- [x] Nicht-blockierender Hotkey-Callback (Event-Tap-Timeout vermeiden)
- [x] Supervisor-Thread mit `join()` — kein stiller Listener-Tod mehr
- [x] Kollisionsfreier Auslöser: Doppel-Tap rechte Option statt `Ctrl+Option+Space`
- [x] Korrekte Berechtigungsprüfung (`c_bool`-Restype **und** `IOHIDCheckAccess`)
- [x] Deadlockfreier `AudioRecorder` (Queue statt Lock im Audio-Callback)
- [x] Min-/Max-Aufnahmedauer, Geräte-Fehlerbehandlung, temporäre Dateien in Application Support
- [x] Paketstruktur `flowtext/`, Zustandsautomat, `config.toml`
- [x] Akustisches Feedback, rotierendes Logfile
- [x] `tools/diagnose.py`, automatisierte Tests, `ruff` + `mypy --strict`
- [x] Konflikterkennung für belegte Auslöser (Claude für Mac, macOS-Systemkurzbefehle)
- [x] Event-Watchdog + `--watch-keys`: fehlende Berechtigungen melden sich selbst
- [x] Diagnose zentral in `flowtext/diagnostics.py`, benennt die freizugebende App

### Version 1.3 (MVP Step 2) - Abgeschlossen
- [x] Integration von `mlx-whisper` (Modell im Warmup laden, feste Sprache)
- [x] Anbindung der lokalen `ollama`-Library zur Text-Formatierung
      (Erreichbarkeitsprüfung beim Start, Fallback auf das Rohtranskript)
- [x] Text-Injektion in das aktive Fenster via `pyperclip` + `pynput` (`Cmd+V`),
      inkl. Sichern und Wiederherstellen der Zwischenablage
- [x] Akustische System-Benachrichtigungen (Systemsounds via `afplay`)

### Version 1.4 - UX & Performance Refinements
- [x] Menüleisten-App via `rumps` mit Statusicon, Pause und Schnellzugriffen
- [x] `FlowText.app`-Launcher-Bundle (`tools/make_app.py`): eigene TCC-Identität,
      dadurch braucht das Terminal keine Bedienungshilfen/Eingabeüberwachung mehr
- [ ] Push-to-Talk als Alternative zum Toggle
- [ ] Stream-/Chunk-basiertes Buffer-Management
- [ ] Dynamic Whisper Model Selection (`tiny`, `base`, `small`, `large-v3-turbo`)
- [ ] Konfigurierbare System-Prompts für Ollama (z. B. "Formatiere als Stichpunkte", "Korrigiere Grammatik")

---

## 5. Wichtige macOS Berechtigungen (Accessibility & Microphone)

Aufgrund der macOS-Sicherheitsarchitektur benötigt Python / das Terminal folgende Freigaben in den Systemeinstellungen (**Systemeinstellungen -> Datenschutz & Sicherheit**):

1. **Bedienungshilfen (Accessibility)**: Notwendig für `pynput` zur globalen Erfassung von Hotkeys sowie für `pyautogui` zur Simulation von `Cmd+V`.
2. **Mikrofon (Microphone)**: Notwendig für `sounddevice` zur Aufnahme von Mikrofon-Signalen.
3. **Eingabeüberwachung (Input Monitoring)**: **Zwingend erforderlich**, nicht optional. Ein Session-Level-Event-Tap braucht `kTCCServiceListenEvent`; `AXIsProcessTrusted()` deckt diese Berechtigung nicht ab.

**Wichtig:** Die Freigabe gilt der *startenden App* (Terminal.app, iTerm2, VS Code, Cursor) — nicht dem Python-Binary. Nach dem Erteilen die App mit `Cmd+Q` vollständig beenden und neu starten. `python tools/diagnose.py` prüft alle drei Berechtigungen und meldet, was fehlt.

**Empfohlener Weg seit v1.4:** `python tools/make_app.py` erzeugt `FlowText.app`. Damit hat FlowText eine *eigene* TCC-Identität — die Freigaben gelten dann nur noch FlowText, und das Terminal kann sie zurückbekommen. Das Bundle packt nichts ein, es zeigt lediglich auf das vorhandene `.venv`.
