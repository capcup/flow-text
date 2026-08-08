# FlowText

Lokale Voice-to-Text-Engine für macOS (Apple Silicon). 100 % offline — keine Daten verlassen den Mac.

**Status:** v1.1 — Hotkey, Aufnahme und Architektur stehen. Die STT-/LLM-Pipeline ist als
Stub eingehängt und wird in der nächsten Iteration aktiviert.

---

## Schnellstart

```bash
cd ~/Desktop/Projects/ai-agents/flow-text
source .venv/bin/activate
pip install -r requirements.txt

python tools/diagnose.py      # 1. Berechtigungen und Mikrofon prüfen
python -m flowtext            # 2. starten
```

**Auslöser:** rechte Option-Taste **zweimal antippen** — einmal zum Starten, einmal zum
Stoppen der Aufnahme.

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
tap_key = "alt_r"         # alt_r, alt_l, cmd_r, ctrl_r, shift_r
double_tap_window = 0.35

[audio]
max_seconds = 300.0
min_seconds = 0.3
```

Häufige CLI-Overrides:

```bash
python -m flowtext --log-level DEBUG
python -m flowtext --combo '<ctrl>+<shift>+d'
python -m flowtext --tap-key cmd_r
```

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
├── config.py         Dataclasses + TOML
├── permissions.py    AXIsProcessTrusted + IOHIDCheckAccess
├── state.py          State-Enum, StateMachine mit Observern
├── feedback.py       Systemsounds
├── hotkey/
│   ├── detectors.py  reine Trigger-Logik (voll unit-getestet)
│   ├── keynames.py   pynput-Key → stabiler Name
│   └── service.py    Listener + Supervisor
├── audio/
│   ├── recorder.py   deadlockfreie Aufnahme
│   └── devices.py    Geräteabfrage
└── pipeline/
    ├── base.py       Protocols + Verdrahtung
    ├── factory.py    Aufbau aus der Konfiguration
    └── stub.py       Platzhalter bis v1.1
```

---

## Entwicklung

```bash
pytest                  # 76 Tests, laufen ohne macOS
ruff check . && ruff format --check .
mypy flowtext
```

Die Trigger-Erkennung arbeitet auf normalisierten Tastennamen und expliziten Zeitstempeln
statt auf pynput-Objekten — dadurch ist sie ohne Event-Tap testbar. Der `AudioRecorder`
nimmt eine austauschbare Stream-Fabrik, sodass die Tests ohne PortAudio auskommen.

---

## Fehlersuche

| Symptom | Ursache | Abhilfe |
| :--- | :--- | :--- |
| Keine Reaktion, keine Meldung | Berechtigungen fehlen | `python tools/diagnose.py` |
| Funktioniert 1–2×, dann nicht mehr | Event-Tap wurde deaktiviert | Log auf „Hotkey-Callback brauchte … ms" prüfen |
| Aufnahme startet, Datei ist stumm | Mikrofonberechtigung oder falsches Gerät | `audio.device` in der Konfiguration setzen |
| Auslöser feuert versehentlich | Doppel-Tap-Fenster zu groß | `double_tap_window` auf 0.25 senken |
| Auslöser feuert zu selten | Fenster zu klein | `double_tap_window` auf 0.45 erhöhen |

Logs: `~/Library/Logs/FlowText/flowtext.log`
Temporäre Aufnahmen: `~/Library/Application Support/FlowText/tmp/` (werden nach der
Verarbeitung gelöscht, Reste älter als 24 h beim Start)

---

## Weiterführend

- [`ANALYSE_UND_UMSETZUNGSPLAN.md`](ANALYSE_UND_UMSETZUNGSPLAN.md) — Root-Cause-Analyse,
  Code-Review und Umsetzungsplan
- [`PROJECT_PLAN.md`](PROJECT_PLAN.md) — Vision, Tech-Stack, Roadmap
