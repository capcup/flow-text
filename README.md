# FlowText

Lokale Voice-to-Text-Engine für macOS (Apple Silicon). 100 % offline — keine Daten verlassen den Mac.

**Status:** v1.4 — vollständig: Hotkey → Aufnahme → Whisper → optionale LLM-Korrektur →
Text in die aktive App.

---

## Schnellstart

```bash
cd ~/Desktop/Projects/ai-agents/flow-text
source .venv/bin/activate
pip install -r requirements.txt

python tools/diagnose.py      # 1. Berechtigungen und Mikrofon prüfen
python -m flowtext            # 2. starten
```

Beim ersten Start lädt Whisper sein Modell herunter (~1,5 GB für
`large-v3-turbo`). Das passiert einmalig und im Warmup, nicht beim ersten Diktat.


### Als Menüleisten-App

```bash
pip install 'rumps>=0.4.0'
python -m flowtext --menubar     # oder: flowtext-menubar
```

Statt Terminal-Log bekommst du ein Icon in der Menüleiste, das den Zustand zeigt:

| Icon | Bedeutung |
| :---: | :--- |
| 🎙 | bereit |
| 🔴 | nimmt auf |
| ⏳ | transkribiert |
| ⏸ | pausiert |
| ⚠️ | Fehler — ins Log schauen |

Im Menü: pausieren/fortsetzen, `config.toml` öffnen, Logdatei öffnen, beenden.

Die Berechtigungen hängen weiterhin an der startenden App (Terminal), weil dies noch
kein eigenständiges `.app`-Bundle ist. Das kommt in einer späteren Stufe.

**Auslöser:** Command-Taste **zweimal antippen** (links oder rechts) — einmal zum Starten,
einmal zum Stoppen der Aufnahme.

> **Warum nicht Option?** Claude für Mac belegt den Doppel-Tap auf Option bereits systemweit
> für seine Schnelleingabe. FlowText warnt beim Start, wenn ein bekannt belegter Auslöser
> konfiguriert ist. Wer Claudes Shortcut abschaltet, kann `tap_key = "alt_r"` verwenden.

---

## Berechtigungen (der häufigste Stolperstein)

FlowText braucht drei Freigaben unter *Systemeinstellungen → Datenschutz & Sicherheit*:

| Berechtigung | Wofür |
| :--- | :--- |
| **Bedienungshilfen** | globaler Tastatur-Event-Tap |
| **Eingabeüberwachung** | dito — wird oft vergessen, `AXIsProcessTrusted()` deckt sie **nicht** ab |
| **Mikrofon** | Audioaufnahme |

Drei Regeln, die viel Zeit sparen:

1. Die Freigabe gilt der **startenden App** (Terminal.app, iTerm2, VS Code, Cursor) — nicht
   dem Python-Binary. Ein pyenv- oder venv-Interpreter taucht in der Liste gar nicht auf.
2. Nach dem Erteilen die App mit **Cmd+Q vollständig beenden** und neu starten. Fenster
   schließen reicht nicht.
3. Zum Einrichten **Terminal.app** verwenden. Das integrierte Terminal einer IDE braucht
   eine eigene Freigabe und verwirrt beim Debuggen.

`python tools/diagnose.py` prüft alles und sagt genau, was fehlt.

---

## Konfiguration

`config.toml` im Projektverzeichnis. Persönliche Overrides gehören in `config.local.toml`
(nicht im Git). Alle Schlüssel sind optional.

```toml
[hotkey]
mode = "double_tap"       # oder "combo"
tap_key = "cmd"           # generisch: cmd, shift, ctrl (beide Seiten)
                          # seitenspezifisch: cmd_r, shift_r, ctrl_r, alt_l
double_tap_window = 0.50  # großzügig — zu eng fühlt sich an wie "tut nichts"
max_tap_duration = 0.45

[audio]
max_seconds = 300.0
min_seconds = 0.3

[pipeline]
transcriber = "mlx"       # oder "stub" (nur protokollieren)
language = "de"           # feste Sprache, keine Auto-Erkennung
refiner = "none"          # "ollama" für LLM-Korrektur
injector = "clipboard"    # Zwischenablage + Cmd+V
paste = true              # false = nur kopieren
```

### Whisper-Modell

`whisper_model` bestimmt Tempo und Qualität. Auf einem M4 mit 16 GB:

| Modell | Größe | Wofür |
| :--- | :--- | :--- |
| `mlx-community/whisper-large-v3-turbo` | ~1,5 GB | Standard — beste Qualität, schnell genug |
| `mlx-community/whisper-medium-mlx` | ~1,5 GB | ältere Alternative, langsamer |
| `mlx-community/whisper-small-mlx` | ~0,5 GB | wenn RAM knapp ist |
| `mlx-community/whisper-tiny` | ~0,1 GB | zum Testen der Kette |

