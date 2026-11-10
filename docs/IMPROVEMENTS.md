# FlowText — Improvement Backlog

> **Source:** full code review and on-device measurements, 2026-10-27.
> **Status:** see the overview table; most items are still open. This is the single source of
> truth for open work. `FIXES.md` stays the record of what *was* fixed.
>
> **How to work with this file**
> - Pick the next item from [Recommended order](#recommended-order).
> - Before starting, re-check the item: code may have moved. References use
>   file + symbol names rather than line numbers so they stay valid.
> - When an item is done, set its status to `done (<date>, <commit>)` in the
>   overview and add a short entry to `FIXES.md`. Don't delete or renumber items.
> - New findings get the next free ID in their category. Keep each category
>   sorted by priority.

Baseline at time of writing: 266 tests passing, `ruff` and `mypy --strict`
clean. About 880 lines of uncommitted work (embedded bundle launcher,
`early_start`) were sitting on `main`. See D1.

**Categories:** **P** Performance · **Q** Quality (recognition accuracy) ·
**B** Bugs · **R** Robustness & privacy · **U** UX · **D** Docs, tooling & git

**Priorities:** **high** = do next, small effort or real damage ·
**med** = noticeable gain, more effort or depends on something ·
**low** = nice to have

---

## Recommended order

0. **D1**: commit the pending work so later changes stay reviewable.
1. **Q1 + Q2**: silence gate and vocabulary prompt. Small changes, fewer
   wrong outputs right away.
2. **P1**: pass numpy audio to mlx-whisper. Removes the ffmpeg dependency
   and is the basis for P3–P5.
3. **P2**: benchmark harness with *real* recordings. Required before P3.
4. **R1, B1, B2**, then **B3, B4**.
5. **P3**: shortened encoder window with fallback (~3× faster for 3–10 s
   dictations).
6. **Q5**, then **Q4** (filler-word option; rule stage first, it needs no LLM).
7. **P7**: Parakeet benchmark. If it wins, drop P3.
8. Remaining **med** items (R2–R4, P4–P6, U1, D2), then **low** items.

---

## Overview (sorted by priority, then category)

| ID | Prio | Category | Status | Summary |
|----|------|----------|--------|---------|
| P1 | high | Performance | done (2026-11-10) | Pass `np.ndarray` to mlx-whisper; drop WAV round trip and hidden ffmpeg dependency |
| P2 | high | Performance | done (2026-11-10) | `tools/bench.py`: latency + WER on real recordings (also measures quality) |
| Q1 | high | Quality | done (2026-11-10) | Silence/noise is transcribed as "Vielen Dank." → gate on RMS, blocklist |
| Q2 | high | Quality | done (2026-11-10) | Configurable vocabulary via `initial_prompt` (measured WER 4 % → 1 %) |
| Q6 | high | Quality | done (2026-11-10) | Fixed replacements after transcription (`cleanup.replacements`) |
| B1 | high | Bug | done (2026-11-10) | Clipboard restore wipes images/files; overwrites user copies |
| B2 | high | Bug | open | Config values not type-checked (`"false"` is truthy) |
| R1 | high | Robustness & privacy | open | Transcript text written to the log file at INFO |
| D1 | high | Docs/tooling/git | done (2026-11-10, 2905d8e) | Commit the pending uncommitted work on `main` |
| P3 | med  | Performance | open | Shortened encoder window (encoder = 89 % of latency) with guards |
| P4 | med  | Performance | open | Transcribe long dictations incrementally while recording |
| P5 | med  | Performance | open | Trim leading/trailing silence before transcription (helps Q1 too) |
| P6 | med  | Performance | open | Ollama: `keep_alive`, reuse client |
| P7 | med  | Performance | open | Benchmark Parakeet TDT v3 (`parakeet-mlx`) as an alternative transcriber |
| Q4 | med  | Quality | partial (2026-11-10) | Selectable text clean-up options (filler words first), rules + LLM. Rule stage done; LLM stage, guard and menu toggle open |
| Q5 | med  | Quality | open | Refiner: re-evaluate model (`qwen2.5:7b`), harden prompt and output guards |
| B3 | med  | Bug | open | `config.local.toml` replaces instead of overriding `config.toml` |
| B4 | med  | Bug | open | `set_paused` races with the controller thread |
| R2 | med  | Robustness & privacy | done (2026-11-10) | ⚠️ error icon is never visible (ERROR→IDLE instantly) |
| R3 | med  | Robustness & privacy | open | New audio devices (AirPods/USB) not seen while running |
| R4 | med  | Robustness & privacy | open | Config lookup depends on the working directory |
| U1 | med  | UX | open | Text is pasted into whatever app is in front at the *end* |
| D2 | med  | Docs/tooling/git | open | README/PROJECT_PLAN out of date |
| Q3 | low  | Quality | open | `condition_on_previous_text=False` against repetition loops |
| R5 | low  | Robustness & privacy | open | osascript Cmd+V needs an undocumented Automation permission |
| U2 | low  | UX | open | Trigger during PROCESSING is silently dropped |
| U3 | low  | UX | open | Secure Input silently disables the hotkey |
| U4 | low  | UX | open | Menu bar lacks reload, re-paste, mic selection, mic permission |
| D3 | low  | Docs/tooling/git | open | Mixed German/English in logs and UI |
| D4 | low  | Docs/tooling/git | open | CI: add Python 3.13 and coverage |

---

## Results 2026-11-10 (after P1, P2, Q1, Q2)

First run of `tools/bench.py` with the real `whisper-large-v3-turbo`, no
ffmpeg on `PATH`. Six clips: four TTS sentences (0.8–11.6 s), 2 s digital
silence, 3 s noise (σ = 0.003). **Still synthetic audio**, so this confirms
the mechanisms, not the thresholds.

| Variant | Median latency | Mean WER |
|---|---|---|
| `config` (vocabulary, filler removal, silence gate) | 918 ms | 1 % |
| `plain` (none of the three) | 952 ms | 36 % |

- Silence and noise: `plain` returns "Vielen Dank." for both; `config`
  skips them in 0 ms.
- A really spoken "Vielen Dank." (0.8 s) still comes through with the gate on.
- Vocabulary: "Flowtext mit Olamour" → "FlowText mit Ollama".
- The remaining 4 % on the long clip is "14 Uhr" vs. "vierzehn Uhr" (formatting).

## Measurements (2026-10-27)

**Setup:** Apple M4, 16 GB, macOS 26, Python 3.12, mlx-whisper 0.4.3,
`mlx-community/whisper-large-v3-turbo` (fp16), `language="de"`.
Test audio: 13 German sentences, 0.3–32 s, synthesised with
`say -v <German voice>` and converted with
`afconvert -f WAVE -d LEI16@16000 -c 1`.

> ⚠️ TTS audio is unusually clean. Every number below must be re-checked on
> real microphone recordings (P2) before anything is tuned against it.

### Latency

| Clip length | Transcription (median of 3–5 runs) |
|---|---|
| 0.3–10 s | 900–1,040 ms, almost independent of length |
| 32 s | 2,600 ms |

Breakdown for a 2.2 s clip (total ~965 ms):

| Step | Time |
|---|---|
| Log-mel spectrogram | 1 ms |
| **Encoder (padded 30 s window)** | **858 ms (89 %)** |
| Decoder and rest | ~100 ms |
| WAV write + ffmpeg decode (current path) vs numpy input | +~50 ms |
| `osascript` Cmd+V spawn | 23 ms |

**Conclusion:** the only real lever is the encoder window. File I/O,
ffmpeg and paste are noise.

### Shortened encoder window (experiment for P3)

Monkeypatch that lets the encoder accept fewer than 3000 mel frames:

```python
from mlx_whisper import whisper as W
import mlx.nn as nn


def enc_call(self, x):
    x = nn.gelu(self.conv1(x))
    x = nn.gelu(self.conv2(x))
    x = x + self._positional_embedding[: x.shape[1]]  # instead of the shape assert
    for b in self.blocks:
        x, _, _ = b(x)
    return self.ln_post(x)


W.AudioEncoder.__call__ = enc_call
# then: mel = log_mel_spectrogram(samples, n_mels=128)
#       seg = pad_or_trim(mel, frames, axis=-2).astype(mx.float16)
#       decode(model, seg, DecodingOptions(language="de", fp16=True, without_timestamps=True))
```

Results across 10 clips (`frames = (duration + padding) * 100`, even number):

| Window | Median latency | Correct |
|---|---|---|
| 30 s (standard) | 935–958 ms | 8/10 |
| length + 2 s | 378 ms | 5/10 |
| length + 3 s | 316 ms | 6/10 |
| max(length + 2 s, 10 s) | 328 ms | 7/10 |

The two "failures" in the 30 s row are formatting only ("14 Uhr" vs
"vierzehn Uhr", "9" vs "neun"), so they count as correct.

- **Clips ≥ 3.5 s:** identical text, 220–560 ms instead of ~950 ms, about 3× faster.
- **Clips < ~2.5 s:** repetition loops ("Ja. Ja. Ja. …") or wrong words
  ("Ok, machen Sie." for "Okay, mach ich."), even with a 10 s minimum window.

### Quality

- **Hallucination on silence:** 2 s of digital silence and 3 s of low noise
  (σ = 0.003) both return `"Vielen Dank."`, with and without
  `temperature=0` / `condition_on_previous_text=False`.
  `no_speech_prob` is **0.00** in those segments, so it is useless with turbo.
  RMS separates the two cleanly: speech 0.13, noise 0.003, silence 0.0.
- **`initial_prompt` with domain terms** (`"FlowText, Ollama, Whisper, Apple
  Silicon, Latenz, Transkription."`): WER on the 32 s clip 4 % → 1 %
  ("Olammer" → "Ollama", "Lattenz" → "Latenz"), no latency cost.
- **`temperature=0`, `condition_on_previous_text=False`:** no measurable
  difference on clean audio.
- **Not measured:** Ollama refinement (server wasn't running).

WER method: lower-case, strip everything except `\w äöüß` and spaces,
word-level Levenshtein distance divided by the number of reference words.

---

## Details by category

### P — Performance

**P1 — numpy input instead of WAV + ffmpeg** · high
- *Where:* `app.py` `_finish_recording` (`recorder.save` → `jobs.put_nowait(path)`),
  `pipeline/base.py` (`Transcriber.transcribe(audio_path)`),
  `transcribe_mlx.py` (`_transcribe_file`, `warmup` writes a WAV too).
- *Why:* the speed gain is only ~50 ms, but `mlx_whisper.transcribe(str_path)`
  shells out to the `ffmpeg` CLI, which the README never mentions. A fresh
  install without Homebrew fails on every dictation. It only works on the
  dev machine because of `/opt/homebrew/bin/ffmpeg`. The app bundle adds
  that directory to PATH; the CLI doesn't. This is also the basis for P3–P5.
- *Fix:* the `jobs` queue carries `Recording` (float32, 16 kHz mono, exactly
  what Whisper expects). Change the protocol to
  `transcribe(samples: np.ndarray, sample_rate: int)`. Keep an optional debug
  dump to WAV (`audio.keep_recordings`) instead of `delete_after_use`.
  Update `StubTranscriber` and its tests.
- *Done when:* no temp WAV in the normal path and no ffmpeg needed. Also
  removes the temp-file cleanup in `_cleanup_temp`.

- *Implemented 2026-11-10:* `Transcriber.transcribe(recording: Recording)`,
  `Pipeline.run(recording)`, the `jobs` queue carries `Recording`. No temp WAV
  in the normal path. `audio.delete_after_use` was kept (renaming it would
  reject existing configs): `false` now dumps each recording as WAV for
  debugging. Other sample rates are resampled to 16 kHz by linear
  interpolation (ffmpeg used to do this); 16 kHz stays the recommendation.
- *Verified:* real `whisper-large-v3-turbo` run with `PATH=/usr/bin:/bin`
  (no ffmpeg) on a TTS clip: ~1.0 s, correct text. The same clip also showed
  Q2 working on the real model ("Flowtext mit Olammer" → "FlowText mit
  Ollama"). Not tested: microphone audio, the built app bundle.

**P2 — Benchmark harness** · high
- *Fix:* `tools/bench.py`, which takes a folder of `*.wav` + `*.txt`
  references and prints latency (median) and WER per variant (model, window
  strategy, prompt). Add a way to collect real dictations: a config flag that
  keeps recordings plus the raw transcript, so references can be corrected by
  hand. Keep audio out of git (`.gitignore` already covers `*.wav`).
- *Done when:* about 20 real dictations (short, medium, long, quiet, noisy)
  can be benchmarked with one command. Prerequisite for P3 and for tuning Q1.

- *Implemented 2026-11-10:* `flowtext/bench.py` + `tools/bench.py <folder>`
  (`--plain`, `--model`, `--details`, `--repeats`). Runs the pipeline exactly
  as the app builds it, minus the injector. An empty `.txt` means "nothing
  should be recognised". Collecting: `audio.delete_after_use = false` now
  writes the transcript next to each WAV.
- *Still open:* the ~20 real dictations themselves. Only the user can record
  them.

**P3 — Shortened encoder window (largest speed lever)** · med
- *Depends on:* P1, P2.
- *Fix:* own transcription path (`mlx_whisper.decoding.decode` on a mel
  segment, plus the encoder patch above, ideally as a subclass/wrapper rather
  than a global monkeypatch), used **only** when
  - the clip is ≥ ~3 s (threshold to confirm with P2),
  - with a token cap proportional to duration (e.g. `sample_len` ≈ 6 tokens/s + margin),
  - with a fallback to the standard 30 s path when the result repeats
    (compression ratio > 2.4, the same n-gram ≥ 3×, or the token cap is hit).
- *Risk:* depends on mlx-whisper internals (`AudioEncoder`, `decode`). Pin or
  guard the version and fall back to the standard path on any exception.
- *Expected gain:* 3–10 s dictations go from ~950 ms to ~250–550 ms.

**P4 — Incremental transcription for long dictations** · med
- *Fix:* while recording, transcribe each completed ~25–30 s chunk in the
  worker, splitting at a quiet point. After stop, only the last chunk is
  left. Pass the previous chunk's text as the prompt for continuity.
- *Expected gain:* final latency roughly constant (~1 s) instead of growing
  ~0.9 s per 30 s.

**P5 — Trim silence** · med
- *Fix:* cut leading/trailing frames below the RMS threshold (keep ~200 ms of
  margin). That gives a shorter window (P3) and fewer hallucinations (Q1).

**P6 — Ollama keep-alive** · med
- *Where:* `pipeline/refine_ollama.py` `OllamaRefiner._chat`, `warmup`.
- *Fix:* create `ollama.Client` once; pass `keep_alive` (configurable, e.g.
  `"30m"`). Without it Ollama unloads the model after 5 min and the next
  refinement pays a cold load. Consider skipping refinement for very short
  texts. Measure with P2.

**P7 — Evaluate Parakeet TDT v3 as transcriber** · med (added 2026-11-10)
- *Depends on:* P1, P2.
- *Why:* Whisper pads every clip to 30 s, which is why the encoder costs
  ~860 ms even for a 2 s dictation (see Measurements). P3 works around that
  by patching mlx-whisper internals and fails on short clips. Parakeet
  (`nvidia/parakeet-tdt-0.6b-v3`, 25 European languages incl. German, runs
  via `parakeet-mlx`) has no fixed window, so latency scales with clip length.
- *Trade-offs to measure:* German WER vs. large-v3-turbo on real recordings;
  no `initial_prompt` (conflicts with Q2); language cannot be forced.
- *Fix:* new `transcriber = "parakeet"` behind the existing `Transcriber`
  protocol. Keep `mlx` as default until P2 numbers say otherwise. If
  Parakeet wins, P3 is obsolete.
- *Not worth it:* 4-bit/8-bit quantised turbo saves RAM (~1.6 GB → ~0.5–0.9 GB)
  but hardly any time on the GPU. Only relevant if RAM gets tight with a
  refiner model loaded.

### Q — Quality (recognition accuracy)

**Q1 — Silence gate + hallucination blocklist** · high
- *Where:* `app.py` `FlowTextApp._finish_recording` (it only warns at
  `peak < 0.001` and still transcribes); `pipeline/base.py` `Pipeline.run`.
- *Fix:* skip transcription when RMS is below a configurable threshold (start
  around 0.005–0.01 and calibrate with P2). Additionally drop outputs that
  match a small blocklist of known Whisper phantoms ("Vielen Dank.",
  "Untertitel im Auftrag des ZDF…", "Untertitel der Amara.org-Community",
  "Danke fürs Zuschauen") when the audio was quiet or short.
- *Done when:* silence/noise produce no paste; test covers both paths.

- *Implemented 2026-11-10:* `pipeline/guard.py` `SpeechGuard`, wired into
  `Pipeline.run`; `pipeline.silence_threshold` (default 0.005, 0 = off). The
  level is the RMS of the fourth-loudest 50 ms window rather than the
  whole-clip RMS, so pauses do not hide quiet speech and one key click does
  not count. Subtitle credits are always dropped, courtesies only below
  4 × threshold. The level is logged with every recording.
- *Still open:* calibrating 0.005 against real microphone recordings (P2).

**Q2 — Vocabulary prompt** · high
- *Where:* `config.py` `PipelineConfig`, `pipeline/transcribe_mlx.py`
  `MlxTranscriber._transcribe_file`.
- *Fix:* new option, e.g. `vocabulary = ["FlowText", "Ollama", …]`, joined
  into `initial_prompt`. Keep it short: Whisper's prompt budget is ~224 tokens.
- *Done when:* the option is documented in `config.toml` and the README, and
  passed through to the transcriber (unit test with a mocked `mlx_whisper`).

- *Implemented 2026-11-10:* `pipeline.vocabulary` → `vocabulary_prompt()` →
  `initial_prompt`. Warns above ~600 characters. Not verified against the
  real model in this session (mocked `mlx_whisper` only).
- *Known limit:* mlx-whisper 0.4.3 has no `carry_initial_prompt`. The prompt
  is the start of the token history, so in dictations beyond ~30 s the
  recognised text gradually pushes it out of the 224-token window, and a
  temperature fallback drops it entirely. Q6 covers what the hint misses.

**Q6 — Fixed replacements** · high (added and implemented 2026-11-10)
- `[cleanup.replacements]` table, `"heard" = "wanted"`. Whole words,
  case-insensitive, longest key first, applied by `RuleBasedCleaner` after
  filler removal and independent of `remove_fillers`. The hard complement to
  Q2, which is only a hint to Whisper.

**Q3 — Decode options** · low
- `condition_on_previous_text=False` (prevents loops carrying over between
  30 s windows in long dictations). No effect measured on clean audio.

**Q4 — Selectable clean-up options (filler words first)** · med (added 2026-11-10)
- *Goal:* the user ticks options such as "remove filler words"; more options
  follow later (self-corrections, punctuation, lists).
- *Observation:* Whisper already drops most pure hesitations ("äh", "ähm").
  What survives are word fillers ("halt", "sozusagen", "quasi", "irgendwie",
  "also" at sentence start, "ja" as a particle) and repetitions. Those are
  context dependent ("Halt an!" vs. "das ist halt so"), so a word list
  alone damages content.
- *Design:*
  1. Config: `[cleanup]` section with one bool per option
     (`remove_fillers`, later `apply_self_corrections`, …). Needs B2 so
     `"false"` is not truthy.
  2. `Pipeline` takes an ordered list of text stages instead of one
     `refiner`. Order: rule stage → LLM stage → injector.
  3. Rule stage (`pipeline/cleanup_rules.py`, no dependencies, <1 ms, works
     without Ollama): removes unambiguous hesitations (`äh|ähm|öh|öhm|hm|mhm`),
     immediate word repetitions ("ich ich"), then repairs commas/spacing/
     capitalisation left behind.
  4. LLM stage: `OllamaRefiner` builds its system prompt from the enabled
     options (one instruction block + one or two few-shot examples per option)
     instead of the single `ollama_prompt` string. Keep `ollama_prompt` as an
     optional full override.
  5. Guard for deletion-only options: the output's words must be an in-order
     subsequence of the input's words (normalised, small tolerance). Otherwise
     keep the rule-stage text. Much stricter than `_MAX_GROWTH_FACTOR`.
  6. If only `remove_fillers` is on and Ollama is unavailable, the rule stage
     still runs and a one-time warning says the result is partial.
  7. Menu bar: checkable items per option (ties into U4). The stages read the
     options per dictation, so a toggle needs no restart. Persisting a toggle
     needs a writable user config (R4/B3).
- *Implemented 2026-11-10 (steps 1 and 3, part of 2):* `[cleanup]` with
  `remove_fillers` and `extra_fillers` (type-checked in `validate`),
  `pipeline/cleanup_rules.py` `RuleBasedCleaner`, wired as
  `Pipeline(cleaner=…)` before the refiner. A dictation that consists only of
  fillers produces no paste. Tested on hand-written sentences only, not on
  real Whisper output; check with P2.
- *Still open:* steps 4–7 (prompt builder, subsequence guard, Ollama
  fallback warning, menu bar toggle) and the general stage list from step 2.
- *Note for Q2:* write the `initial_prompt` as clean prose. A prompt that
  contains fillers makes Whisper transcribe them.
- *Done when:* `remove_fillers = true` strips fillers with `refiner = "none"`
  (rules only) and with Ollama (rules + LLM), tests cover the rule stage, the
  prompt builder and the subsequence guard, and P2 shows the added latency.

**Q5 — Refiner model and guards** · med (added 2026-11-10)
- *Where:* `config.py` `PipelineConfig.ollama_model` / `ollama_prompt`,
  `pipeline/refine_ollama.py`.
- *Model:* `qwen2.5:7b` (Sept 2024, ~4.7 GB) is workable but not a good fit:
  German is not its strength, and at roughly 25–35 tokens/s on an M4 a
  50-word dictation adds an estimated 2–4 s to a ~1 s transcription
  (unmeasured, Ollama was not running). Candidates to benchmark with P2:
  `gemma3:4b` (strong German, ~3.3 GB, about twice as fast) as the likely
  default, `gemma3:12b` as the quality option (tight on 16 GB next to
  Whisper). Models with a thinking mode (qwen3 etc.) need `think=False`.
  Check what is current at implementation time.
- *Prompt:* the transcript goes in as a bare user message, so a dictated
  question or instruction ("Schreib mir …") can be answered instead of
  corrected. Wrap it in delimiters, say explicitly that it is data, add
  few-shot examples.
- *Options:* set `num_predict` relative to the input length so a runaway
  answer stops early instead of running into the 20 s timeout.
- *Guards:* `_MAX_GROWTH_FACTOR = 3.0` only catches gross cases. Add a
  similarity check (word-level ratio) and reject outputs that shrink far
  below the input unless a deleting option (Q4) is on.
- *Bug:* `_matches_available` accepts any tag with the same base name, so an
  installed `qwen2.5:14b` passes the warmup for `qwen2.5:7b` and every
  dictation then fails with a 404 (falls back to raw text, but logs a warning
  each time). Compare the full tag, treating a missing tag as `:latest`.
- *Skip short texts:* below ~4 words the refiner cannot improve anything.

### B — Bugs

**B1 — Clipboard restore loses data** · high
- *Where:* `pipeline/inject_macos.py` `ClipboardInjector._read_clipboard`,
  `_schedule_restore`.
- *Problem:* `pyperclip.paste()` returns `""` for images, files and rich text.
  `previous` is then `""` (not `None`), so the restore writes an empty string
  and the user's clipboard content is gone. Anything the user copies within
  `restore_clipboard_after` (1 s) is also overwritten.
- *Fix:* use `NSPasteboard` (pyobjc is already a dependency): snapshot all
  items/types, remember `changeCount` after our write, and restore only if it
  is unchanged. Mark our write with `org.nspasteboard.TransientType` /
  `ConcealedType` so clipboard managers skip the transcript.

- *Implemented 2026-11-10:* as described, plus: back-to-back dictations keep
  the original content, and the Transient marker is only set when the
  transcript will be removed again. `ConcealedType` was left out (it is meant
  for secrets). pyperclip stays as fallback and no longer restores `""`.
  Tested against a private `NSPasteboard`; not yet tried by hand with a real
  image and a clipboard manager.

**B2 — No type validation in config** · high
- *Where:* `config.py` `_apply`, `validate`.
- *Problem:* values are `setattr`'d unchecked. `early_start = "false"` and
  `paste = "false"` are truthy; `max_seconds = "300"` fails later inside the
  recorder.
- *Fix:* check against the dataclass field types (int is OK for float,
  `str | int | None` for `device`) and raise `ConfigError`. Add range checks:
  `min_seconds >= 0`, `max_tap_duration > 0`, `restore_clipboard_after >= 0`,
  valid `tap_key`.

**B3 — Config files are not merged** · med
- *Where:* `config.py` module docstring ("Werte werden gemergt") vs. `load`
  (`break` after the first file). The comment in `config.toml` promises
  override semantics.
- *Fix:* apply in order: defaults → `support_dir()/config.toml` →
  `./config.toml` → `./config.local.toml`. `cfg.source` should list all files used.

**B4 — Pause races with the controller** · med
- *Where:* `app.py` `FlowTextApp.set_paused` (called from the rumps main
  thread) touches `recorder`, `_tentative` and `state`. The docstring itself
  says `_tentative` belongs to the controller thread.
- *Fix:* put a `PAUSE`/`RESUME` signal on `self.events` and handle it in
  `_on_signal`. Keep `paused` as a read-only property for the UI.

### R — Robustness & privacy

**R1 — Transcripts in the log file** · high
- `pipeline/base.py` `Pipeline.run` logs the first 120 characters at INFO to
  `~/Library/Logs/FlowText/flowtext.log` (rotating, up to ~4 MB). Log only
  length and timing at INFO; text at DEBUG. `stub.LogInjector` is fine
  (explicit "log only" mode).

**R2 — Error state invisible** · med
- `app.py` `_to_error` transitions ERROR → IDLE back-to-back, while the menu
  bar polls every 200 ms (`menubar.py` `_tick`). Keep a "last error" (message
  + timestamp) visible for ~5 s or until the next trigger, and show
  `rumps.notification` with the reason.

- *Implemented 2026-11-10:* `app.Notice`; shown for 6 s or until the next
  recording, one `rumps.notification` per error. "No speech detected" is a
  hint without icon or notification. Not checked in the running menu bar app.

**R3 — Audio hot-plug** · med
- PortAudio enumerates devices once at init. In a long-running menu bar
  process, newly connected AirPods/USB mics are missing. On `AudioError` in
  `AudioRecorder.start`, re-init (`sd._terminate(); sd._initialize()`) once
  and retry.

**R4 — Config location depends on cwd** · med
- `config.candidate_paths` uses `Path.cwd()`. Running `flowtext` from another
  directory silently uses the defaults. Create
  `~/Library/Application Support/FlowText/config.toml` on first run (copy of
  the commented template) and treat it as the primary file. Closely related to B3.

**R5 — Paste mechanism** · low
- `inject_macos.py` `_press_paste_shortcut` uses `osascript … System Events`,
  which needs the **Automation** permission (a fourth TCC prompt,
  undocumented). Latency is *not* the issue (23 ms measured). Option:
  `CGEventPost` via Quartz (Accessibility only). At minimum, document the
  permission (see D2).

### U — UX

**U1 — Wrong target app** · med
- Record `NSWorkspace.frontmostApplication` when recording starts. If a
  different app is in front at paste time, copy only and notify.

**U2 — Busy feedback** · low
- `app.py` `_on_trigger` drops triggers in PROCESSING with only a log line.
  At minimum play a "busy" sound; better, allow the next recording while the
  previous one is processed (the `jobs` queue already exists; the state
  machine needs a separate "processing" flag).

**U3 — Secure Input** · low
- When a password field, 1Password or Terminal's "Secure Keyboard Entry" is
  active, the event tap gets no keys. Poll `IsSecureEventInputEnabled()`
  (Carbon) in the menu bar tick and show it.

**U4 — Menu bar items** · low
- Reload config, re-paste/copy last transcript, input device selection, link
  to the Microphone privacy pane.

### D — Docs, tooling & git

**D1 — Pending work** · high
- ~880 lines uncommitted on `main` as of 2026-10-27, all tests green. Commit
  before starting any other item.

**D2 — Doc drift** · med
- README config example says `double_tap_window = 0.40` / `max_tap_duration
  = 0.35`; the real defaults are 0.50 / 0.45.
- README Step 7 quotes German menu labels ("Berechtigungen fehlen",
  "Bedienungshilfen freigeben …"); `menubar.py` uses English ones.
- README says 223 tests; there are 266.
- The permission count differs between README and PROJECT_PLAN ("two",
  then lists three), and Automation is missing (R5). ffmpeg is missing (P1).

**D3 — Language** · low
- Log messages are half German, half English; the UI is English; config
  comments are German. Pick one for user-facing output.

**D4 — CI** · low
- `requires-python = ">=3.11"` without an upper bound, but CI only tests
  3.11/3.12. Add 3.13. Add `pytest-cov` to `dev` and report coverage.
