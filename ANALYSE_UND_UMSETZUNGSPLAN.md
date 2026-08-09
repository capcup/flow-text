# FlowText — Analyse, Bewertung & Umsetzungsplan

**Stand:** 2026-08-08 · **Analysierte Version:** MVP Step 1 (`main.py`, 289 Zeilen)
**Umgebung:** macOS / Apple Silicon, Python 3.12.3 (pyenv), `.venv` (1,2 GB), pynput 1.8.2, sounddevice 0.5.5, mlx-whisper 0.4.3, ollama 0.6.2

---

> ## Umsetzungsstand (08.08.2026) — v1.2
>
> **Phasen 0–3 sind umgesetzt, der Hotkey läuft.** Das Projekt liegt als Paket
> `flowtext/` vor; 124 Tests, `ruff`, `ruff format` und `mypy --strict` sind grün.
> Bedienung: siehe `README.md`.
>
> ### Nachtrag: was die Analyse noch nicht wusste
>
> Die Analyse empfahl **Doppel-Tap auf die rechte Option-Taste** (§2 U3, §7.5). Das ist
> überholt: **Claude für Mac belegt Doppel-Tap Option systemweit** für seine
> Schnelleingabe. Aktueller Default ist deshalb **Doppel-Tap Command** (beide Seiten).
> `config.RESERVED_TAP_KEYS` und `RESERVED_COMBOS` führen die bekannten Kollisionen;
> FlowText warnt beim Start, wenn ein belegter Auslöser konfiguriert ist.
>
> Zweite Erkenntnis aus der Praxis: die eigentliche Hürde war weder Auslöser noch Code,
> sondern die **Eingabeüberwachung**. Sie äußert sich als vollkommene Stille. Dagegen
> gibt es jetzt drei Stufen: `permissions.responsible_app()` benennt die freizugebende
> App konkret, `--watch-keys` unterscheidet „keine Ereignisse" von „falscher Tastenname",
> und ein Watchdog meldet sich von selbst, wenn 20 Sekunden lang nichts ankommt.
>
> | Ursache / Befund | Status | Wo behoben |
> | :--- | :---: | :--- |
> | U1 Blockierender Callback | ✅ | `flowtext/hotkey/service.py` — Callback macht nur Set-Logik + `put_nowait`, Dauer wird gemessen |
> | U2 Stiller Listener-Tod | ✅ | `HotkeyService._supervise` — `join()` + Neustart mit Backoff |
> | U3 Systemkurzbefehl-Kollision | ✅ | Default ist Doppel-Tap rechte Option (`DoubleTapDetector`) |
> | U4 Falsche Permission-Prüfung | ✅ | `flowtext/permissions.py` — `restype=c_bool` + `IOHIDCheckAccess`, fail-loud |
> | U5 TCC am falschen Prozess | ✅ | Interpreter-Report beim Start, Warnung in `tools/diagnose.py` |
> | U6 Deadlock im Audio-Pfad | ✅ | `flowtext/audio/recorder.py` — Queue statt Lock, Regressionstest vorhanden |
> | F8 `issubset` / Obermengen | ✅ | Exakter Modifier-Vergleich in `ComboDetector` |
> | F9–F13, A1–A7, B1–B8 | ✅ | siehe Commits `v1.1` und `v1.2` |
> | Fremd-Shortcut (Claude, macOS) | ✅ | `config.RESERVED_TAP_KEYS`, Warnung beim Start |
> | Stille bei fehlender Berechtigung | ✅ | `responsible_app()`, `--watch-keys`, Event-Watchdog |
> | Phase 4 (mlx-whisper, Ollama, Injektion) | ✅ | `pipeline/transcribe_mlx.py`, `refine_ollama.py`, `inject_macos.py` |
>
> Damit ist der Plan aus §6 vollständig abgearbeitet. Aus [§9](#9-roadmap-v11--v20)
> sind inzwischen ebenfalls erledigt: Menüleisten-App mit Statusicon und das
> `.app`-Bundle — letzteres als **Launcher**-Bundle statt py2app. Es packt nichts
> ein, sondern zeigt auf das vorhandene `.venv`, und kostete damit eine Stunde
> statt zwei Tage. Der entscheidende Gewinn ist derselbe: FlowText hat eine eigene
> TCC-Identität, das Terminal braucht keine Rechte mehr.
>
> ### Nachtrag 09.08.2026: das fehlende Menüleisten-Icon
>
> Das Bundle startete, das Diktat funktionierte, die Rechte stimmten — nur das
> Statusicon fehlte. Ursache war die Bundle-Identität: **AppKit ermittelt
> `[NSBundle mainBundle]` über den _aufgelösten_ Pfad der laufenden ausführbaren
> Datei.** Ein Symlink `Contents/MacOS/python3` genügt daher nicht; `realpath`
> folgt ihm ins `.venv` und weiter zum Basis-Interpreter, der Prozess liegt also
> wieder außerhalb der `.app` und `NSStatusBar` zeichnet nichts. Die TCC-Rechte
> überleben das, weil sie am verantwortlichen Prozess hängen — daher das
> irreführende Bild „alles funktioniert außer dem Icon".
>
> Behoben durch eine **Kopie** des Startbinaries plus `pyvenv.cfg` daneben, also
> genau den Aufbau von `python -m venv --copies`. `bundle.verify_bundle()` startet
> die Kopie nach dem Bauen und prüft ihre Importe, damit ein kaputter Framework-
> Verweis nicht erst beim Doppelklick auffällt.
>
> Der Weg dorthin war eine Bisektion: minimale rumps-App im Terminal (Icon da) →
> FlowText im Terminal (Icon da) → minimale rumps-App **aus dem Bundle** (kein
> Icon). Damit war der eigene Code entlastet und nur noch der Startpfad übrig.
>
> Dahinter lag eine **zweite, unabhängige Ursache**: `MenubarApp.run()` rief
> `app.start()` *vor* `rumps.run()`. pynput, PortAudio und MLX legen dabei eigene
> CoreFoundation-Runloops an — geschieht das, bevor `NSApplication` den Hauptthread
> besitzt, kehrt `rumps.run()` sofort zurück, das `finally` fährt alles wieder
> herunter, und die App beendet sich nach wenigen Sekunden scheinbar grundlos.
> Genau das zeigte `launcher.log`: sauberer Start, Modell geladen, Shutdown.
> `app.start()` läuft jetzt im ersten Timer-Tick, also innerhalb der laufenden
> Runloop. Kehrt `rumps.run()` künftig zu schnell zurück, sagt das Log es
> ausdrücklich, statt nur ein fehlendes Icon zu hinterlassen.
>
> Offen bleiben Push-to-Talk, Streaming-Transkription und die Vokabularlisten.

---

## Inhalt

1. [Executive Summary & Bewertung](#1-executive-summary--bewertung)
2. [Warum der Hotkey nicht funktioniert](#2-warum-der-hotkey-nicht-funktioniert)
3. [Sofort-Diagnose: Schritt-für-Schritt](#3-sofort-diagnose-schritt-für-schritt)
4. [Vollständiges Code-Review](#4-vollständiges-code-review)
5. [Zielarchitektur](#5-zielarchitektur)
6. [Umsetzungsplan mit Akzeptanzkriterien](#6-umsetzungsplan-mit-akzeptanzkriterien)
7. [Konkrete Code-Bausteine](#7-konkrete-code-bausteine)
8. [Testmatrix & Definition of Done](#8-testmatrix--definition-of-done)
9. [Roadmap v1.1 – v2.0](#9-roadmap-v11--v20)

---

## 1. Executive Summary & Bewertung

Das Projekt ist ein **solider, sauber geschriebener Prototyp** mit klarer Vision und gutem Plan-Dokument. Der Code ist lesbar, typisiert und dokumentiert. Die Probleme liegen nicht im Stil, sondern in **plattformspezifischer Nebenläufigkeit** — genau dort, wo macOS-Hotkey-Tools erfahrungsgemäß scheitern.

### Bewertung

| Dimension | Note | Begründung |
| :--- | :---: | :--- |
| Vision & Planung | **A** | `PROJECT_PLAN.md` ist präzise, Tech-Stack-Wahl (MLX statt PyTorch-Whisper) ist für M4 richtig |
| Lesbarkeit & Stil | **B+** | Docstrings, Type Hints, sprechende Namen — sehr ordentlich |
| Architektur | **C** | Plan verspricht Modularität, Realität ist eine 289-Zeilen-Monolith-Datei; Event-Hook ist der einzige Entkopplungspunkt |
| Korrektheit / Nebenläufigkeit | **D** | Zwei latente Deadlocks, blockierender Callback im Event-Tap, stiller Listener-Tod bei Exceptions |
| macOS-Integration | **D** | Falscher Rückgabetyp bei der Permission-Prüfung, kollidierender System-Hotkey, kein Input-Monitoring-Check |
| Robustheit / Fehlerbehandlung | **D** | Keine Watchdogs, kein `join()`, Fehler verschwinden lautlos |
| Reproduzierbarkeit | **D** | Kein Git, kein `.gitignore`, keine Pins, `__pycache__` im Repo, 1,2 GB `.venv` im Projektordner |
| Tests | **F** | Nicht vorhanden |
| UX-Feedback | **F** | Keinerlei Rückmeldung, ob gerade aufgenommen wird |

**Gesamteinschätzung: C-** — Guter Prototyp, aber noch nicht alltagstauglich. Mit den Fixes aus Phase 0–2 (geschätzt 1–2 Arbeitstage) wird daraus ein zuverlässiges Werkzeug.

### Die drei wichtigsten Erkenntnisse

1. **Der Hotkey ist nicht „kaputt" — er ist blockiert, kollidiert und stirbt still.** Es gibt nicht *eine* Ursache, sondern eine Kette von vier unabhängigen Fehlern, die sich gegenseitig maskieren.
2. **Die Permission-Prüfung liefert potenziell falsche Ergebnisse.** `ctypes` interpretiert den Rückgabewert falsch → die Warnung erscheint möglicherweise nie, obwohl die Rechte fehlen.
3. **`Ctrl+Option+Space` ist ein reservierter macOS-Systemkurzbefehl** („Nächste Eingabequelle auswählen"). Selbst wenn alles andere funktioniert, ist dieser Hotkey die schlechtestmögliche Wahl.

---

## 2. Warum der Hotkey nicht funktioniert

Priorisiert nach Wahrscheinlichkeit × Auswirkung. Jede Ursache ist unabhängig — es können mehrere gleichzeitig zutreffen.

---

### U1 — Blockierender Callback tötet den Event-Tap (kritisch)

**Was passiert:**

`RobustMacHotkeyListener._on_press` ruft `self.on_trigger()` **synchron** auf (`main.py:181`), und zwar *während* `self._lock` gehalten wird. `on_trigger` ist `FlowTextApp._toggle_recording`, das direkt `AudioRecorder.start_recording()` bzw. `stop_recording()` aufruft. `start_recording()` erzeugt und startet einen `sd.InputStream` — eine CoreAudio-Geräteaushandlung, die je nach Interface **100–500 ms und mehr** dauern kann.

Dieser gesamte Aufruf läuft im **CFRunLoop des CGEventTaps**.

**Warum das fatal ist:**

macOS deaktiviert einen Event-Tap automatisch, wenn dessen Callback zu lange braucht (`kCGEventTapDisabledByTimeout`). Der Tap wird dann *dauerhaft* stumm geschaltet, bis er explizit per `CGEventTapEnable` reaktiviert wird.

**pynput reaktiviert ihn nicht.** Beleg aus dem installierten Paket, `pynput/_util/darwin.py`:

```python
# Zeile 234 — der EINZIGE CGEventTapEnable-Aufruf im gesamten Modul
CGEventTapEnable(tap, True)
```

Es gibt keinerlei Behandlung von `kCGEventTapDisabledByTimeout` oder `kCGEventTapDisabledByUserInput`. Einmal deaktiviert = für die Laufzeit des Prozesses tot.

**Symptom:** Der Hotkey funktioniert ein- bis zweimal und dann nie wieder — ohne jede Fehlermeldung. Oder er funktioniert unter Last (Spotlight-Indexierung, Videocall) gar nicht.

**Fix:** Callback muss in **unter 1 ms** zurückkehren. Hotkey-Event nur in eine `queue.Queue` legen, die eigentliche Arbeit macht ein Worker-Thread. Siehe [§7.2](#72-nicht-blockierender-hotkey-callback).

---

### U2 — Exceptions töten den Listener lautlos (kritisch)

**Was passiert:**

pynput umschließt jeden Callback mit `AbstractListener._emitter`. Beleg aus `pynput/_util/__init__.py`, Zeilen 231–247:

```python
def inner(self, *args, **kwargs):
    try:
        return f(self, *args, **kwargs)
    except Exception as e:
        if not isinstance(e, self._HANDLED_EXCEPTIONS):
            if not isinstance(e, AbstractListener.StopException):
                self._log.exception("Unhandled exception in listener callback")
            self._queue.put(None if isinstance(e, cls.StopException) else sys.exc_info())
            self.stop()  # <-- Listener wird beendet
```

Die Exception wird in eine Queue gelegt und **erst beim Aufruf von `listener.join()` erneut geworfen**. `main.py` ruft `join()` nie auf (`main.py:192-193`: nur `.start()`), sondern hält den Hauptthread mit `time.sleep(0.5)` am Leben.

**Konsequenz:** Wirft irgendetwas in `_toggle_recording` eine Exception — z. B. `np.concatenate` bei leerer Liste, `sf.write` bei fehlenden Schreibrechten im CWD, ein CoreAudio-Fehler — dann:

- ist der Listener sofort und endgültig tot,
- läuft die `while not stop_event.is_set()`-Schleife munter weiter,
- sieht der Nutzer **nichts**. Keine Meldung, kein Absturz, nur ein Programm, das nicht mehr reagiert.

**Fix:** Watchdog-Thread, der `listener.join()` in einem eigenen Thread aufruft, die Exception loggt und den Listener neu startet. Siehe [§7.4](#74-listener-supervisor--watchdog).

---

### U3 — Systemkonflikt: `Ctrl+Option+Space` ist reserviert (hoch)

`Control–Space` und `Control–Option–Space` sind macOS-Standardkurzbefehle zum Wechseln der Eingabequelle (vorherige bzw. **nächste** Eingabequelle im Eingabemenü). Nachzulesen in Apples Kurzbefehl-Übersicht und einstellbar unter *Systemeinstellungen → Tastatur → Tastaturkurzbefehle → Eingabequellen*.

Drei separate Probleme daraus:

1. **Nebenwirkung:** Bei mehreren Eingabequellen wechselt jedes Mal die Tastaturbelegung mit.
2. **Textverschmutzung:** pynputs Tap läuft als `kCGEventTapOptionListenOnly` (Beleg: `_util/darwin.py:275`) — das Event wird also **nicht unterdrückt**. Da `Option+Space` unter macOS ein geschütztes Leerzeichen (U+00A0) erzeugt, tippt jeder Hotkey-Druck ein unsichtbares Sonderzeichen in das gerade fokussierte Textfeld. Genau in das Feld, in das FlowText anschließend diktieren soll.
3. **Konsumierung:** Je nach Systemkonfiguration kann der Symbolic-Hotkey-Handler des WindowServers das Event vor dem Tap abfangen.

**Fix:**

- **Kurzfristig:** Hotkey wechseln. Gute Kandidaten ohne Systemkollision: `F5`, `Ctrl+Shift+D`, `Cmd+Shift+Ö`, oder ein **doppelter Tap auf die rechte Option-Taste** (das Superwhisper-Muster — kollisionsfrei, weil Modifier allein kein Systemkurzbefehl ist).
- **Mittelfristig:** Ereignis gezielt unterdrücken via `darwin_intercept`. pynput unterstützt das (`keyboard/_base.py:693 ff.`): Gibt der Callback das Event nicht zurück, wird es systemweit verschluckt. **Achtung:** Damit wechselt der Tap von `ListenOnly` auf `kCGEventTapOptionDefault` — das erfordert zwingend Bedienungshilfen-Rechte und macht die Latenzanforderung aus U1 noch strikter.
- **Langfristig:** Carbon `RegisterEventHotKey` statt Event-Tap — reserviert den Hotkey sauber beim System, unterdrückt ihn automatisch und **benötigt gar keine TCC-Berechtigung**. Preis: braucht eine echte Cocoa-Runloop (z. B. via `rumps` als Menüleisten-App).

---

### U4 — Die Berechtigungsprüfung ist falsch implementiert (hoch)

```python
# main.py:106-115
lib = ctypes.cdll.LoadLibrary(".../ApplicationServices")
return bool(lib.AXIsProcessTrusted())
```

Zwei Fehler:

1. **Fehlender `restype`.** `ctypes` nimmt standardmäßig `c_int` an (4 Byte). `AXIsProcessTrusted()` gibt aber einen `Boolean` zurück — **1 Byte**. Die oberen 3 Byte sind undefiniert. Ein Rückgabewert mit Müll in den oberen Bytes wird zu `True` → **die Warnung erscheint nie, obwohl die Rechte fehlen**. Genau das erklärt „kein Fehler, aber es tut nichts".
2. **`except: return True`** (Zeile 113–114). Schlägt der Check fehl, meldet die Funktion „alles in Ordnung". Fail-open statt fail-loud.

Außerdem fehlt die Prüfung der **zweiten** relevanten Berechtigung: *Eingabeüberwachung* (Input Monitoring / TCC `kTCCServiceListenEvent`). Ein Session-Level-Event-Tap braucht diese; `AXIsProcessTrusted()` deckt sie nicht ab. Korrekt geprüft wird sie mit `IOHIDCheckAccess`.

**Fix:** Siehe [§7.1](#71-korrekte-berechtigungsprüfung).

---

### U5 — Berechtigungen hängen am falschen Prozess (hoch, umgebungsabhängig)

Das Projekt nutzt ein **pyenv**-Python (`.venv/pyvenv.cfg`: `home = /Users/capcup/.pyenv/versions/3.12.3/bin`). Das ist der klassische TCC-Stolperstein:

- macOS vergibt Rechte an den **verantwortlichen Prozess** — also Terminal.app, iTerm2, VS Code oder Cursor, **nicht** an das Python-Binary.
- Startest du das Skript im integrierten Terminal von VS Code/Cursor, brauchst *diese App* die Rechte — eine Freigabe für Terminal.app hilft dann nicht.
- Nach dem Erteilen muss die App **vollständig beendet** werden (`Cmd+Q`, nicht nur Fenster schließen) und neu gestartet.
- pyenv-Pythons sind in der Regel **keine Framework-Builds**. Für den reinen Listener genügt das, aber `pyautogui` (geplant für die `Cmd+V`-Injektion in v1.1) kann damit Probleme bekommen.
- Nach jedem pyenv-Update ändert sich der Binärpfad — bestehende Freigaben laufen ins Leere.

**Fix:** Als Diagnoseregel ins Projekt aufnehmen: Immer aus **Terminal.app** testen (nicht aus der IDE), bis alles läuft. Beim Start loggen, welcher Interpreter läuft (`sys.executable`) und welcher Elternprozess ihn gestartet hat.

---

### U6 — Deadlock-Risiko im Audio-Pfad (mittel, sporadisch)

Zwei Stellen, an denen ein Lock über einen blockierenden PortAudio-Aufruf gehalten wird:

**a) `stop_recording` (`main.py:78-88`):**

```python
with self._lock:  # Lock wird gehalten ...
    self.is_recording = False
    if self._stream:
        self._stream.stop()  # ... und wartet auf das Ende des Audio-Callbacks
        self._stream.close()
```

Läuft in genau diesem Moment `_audio_callback` und hängt bei `with self._lock:` (Zeile 50), warten beide aufeinander. `is_recording = False` wird zwar vorher gesetzt, aber ein bereits *in-flight* befindlicher Callback hat die Prüfung schon passiert. Klassische Race Condition → sporadischer, nicht reproduzierbarer Hänger.

**b) `start_recording` (`main.py:55-70`):** `_stream.start()` wird innerhalb von `with self._lock:` aufgerufen. PortAudio kann den Callback bereits feuern, **bevor** `start()` zurückkehrt → derselbe Deadlock.

Und weil dieser Deadlock im Hotkey-Callback-Thread passiert (siehe U1), reißt er den Event-Tap gleich mit in den Tod.

**Fix:** Audio-Callback darf **nie** ein Lock nehmen, das ein anderer Thread über einen blockierenden Aufruf hält. Stattdessen `queue.Queue` (lock-frei aus Callback-Sicht) oder Ringpuffer verwenden, und Stream-Lifecycle-Operationen außerhalb des Locks ausführen. Siehe [§7.3](#73-deadlockfreier-audiorecorder).

---

### Zusammenfassung der Ursachen

| # | Ursache | Schwere | Symptom |
| :-- | :--- | :---: | :--- |
| U1 | Blockierender Callback → Tap-Timeout, keine Reaktivierung | 🔴 kritisch | Funktioniert 1–2×, dann tot |
| U2 | Exception → `listener.stop()`, kein `join()` | 🔴 kritisch | Stirbt lautlos, Programm läuft weiter |
| U3 | `Ctrl+Option+Space` = macOS-Systemkurzbefehl | 🟠 hoch | Sprachwechsel + unsichtbares U+00A0 im Textfeld |
| U4 | `AXIsProcessTrusted` ohne `restype`, fail-open | 🟠 hoch | Warnung erscheint nie, obwohl Rechte fehlen |
| U5 | TCC-Rechte am falschen Prozess (pyenv/IDE) | 🟠 hoch | Gar keine Tastaturevents |
| U6 | Lock über blockierendem PortAudio-Call | 🟡 mittel | Sporadischer Hänger beim Stoppen |

---

## 3. Sofort-Diagnose: Schritt-für-Schritt

Diese Reihenfolge einhalten — sie eliminiert Ursachen von außen nach innen.

**Schritt 1 — Aus Terminal.app testen, nicht aus der IDE.**

```bash
cd ~/Desktop/Projects/ai-agents/flow-text
source .venv/bin/activate
python -c "import sys; print(sys.executable)"
```

**Schritt 2 — Beide Berechtigungen erteilen** (*Systemeinstellungen → Datenschutz & Sicherheit*):

- **Bedienungshilfen** → Terminal.app hinzufügen und aktivieren
- **Eingabeüberwachung** → Terminal.app hinzufügen und aktivieren
- **Mikrofon** → Terminal.app aktivieren

Dann Terminal mit `Cmd+Q` **vollständig beenden** und neu starten.

**Schritt 3 — Berechtigungen korrekt verifizieren** (nicht mit dem fehlerhaften Check aus `main.py`):

```bash
python - <<'PY'
import ctypes
apps = ctypes.cdll.LoadLibrary('/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')
apps.AXIsProcessTrusted.restype = ctypes.c_bool
iokit = ctypes.cdll.LoadLibrary('/System/Library/Frameworks/IOKit.framework/IOKit')
iokit.IOHIDCheckAccess.restype = ctypes.c_int
iokit.IOHIDCheckAccess.argtypes = [ctypes.c_uint32]
states = {0: "GRANTED", 1: "DENIED", 2: "UNKNOWN"}
print("Bedienungshilfen  :", "GRANTED" if apps.AXIsProcessTrusted() else "DENIED")
print("Eingabeüberwachung:", states.get(iokit.IOHIDCheckAccess(1), "?"))
PY
```

Beides muss `GRANTED` sein.

**Schritt 4 — Hotkey wechseln.** In `main.py:260` testweise auf `<f5>` oder `<ctrl>+<shift>+d` umstellen. Reagiert es jetzt, war U3 (mit-)ursächlich.

**Schritt 5 — Isoliert testen, ob der Listener überhaupt Events sieht:**

```bash
python debug_keys.py
```

Kommen hier gar keine `[KEY DOWN]`-Zeilen → Berechtigungsproblem (U4/U5). Kommen sie, aber `main.py` reagiert nicht → U1/U2/U3.

**Schritt 6 — Listener-Tod sichtbar machen.** Vorübergehend in `main.py` ergänzen:

```python
import threading

threading.Thread(
    target=lambda: (self._hotkey_listener._listener.join(), logger.error("LISTENER IST GESTORBEN")),
    daemon=True,
).start()
```

Erscheint diese Meldung, ist U2 bestätigt und die Original-Exception wird mit ausgegeben.

---

## 4. Vollständiges Code-Review

### 4.1 Funktionale Fehler

| ID | Datei:Zeile | Befund | Schwere |
| :-- | :--- | :--- | :---: |
| F1 | `main.py:181` | `on_trigger()` synchron im Event-Tap-Callback, unter gehaltenem Lock | 🔴 |
| F2 | `main.py:78-88` | Lock gehalten über `stream.stop()/close()` → Deadlock-Race | 🔴 |
| F3 | `main.py:64-70` | Lock gehalten über `stream.start()` → Deadlock-Race | 🟠 |
| F4 | `main.py:192-193` | `listener.start()` ohne `join()`/Supervisor → stiller Tod | 🔴 |
| F5 | `main.py:111-114` | `AXIsProcessTrusted` ohne `restype = c_bool`, `except: return True` | 🟠 |
| F6 | `main.py:106-115` | Input-Monitoring-Berechtigung wird gar nicht geprüft | 🟠 |
| F7 | `main.py:260` | Hotkey kollidiert mit macOS-Systemkurzbefehl | 🟠 |
| F8 | `main.py:178` | `issubset` → jede *Obermenge* triggert ebenfalls (`Ctrl+Alt+Shift+Space` löst aus) | 🟡 |
| F9 | `main.py:95` | `np.concatenate` auf potenziell leerer Liste → `ValueError` → tötet Listener (F4) | 🟡 |
| F10 | `main.py:98-99` | `temp_audio.wav` relativ zum CWD → Schreibfehler je nach Startverzeichnis | 🟡 |
| F11 | `main.py:271-277` | `signal_handler` loggt aus dem Signal-Kontext (`logging` ist nicht reentrant-sicher) | 🟢 |
| F12 | `main.py:45` | `time_info: dict` — falscher Type Hint (PortAudio liefert eine CData-Struktur) | 🟢 |
| F13 | `debug_keys.py:29-35` | Docstring verspricht „Tests sounddevice Microphone Recording", der Code fragt nur Geräte ab | 🟢 |

### 4.2 Architektur & Design

| ID | Befund | Empfehlung |
| :-- | :--- | :--- |
| A1 | Alles in einer Datei; `PROJECT_PLAN.md` verspricht modulare Architektur | Paketstruktur nach [§5.1](#51-modulstruktur) |
| A2 | Keine Zustandsmaschine — `is_recording` als bool reicht nicht (`STARTING`, `TRANSCRIBING`, `INJECTING` fehlen) | Explizites `State`-Enum, siehe [§5.3](#53-zustandsautomat) |
| A3 | Konfiguration hartkodiert (Hotkey, Sample Rate, Pfade) | `config.toml` + Pydantic-Modell + CLI-Overrides |
| A4 | Nur ein Event-Hook; Pipeline-Stufen sind nicht austauschbar | Protokoll-basierte Stages: `Transcriber`, `Refiner`, `Injector` |
| A5 | Kein Backpressure — bei schneller Doppel-Auslösung überlagern sich Aufnahmen | Single-Flight-Guard, weitere Trigger während `BUSY` verwerfen |
| A6 | Kein Speicherlimit — unbegrenzte Aufnahmelänge | Hartes Limit (z. B. 5 Min) + Warnton |
| A7 | Audiodatei bleibt unverschlüsselt liegen | Nach der Transkription löschen; `tempfile.NamedTemporaryFile` nutzen |

### 4.3 Betrieb & Reproduzierbarkeit

| ID | Befund | Empfehlung |
| :-- | :--- | :--- |
| B1 | **Kein Git-Repository** | `git init`, erster Commit vor allen Änderungen |
| B2 | Kein `.gitignore`, `__pycache__/` liegt im Projektordner | `.gitignore` mit `.venv/`, `__pycache__/`, `*.wav`, `.DS_Store` |
| B3 | `requirements.txt` nur mit `>=`, keine Lock-Datei | `uv` oder `pip-tools` → `requirements.lock` mit Hashes |
| B4 | `.venv` ist 1,2 GB, enthält **torch 2.13** (~1 GB) | Prüfen, ob torch tatsächlich zur Laufzeit gebraucht wird — mlx-whisper läuft auf MLX. Falls transitiv: `pip uninstall torch torchgen` und testen |
| B5 | `pyobjc` nicht explizit in `requirements.txt` (nur transitiv über pynput) | Explizit aufnehmen, damit die macOS-Abhängigkeit sichtbar ist |
| B6 | Kein Logfile, nur stdout | Zusätzlicher `RotatingFileHandler` nach `~/Library/Logs/FlowText/` |
| B7 | Kein Autostart, kein Menüleisten-Icon | `launchd`-plist + `rumps`-Statusicon (v1.2) |
| B8 | Keine Linting-/Format-Konfiguration | `ruff` + `mypy --strict` in `pyproject.toml` |

### 4.4 Was gut ist (bitte beibehalten)

- Trennung von `AudioRecorder` und `FlowTextApp` — sauberer Ausgangspunkt für die Modularisierung.
- Der Event-Hook `register_audio_stopped_hook` ist genau die richtige Naht für die STT-Pipeline.
- Die Erkenntnis über `\xa0` bei Option+Space in `_normalize_key` (Zeile 165) zeigt echtes Verständnis der Plattform.
- 16 kHz / Mono / float32 ist exakt das, was Whisper erwartet — keine Resampling-Kosten später.
- Die Wahl von MLX statt PyTorch-Whisper ist für den M4 die richtige Entscheidung.
- `debug_keys.py` als separates Diagnose-Tool ist gute Praxis.

---

## 5. Zielarchitektur

### 5.1 Modulstruktur

```
flow-text/
├── pyproject.toml              # Metadaten, ruff/mypy/pytest-Konfiguration
├── requirements.lock           # gepinnt, mit Hashes
├── config.toml                 # Nutzer-Konfiguration
├── .gitignore
├── README.md
├── flowtext/
│   ├── __init__.py
│   ├── __main__.py             # Entry Point, CLI-Parsing
│   ├── config.py               # Pydantic-Settings, TOML laden, Defaults
│   ├── state.py                # State-Enum + StateMachine + Observer
│   ├── permissions.py          # AXIsProcessTrusted, IOHIDCheckAccess, Diagnose
│   ├── hotkey/
│   │   ├── base.py             # Protocol: HotkeyBackend
│   │   ├── pynput_backend.py   # Event-Tap-Backend + Supervisor
│   │   └── carbon_backend.py   # (v2.0) RegisterEventHotKey
│   ├── audio/
│   │   ├── recorder.py         # deadlockfrei, Queue-basiert
│   │   └── devices.py          # Geräteauswahl, Hotplug-Handling
│   ├── pipeline/
│   │   ├── base.py             # Protocols: Transcriber, Refiner, Injector
│   │   ├── transcribe_mlx.py   # mlx-whisper
│   │   ├── refine_ollama.py    # Ollama-Nachbearbeitung
│   │   └── inject_macos.py     # pyperclip + Cmd+V
│   ├── feedback.py             # Sound, Notification, Menüleisten-Status
│   └── logging_setup.py
├── tools/
│   └── diagnose.py             # ersetzt debug_keys.py, erweitert
└── tests/
    ├── test_hotkey_parser.py
    ├── test_recorder.py        # mit Fake-Audio-Backend
    ├── test_state_machine.py
    └── test_pipeline.py        # mit Stub-Transcriber
```

### 5.2 Threading-Modell

Die zentrale Regel: **Der Event-Tap-Callback darf niemals blockieren.**

```
┌──────────────────────┐
│ CFRunLoop-Thread     │   pynput Event-Tap
│ (macOS, latenzkrit.) │   → NUR: queue.put_nowait(TriggerEvent)
└──────────┬───────────┘      Ziel: < 1 ms
           │ queue.Queue(maxsize=8)
           ▼
┌──────────────────────┐
│ Controller-Thread    │   Zustandsautomat, Start/Stop des Streams,
│ (Single-Flight)      │   Übergabe an die Pipeline
└──────────┬───────────┘
           │ queue.Queue
           ▼
┌──────────────────────┐
│ Pipeline-Worker      │   mlx-whisper → Ollama → Clipboard/Cmd+V
│ (CPU/GPU-intensiv)   │   darf Sekunden dauern
└──────────────────────┘

┌──────────────────────┐
│ PortAudio-Callback   │   NUR: audio_queue.put_nowait(chunk.copy())
│ (Realtime-Thread)    │   kein Lock, keine Allokation im heißen Pfad
└──────────────────────┘

┌──────────────────────┐
│ Supervisor-Thread    │   listener.join() → loggt Exception → Neustart
│ (Watchdog)           │   + Heartbeat-Prüfung des Taps
└──────────────────────┘
```

### 5.3 Zustandsautomat

```
IDLE ──(hotkey)──> RECORDING ──(hotkey)──> TRANSCRIBING ──> REFINING ──> INJECTING ──> IDLE
  ▲                    │                        │              │             │
  │                    │ (max. Dauer /          │              │             │
  │                    │  Escape = Abbruch)     │              │             │
  └────────────────────┴────────────────────────┴──────────────┴─────────────┘
                                    ERROR ──> IDLE (mit Fehlerton)
```

Hotkey-Trigger in allen Zuständen außer `IDLE` und `RECORDING` werden **verworfen** (Single-Flight). Jeder Zustandswechsel feuert ein Feedback-Event (Ton / Menüleisten-Icon).

---

## 6. Umsetzungsplan mit Akzeptanzkriterien

### Phase 0 — Absicherung (30 Min)

| # | Aufgabe |
| :-- | :--- |
| 0.1 | `git init`, `.gitignore` anlegen, aktuellen Stand committen |
| 0.2 | `__pycache__/` löschen |
| 0.3 | Aktuelle Versionen einfrieren: `pip freeze > requirements.lock` |
| 0.4 | Prüfen, ob `torch` entfernbar ist (`pip uninstall torch torchgen`, dann `python -c "import mlx_whisper"`) |

**Akzeptanz:** `git log` zeigt einen Baseline-Commit; `git status` ist sauber.

---

### Phase 1 — Hotkey reparieren (halber Tag) 🔴

| # | Aufgabe | Bezug |
| :-- | :--- | :--- |
| 1.1 | `permissions.py`: korrekte Prüfung mit `restype = c_bool` **und** `IOHIDCheckAccess`; bei fehlenden Rechten **fail-loud** mit Anleitung | U4, F5, F6 |
| 1.2 | Hotkey-Callback auf `queue.put_nowait()` reduzieren; Controller-Thread einführen | U1, F1 |
| 1.3 | Supervisor-Thread mit `listener.join()`, Exception-Logging und automatischem Neustart (max. 5 Versuche mit Backoff) | U2, F4 |
| 1.4 | Default-Hotkey auf kollisionsfreie Kombination ändern (`<f5>` oder Doppel-Tap rechte Option); alter Wert per Config wählbar | U3, F7 |
| 1.5 | `issubset` durch exakten Mengenvergleich ersetzen (Modifier-Obermengen ignorieren) | F8 |
| 1.6 | Beim Start `sys.executable`, `sys.version`, Elternprozess und beide Berechtigungsstatus loggen | U5 |

**Akzeptanz:**

- 20 aufeinanderfolgende Hotkey-Auslösungen ohne Aussetzer.
- Hotkey funktioniert weiterhin, nachdem in der Pipeline absichtlich eine Exception geworfen wurde (Supervisor greift, Log zeigt den Stacktrace).
- Bei entzogener Eingabeüberwachung startet das Programm **nicht** stillschweigend, sondern gibt eine klare Anleitung aus und beendet sich mit Exit-Code ≠ 0.
- Die Hotkey-Callback-Latenz liegt gemessen unter 1 ms (`time.perf_counter` um `put_nowait`).

---

### Phase 2 — Audio robust machen (halber Tag) 🟠

| # | Aufgabe | Bezug |
| :-- | :--- | :--- |
| 2.1 | `AudioRecorder` auf `queue.Queue` umstellen; Audio-Callback nimmt **kein** Lock | U6, F2, F3 |
| 2.2 | Stream-Lifecycle mit `try/finally`, Stream immer schließen | — |
| 2.3 | Leere Aufnahme sauber behandeln (kein `np.concatenate` auf leerer Liste) | F9 |
| 2.4 | Zieldatei über `tempfile` in `~/Library/Application Support/FlowText/tmp/`, absolute Pfade | F10 |
| 2.5 | Maximale Aufnahmedauer (Default 300 s) mit automatischem Stopp und Warnton | A6 |
| 2.6 | Mindestdauer (z. B. 0,3 s) — kürzere Aufnahmen verwerfen (Fehlbedienung) | — |
| 2.7 | Gerätewechsel abfangen (`PaErrorCode` bei getrenntem USB-Mikro) → sauber in `ERROR` → `IDLE` | — |

**Akzeptanz:**

- 100 Start/Stop-Zyklen in Folge, kein Hänger, kein Speicherwachstum (RSS-Messung).
- Ziehen des USB-Mikrofons während der Aufnahme führt zu einer Fehlermeldung, nicht zum Absturz.
- Aufnahme von 5 min wird automatisch beendet, Datei ist valide.

---

### Phase 3 — Modularisierung & Konfiguration (1 Tag) 🟡

| # | Aufgabe |
| :-- | :--- |
| 3.1 | Paketstruktur nach [§5.1](#51-modulstruktur) anlegen, `main.py` auflösen |
| 3.2 | `state.py` mit `State`-Enum und Observer-Callbacks |
| 3.3 | `config.toml` + Pydantic-Settings (Hotkey, Sample Rate, Modell, Ollama-Modell, Prompt, max. Dauer, Feedback an/aus) |
| 3.4 | `pipeline/base.py` mit `Protocol`-Definitionen für `Transcriber`, `Refiner`, `Injector` |
| 3.5 | `logging_setup.py`: Konsole + rotierendes Logfile |
| 3.6 | `ruff` + `mypy --strict` konfigurieren, bestehenden Code sauber bekommen |
| 3.7 | `tools/diagnose.py` — erweitert `debug_keys.py` um echten Aufnahmetest, Berechtigungs-Check und Hotkey-Kollisionsprüfung |

**Akzeptanz:** `ruff check` und `mypy --strict` laufen fehlerfrei; Hotkey und Sample Rate sind ohne Codeänderung über `config.toml` einstellbar.

---

### Phase 4 — Pipeline (v1.1, 1–2 Tage)

| # | Aufgabe |
| :-- | :--- |
| 4.1 | `transcribe_mlx.py`: Modell **beim Start vorladen** (nicht beim ersten Hotkey — sonst 10 s Wartezeit), `mlx-community/whisper-large-v3-turbo` als Default |
| 4.2 | Sprache konfigurierbar (`de`) statt Auto-Detect — spart Zeit und vermeidet Fehlerkennungen |
| 4.3 | `refine_ollama.py`: Ollama-Erreichbarkeit beim Start prüfen, Timeout setzen, bei Fehler auf Rohtranskript zurückfallen (nie blockieren) |
| 4.4 | `inject_macos.py`: Clipboard sichern → Text setzen → `Cmd+V` → Clipboard nach 500 ms wiederherstellen |
| 4.5 | `feedback.py`: `afplay` mit Systemsounds bei Start/Stopp/Fertig/Fehler |
| 4.6 | Temporäre WAV-Datei nach erfolgreicher Transkription löschen |

**Akzeptanz:**

- Ende-zu-Ende: Hotkey → 5 s sprechen → Hotkey → Text erscheint in TextEdit.
- Gemessene Latenz von „Stopp" bis „Text da" unter 3 s für 10 s Audio auf dem M4.
- Ollama gestoppt → Rohtranskript wird trotzdem eingefügt, Warnung im Log.

---

## 7. Konkrete Code-Bausteine

### 7.1 Korrekte Berechtigungsprüfung

```python
# flowtext/permissions.py
import ctypes
import sys
from dataclasses import dataclass
from enum import IntEnum


class HIDAccess(IntEnum):
    GRANTED = 0
    DENIED = 1
    UNKNOWN = 2


@dataclass(frozen=True)
class Permissions:
    accessibility: bool
    input_monitoring: HIDAccess

    @property
    def ok(self) -> bool:
        return self.accessibility and self.input_monitoring is HIDAccess.GRANTED


def check() -> Permissions:
    """Prüft beide für den Event-Tap relevanten macOS-Berechtigungen."""
    if sys.platform != "darwin":
        return Permissions(True, HIDAccess.GRANTED)

    apps = ctypes.cdll.LoadLibrary(
        "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
    )
    # WICHTIG: AXIsProcessTrusted gibt Boolean (1 Byte) zurück, nicht int (4 Byte).
    apps.AXIsProcessTrusted.restype = ctypes.c_bool
    apps.AXIsProcessTrusted.argtypes = []

    iokit = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/IOKit.framework/IOKit")
    iokit.IOHIDCheckAccess.restype = ctypes.c_int
    iokit.IOHIDCheckAccess.argtypes = [ctypes.c_uint32]

    K_IOHID_REQUEST_TYPE_LISTEN_EVENT = 1
    return Permissions(
        accessibility=bool(apps.AXIsProcessTrusted()),
        input_monitoring=HIDAccess(iokit.IOHIDCheckAccess(K_IOHID_REQUEST_TYPE_LISTEN_EVENT)),
    )


HELP = """\
FlowText benötigt zwei macOS-Berechtigungen für die App, aus der es gestartet wird
(Terminal.app, iTerm2, VS Code, Cursor – NICHT das Python-Binary):

  Systemeinstellungen -> Datenschutz & Sicherheit -> Bedienungshilfen
  Systemeinstellungen -> Datenschutz & Sicherheit -> Eingabeueberwachung

Danach die App mit Cmd+Q VOLLSTAENDIG beenden und neu starten.
Tipp: Zum Einrichten immer Terminal.app verwenden, nicht das IDE-Terminal.
"""


def require() -> None:
    """Fail-loud statt fail-open."""
    perms = check()
    if not perms.ok:
        print(f"Bedienungshilfen:   {'OK' if perms.accessibility else 'FEHLT'}")
        print(f"Eingabeueberwachung: {perms.input_monitoring.name}")
        print(HELP)
        raise SystemExit(2)
```

### 7.2 Nicht blockierender Hotkey-Callback

```python
# flowtext/hotkey/pynput_backend.py (Auszug)
import queue
import threading
from typing import Optional
from pynput import keyboard

TRIGGER = object()


class HotkeyBackend:
    def __init__(self, combo: set[str], events: queue.Queue) -> None:
        self._combo = combo
        self._events = events  # an den Controller-Thread
        self._pressed: set[str] = set()
        self._armed = True  # verhindert Auto-Repeat
        self._listener: Optional[keyboard.Listener] = None
        self._state_lock = threading.Lock()  # schuetzt NUR _pressed/_armed

    def _on_press(self, key) -> None:
        # Dieser Block muss in << 1 ms zurueckkehren, sonst deaktiviert
        # macOS den Event-Tap (kCGEventTapDisabledByTimeout) und pynput
        # reaktiviert ihn nie.
        name = normalize(key)
        if name is None:
            return
        with self._state_lock:
            self._pressed.add(name)
            # Exakter Vergleich: Modifier-Obermengen sollen NICHT ausloesen.
            modifiers = self._pressed & {"ctrl", "alt", "cmd", "shift"}
            target_mods = self._combo & {"ctrl", "alt", "cmd", "shift"}
            hit = self._combo <= self._pressed and modifiers == target_mods
            if hit and self._armed:
                self._armed = False
                fire = True
            else:
                fire = False
        if fire:
            try:
                self._events.put_nowait(TRIGGER)
            except queue.Full:
                pass  # Backpressure: ueberzaehlige Trigger verwerfen

    def _on_release(self, key) -> None:
        name = normalize(key)
        if name is None:
            return
        with self._state_lock:
            self._pressed.discard(name)
            if not self._combo <= self._pressed:
                self._armed = True
```

### 7.3 Deadlockfreier AudioRecorder

```python
# flowtext/audio/recorder.py (Auszug)
import queue
import numpy as np
import sounddevice as sd
import soundfile as sf


class AudioRecorder:
    def __init__(self, sample_rate: int = 16_000, max_seconds: float = 300.0) -> None:
        self.sample_rate = sample_rate
        self.max_frames = int(sample_rate * max_seconds)
        self._chunks: queue.Queue = queue.Queue()
        self._stream: sd.InputStream | None = None

    def _callback(self, indata, frames, time_info, status) -> None:
        # Realtime-Thread: KEIN Lock, keine I/O, kein Logging.
        # queue.put_nowait ist fuer diesen Zweck ausreichend guenstig.
        if status:
            self._status_flags = status
        self._chunks.put_nowait(indata.copy())

    def start(self) -> None:
        # Bewusst OHNE Lock: stream.start() kann den Callback feuern,
        # bevor es zurueckkehrt -> Lock hier waere ein Deadlock.
        self._drain()
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            blocksize=1024,
            callback=self._callback,
        )
        self._stream.start()

    def stop(self, path: str) -> str | None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass  # Geraet abgezogen o. Ae. -- Daten trotzdem retten

        chunks = self._drain()
        if not chunks:
            return None
        audio = np.concatenate(chunks, axis=0)[: self.max_frames]
        if len(audio) < self.sample_rate * 0.3:  # < 300 ms -> Fehlbedienung
            return None
        sf.write(path, audio, self.sample_rate, subtype="PCM_16")
        return path

    def _drain(self) -> list[np.ndarray]:
        out = []
        while True:
            try:
                out.append(self._chunks.get_nowait())
            except queue.Empty:
                return out
```

### 7.4 Listener-Supervisor / Watchdog

```python
# flowtext/hotkey/supervisor.py (Auszug)
import logging
import threading
import time

log = logging.getLogger(__name__)


class ListenerSupervisor(threading.Thread):
    """Erkennt den stillen Tod des pynput-Listeners und startet ihn neu.

    Hintergrund: pynput faengt Exceptions in Callbacks ab, ruft self.stop()
    und wirft sie erst in join() erneut. Ohne join() stirbt der Listener
    voellig lautlos.
    """

    def __init__(self, factory, max_restarts: int = 5) -> None:
        super().__init__(daemon=True, name="hotkey-supervisor")
        self._factory = factory  # Callable -> gestarteter Listener
        self._max_restarts = max_restarts
        self._stop = threading.Event()

    def run(self) -> None:
        restarts = 0
        while not self._stop.is_set():
            listener = self._factory()
            try:
                listener.join()  # blockiert; wirft die Callback-Exception
                log.warning("Hotkey-Listener regulaer beendet.")
            except Exception:
                log.exception("Hotkey-Listener durch Exception gestorben.")
            if self._stop.is_set():
                return
            restarts += 1
            if restarts > self._max_restarts:
                log.error("Zu viele Neustarts – FlowText gibt auf.")
                return
            backoff = min(2**restarts, 30)
            log.info("Neustart des Listeners in %ss (Versuch %s)", backoff, restarts)
            time.sleep(backoff)

    def stop(self) -> None:
        self._stop.set()
```

### 7.5 Alternative Hotkey-Muster: Doppel-Tap auf rechte Option

Kollisionsfrei, weil ein einzelner Modifier kein Systemkurzbefehl ist — das Muster, das Superwhisper verwendet.

```python
DOUBLE_TAP_WINDOW = 0.35  # Sekunden


def _on_press(self, key) -> None:
    if key != keyboard.Key.alt_r:
        self._last_tap = 0.0
        return
    now = time.monotonic()
    if now - self._last_tap < DOUBLE_TAP_WINDOW:
        self._last_tap = 0.0
        self._events.put_nowait(TRIGGER)
    else:
        self._last_tap = now
```

### 7.6 `.gitignore`

```gitignore
.venv/
__pycache__/
*.py[cod]
*.wav
.DS_Store
.mypy_cache/
.ruff_cache/
.pytest_cache/
logs/
config.local.toml
```

---

## 8. Testmatrix & Definition of Done

### Automatisiert (`pytest`)

| Test | Prüft |
| :--- | :--- |
| `test_hotkey_parser` | `<ctrl>+<alt>+<space>` → `{ctrl, alt, space}`; unbekannte Tokens werfen |
| `test_exact_modifier_match` | `Ctrl+Alt+Shift+Space` löst **nicht** aus, wenn `Ctrl+Alt+Space` konfiguriert ist |
| `test_recorder_empty` | Stopp ohne Audiodaten gibt `None` zurück statt zu werfen |
| `test_recorder_too_short` | < 300 ms wird verworfen |
| `test_recorder_max_length` | Aufnahme wird bei `max_seconds` abgeschnitten |
| `test_state_machine` | Trigger in `TRANSCRIBING` wird verworfen (Single-Flight) |
| `test_supervisor_restarts` | Fake-Listener, der wirft, wird neu gestartet |
| `test_pipeline_ollama_down` | Bei nicht erreichbarem Ollama wird das Rohtranskript durchgereicht |

### Manuell (macOS-spezifisch, nicht automatisierbar)

| Test | Erwartung |
| :--- | :--- |
| 20× Hotkey in Folge | Jedes Mal Reaktion, keine Aussetzer |
| Start ohne Eingabeüberwachung | Klare Fehlermeldung, Exit-Code 2 |
| Start aus IDE-Terminal ohne Rechte | Gleiche Meldung, Hinweis auf Terminal.app |
| Hotkey während Videocall (CPU-Last) | Weiterhin reaktionsfähig — der Tap-Timeout-Test |
| USB-Mikro während Aufnahme abziehen | Fehler geloggt, Rückkehr nach `IDLE`, kein Absturz |
| 5-Minuten-Aufnahme | Automatischer Stopp, valide WAV, kein Speicherleck |
| Text in TextEdit diktieren | Text erscheint, Clipboard-Inhalt danach wiederhergestellt |
| Hotkey in einem Textfeld drücken | **Kein** unsichtbares Zeichen wird eingefügt |
| `FlowText.app` Gatekeeper & LaunchServices Start (`open dist/FlowText.app`) | App startet ohne Gatekeeper-Blockade (`spctl: rejected`), Menüleisten-Icon 🎙 erscheint und TCC-Rechte (Bedienungshilfen, Eingabeüberwachung, Mikrofon) werden eigenständig für `FlowText.app` registriert |

### Definition of Done für v1.1

- [ ] Alle Punkte aus Phase 1 und 2 umgesetzt und manuell verifiziert
- [ ] `ruff check` und `mypy --strict` fehlerfrei
- [ ] Automatisierte Tests grün
- [ ] `README.md` mit Setup, Berechtigungs-Anleitung und Troubleshooting
- [ ] Ende-zu-Ende-Latenz „Stopp → Text" < 3 s für 10 s Audio
- [ ] 30 Minuten Dauerbetrieb ohne Aussetzer oder Speicherwachstum
- [ ] Temporäre Audiodateien werden nach der Verarbeitung gelöscht

---

## 9. Roadmap v1.1 – v2.0

### v1.1 — Funktionsfähige Pipeline
Phasen 0–4 dieses Dokuments. Ergebnis: zuverlässiges Diktat in jede App.

### v1.2 — UX & Performance
- Menüleisten-App via `rumps` mit Statusicon (grau / rot / Spinner)
- Push-to-Talk als Alternative zum Toggle
- Streaming-Transkription: Whisper läuft parallel zur Aufnahme auf 30-s-Fenstern
- Modellauswahl zur Laufzeit (`tiny` … `large-v3-turbo`)
- Mehrere Ollama-Prompt-Profile mit eigenen Hotkeys („Stichpunkte", „E-Mail-Ton", „nur Grammatik")
- Kontextsensitivität: aktive App erkennen und Prompt-Profil automatisch wählen
- `launchd`-Autostart

### v2.0 — Native Integration
- Hotkey über Carbon `RegisterEventHotKey` statt Event-Tap — reserviert den Kurzbefehl beim System, unterdrückt ihn korrekt und **benötigt keine TCC-Berechtigung**. Beseitigt U1, U3 und U4 auf einen Schlag.
- Signiertes `.app`-Bundle via `py2app` — dann hängen die Berechtigungen an FlowText selbst statt am Terminal (löst U5 endgültig)
- Vokabular-/Ersetzungslisten für Eigennamen und Fachbegriffe
- Sprecher-adaptives Prompting (häufige Fehltranskriptionen lernen)
- Optionale lokale Historie mit Volltextsuche (SQLite + FTS5)

---

## Quellen

- [Mac keyboard shortcuts — Apple Support](https://support.apple.com/en-us/102650) (Control–Space / Control–Option–Space = Eingabequelle wechseln)
- [pynput #389 — Silent failure without accessibility trust](https://github.com/moses-palmer/pynput/issues/389)
- [pynput #416 — Trouble with Permissions on Big Sur](https://github.com/moses-palmer/pynput/issues/416) (Python-Pfad *und* Terminal in Bedienungshilfen **und** Eingabeüberwachung eintragen)
- [pynput auf PyPI](https://pypi.org/project/pynput/)
- Quellcode des installierten Pakets: `.venv/lib/python3.12/site-packages/pynput/_util/darwin.py` (Zeilen 213–281), `pynput/_util/__init__.py` (Zeilen 222–247), `pynput/keyboard/_darwin.py` (Zeilen 235–365), `pynput/keyboard/_base.py` (Zeilen 685–700)