### LLM-Korrektur (optional)

`refiner = "ollama"` lässt ein lokales Modell Interpunktion, Groß-/Kleinschreibung und
offensichtliche Erkennungsfehler korrigieren. Voraussetzung:

```bash
ollama pull qwen2.5:7b
ollama serve
```

Ist Ollama nicht erreichbar oder das Modell nicht gezogen, schaltet FlowText die Stufe
beim Start ab und gibt das Rohtranskript aus — die Korrektur steht nie zwischen dir und
deinem Text. Antwortet das Modell dreimal länger als das Transkript, wird die Antwort
verworfen: dann hat es geplaudert statt korrigiert.

Häufige CLI-Overrides:

```bash
python -m flowtext --watch-keys      # Diagnose: kommen überhaupt Events an?
python -m flowtext --log-level DEBUG # loggt jeden Tastennamen live
python -m flowtext --combo '<ctrl>+<shift>+d'
python -m flowtext --tap-key shift_r
```

**Belegte Auslöser** (FlowText warnt beim Start):

| Auslöser | Belegt durch |
| :--- | :--- |
| Doppel-Tap Option (links/rechts) | Claude für Mac — Schnelleingabe |
| `<ctrl>+<alt>+<space>` | macOS — nächste Eingabequelle |
| `<ctrl>+<space>` | macOS — vorherige Eingabequelle |
| `<cmd>+<space>` | macOS — Spotlight |
| `<cmd>+<alt>+<space>` | macOS — Finder-Suchfenster |

Freie Empfehlungen: Doppel-Tap `cmd_r`, `shift_r` oder `ctrl_r`; als Kombination
`<ctrl>+<shift>+d` oder `<f5>`.

> **Nicht `<ctrl>+<alt>+<space>` verwenden.** Das ist der macOS-Systemkurzbefehl
> „nächste Eingabequelle". Er wechselt die Tastaturbelegung und tippt wegen Option+Space
> ein unsichtbares geschütztes Leerzeichen (U+00A0) in das gerade fokussierte Textfeld —
> genau dorthin, wo FlowText anschließend diktieren soll.

---

## Architektur

```
Event-Tap-Thread   → nur queue.put_nowait()      < 1 ms, Pflicht
Controller-Thread  → Zustandsautomat, Stream-Start/Stop
Worker-Thread      → Transkription, LLM, Ausgabe (darf Sekunden dauern)
Supervisor-Thread  → startet einen gestorbenen Listener neu
PortAudio-Callback → nur queue.put_nowait()      kein Lock
```

Zwei Regeln, aus denen sich fast alles ableitet:

**Der Event-Tap-Callback darf nicht blockieren.** macOS deaktiviert einen Tap dauerhaft,
wenn dessen Callback zu lange braucht (`kCGEventTapDisabledByTimeout`) — und pynput
reaktiviert ihn nie. Deshalb macht der Callback nichts außer Set-Logik und einem
`put_nowait`. Die tatsächliche Dauer wird gemessen und beim Beenden protokolliert.

**Der Audio-Callback nimmt kein Lock.** `stream.stop()` wartet auf das Ende des laufenden
Callbacks. Nimmt der Callback ein Lock, das der stoppende Thread hält, ist das ein
Deadlock. Puffer werden deshalb über eine Queue übergeben.

```
flowtext/
├── __main__.py       CLI, Signal-Handling
├── app.py            Controller, Zustandsautomat, Thread-Orchestrierung
├── config.py         Dataclasses + TOML, Validierung, Shortcut-Konflikte
├── permissions.py    AXIsProcessTrusted + IOHIDCheckAccess, verantwortliche App
├── diagnostics.py    Umgebung, Berechtigungen, Mikrofon, Tastatur
├── menubar.py        Statusicon (rumps), optional
├── state.py          State-Enum, StateMachine mit Observern
├── feedback.py       Systemsounds
├── hotkey/
│   ├── detectors.py  reine Trigger-Logik (voll unit-getestet)
│   ├── keynames.py   pynput-Key → stabiler Name (über macOS-vk)
│   └── service.py    Listener, Supervisor, Event-Watchdog
├── audio/
│   ├── recorder.py   deadlockfreie Aufnahme
│   └── devices.py    Geräteabfrage
└── pipeline/
    ├── base.py            Protocols + Verdrahtung
    ├── factory.py         Aufbau aus der Konfiguration
    ├── transcribe_mlx.py  mlx-whisper
    ├── refine_ollama.py   LLM-Korrektur
    ├── inject_macos.py    Zwischenablage + Cmd+V
    └── stub.py            Platzhalter für den Trockenlauf
```

`tools/diagnose.py` und `--watch-keys` sind beide dünne Aufrufer von
`flowtext.diagnostics` — die Diagnoselogik existiert nur einmal.

---

## Entwicklung

```bash
pytest                  # 173 Tests, laufen ohne macOS
ruff check . && ruff format --check .
mypy                    # strict, Zielplattform darwin
```

Die Tests kommen ohne mlx-whisper, Ollama, pyperclip und rumps aus: sie schieben
Fake-Module in `sys.modules` und prüfen damit die Logik statt der Fremdbibliothek.

Fehlt ein Extra zur Laufzeit, fällt das beim **Start** auf (die Factory prüft mit
`find_spec`, bevor sie baut) und nicht erst beim ersten Diktat. FlowText läuft dann
mit dem Stub weiter und sagt im Log, was fehlt.

Die Trigger-Erkennung arbeitet auf normalisierten Tastennamen und expliziten Zeitstempeln
statt auf pynput-Objekten — dadurch ist sie ohne Event-Tap testbar. Der `AudioRecorder`
nimmt eine austauschbare Stream-Fabrik, sodass die Tests ohne PortAudio auskommen.

---

## Fehlersuche

| Symptom | Ursache | Abhilfe |
| :--- | :--- | :--- |
| Auslöser öffnet eine andere App | Shortcut ist belegt (z. B. Claude bei Doppel-Tap Option) | `tap_key` auf `cmd_r` oder `shift_r` ändern |
| **Auslöser macht gar nichts** | siehe Kasten unten | `python -m flowtext --watch-keys` |
| Doppel-Tap wird selten erkannt | zu langsam getippt | `double_tap_window` auf `0.6` erhöhen |
| Funktioniert 1–2×, dann nicht mehr | Event-Tap wurde deaktiviert | Log auf „Hotkey-Callback brauchte … ms" prüfen |
| Aufnahme startet, Datei ist stumm | Mikrofonberechtigung oder falsches Gerät | `audio.device` in der Konfiguration setzen |
| Text wird nicht eingefügt, liegt aber in der Ablage | Cmd+V braucht Bedienungshilfen | Freigabe prüfen, oder `paste = false` und von Hand einfügen |
| Erstes Diktat dauert ewig | Modell wird noch geladen | einmalig; Log zeigt „Whisper-Modell bereit" |
| Transkript kommt auf Englisch | Auto-Erkennung bei kurzem Diktat | `language = "de"` setzen (Standard) |
| LLM antwortet statt zu korrigieren | Modell hält den Text für eine Frage | wird automatisch verworfen; ggf. `ollama_prompt` schärfen |
| Auslöser feuert versehentlich | Doppel-Tap-Fenster zu groß | `double_tap_window` auf 0.25 senken |
| Auslöser feuert zu selten | Fenster zu klein | `double_tap_window` auf 0.45 erhöhen |

### „Der Auslöser macht gar nichts"

Erst die entscheidende Frage klären — kommen überhaupt Tastaturereignisse an?

```bash
python -m flowtext --watch-keys
```

- **`Ereignisse gesamt: 0`** → der Event-Tap liefert nichts. Das ist immer eine
  Berechtigung, nie der Auslöser. Bedienungshilfen **und** Eingabeüberwachung für
  die startende App setzen, dann `Cmd+Q` und neu starten.
- **Ereignisse > 0, Auslösungen 0** → Events kommen an, aber der Name passt nicht.
  Mit `--log-level DEBUG` ablesen, welchen Namen deine Taste bekommt, und
  `hotkey.tap_key` entsprechend setzen.
- **Auslösungen > 0** → der Hotkey ist in Ordnung, das Problem liegt weiter hinten
  (Mikrofon, Pipeline). Ins Log schauen.

Läuft FlowText normal und kommt in den ersten 20 Sekunden kein einziges Ereignis an,
meldet sich der Watchdog von selbst mit derselben Anleitung.

Logs: `~/Library/Logs/FlowText/flowtext.log`
Temporäre Aufnahmen: `~/Library/Application Support/FlowText/tmp/` (werden nach der
Verarbeitung gelöscht, Reste älter als 24 h beim Start)

---

## Weiterführend

- [`ANALYSE_UND_UMSETZUNGSPLAN.md`](ANALYSE_UND_UMSETZUNGSPLAN.md) — Root-Cause-Analyse,
  Code-Review und Umsetzungsplan
- [`PROJECT_PLAN.md`](PROJECT_PLAN.md) — Vision, Tech-Stack, Roadmap
