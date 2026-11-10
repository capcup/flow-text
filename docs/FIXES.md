# FlowText — Fixes

**Status 2026-08-10: no open items.** Everything documented here has been
fixed and verified, both locally and in CI.

> Open work found since then is tracked in [`IMPROVEMENTS.md`](IMPROVEMENTS.md).
> When an item there is done, record it here.

Local state (Python 3.12 / arm64):

- `ruff check` — clean
- `ruff format --check` — 44 files, clean
- `mypy` — 27 source files, no issues
- `pytest` — 223 tests, all passing

CI ([run #1](https://github.com/capcup/flow-text/actions/runs/31410682162),
commit `bc56162`): **green on both Python 3.11 and 3.12**, 2:10 minutes total.

---

## Minor, non-blocking

- `flowtext/__init__.py` still carries a hardcoded `"1.5.6"` as the
  `PackageNotFoundError` fallback. This is the conventional pattern and
  harmless, but it remains a second place where the version number lives and
  can drift from `pyproject.toml`.

---

## Resolved on 2026-08-10

For the record — all verified, no action needed:

1. **`mypy --strict` failure** — the dead `tomli` fallback branch in
   `flowtext/config.py` was removed in favour of a direct `import tomllib`
   (justified by `requires-python = ">=3.11"`).
2. **Environment-coupled test** — `test_missing_extras_degrade_visibly` now
   mocks `mlx_whisper` and `ollama` alongside `pyperclip`, making it
   independent of which packages happen to be installed.
3. **Formatting drift** — `tests/test_menubar.py` reformatted.
4. **Version out of sync** — `pyproject.toml` bumped to 1.5.6;
   `__init__.py` now reads the version via `importlib.metadata`.
5. **Duplicated dependencies** — `requirements.txt` now delegates to
   `pyproject.toml` via `-e .[pipeline,menubar,dev]`; README updated.
6. **No CI** — `.github/workflows/ci.yml` added: macOS runner, Python
   3.11/3.12 matrix, running ruff, ruff format, mypy and pytest on every
   push and pull request.
7. **Orphaned packages** — the `PyAutoGUI` stack was removed from `.venv`.
8. **CI checked less than the local run** — the workflow ran
   `mypy --strict flowtext` (25 files), skipping `tools/`. It now runs plain
   `mypy`, which picks up the `pyproject.toml` config and checks the same 27
   files a local run does.

Beyond the documented scope, `tests/test_bundle.py` was also made hermetic by
mocking `subprocess.run` in `test_interpreter_without_dependencies` — the same
class of fix as item 2.

### CI: two predicted risks, both disproven

Before the first run, two concerns were recorded here. The actual run
settled both, so they are noted rather than carried forward:

- **Python 3.11 was untested territory** (everything had been verified on
  3.12 only). It passed, and was in fact the faster of the two jobs — 67s
  versus 125s.
- **The `torch` install was expected to make runs slow or flaky**, estimated
  at "~2 GB per run". That estimate was wrong: it confused the installed
  on-disk size of the local `.venv` (1.2 GB) with the download size, and
  macOS arm64 wheels are far smaller than the CUDA Linux builds that figure
  came from. Real dependency installation took 39s (3.11) and 78s (3.12).

  Consequence: the previously suggested follow-up — splitting CI into a fast
  job and a separate heavy one — is unnecessary. The single job is fine.

### Deliberately dropped: the lock file

A `requirements.lock` was generated (via `pip freeze`) and then deleted again.
Reasoning, recorded so the question doesn't get reopened by accident:

- Nothing consumed it. CI installs via `pip install -e ".[pipeline,menubar,dev]"`.
- Wiring it into CI would conflict with the test matrix: the freeze came from
  Python 3.12 / arm64, while CI also tests 3.11. A dedicated lock job would
  add runtime to validate a file nobody needs.
- It had no hashes and was platform-specific — it looked like reproducibility
  without providing it.
- `pyproject.toml` already carries version floors, and the actual problem
  found during the audit was venv drift, which "rebuild the venv from
  `pyproject.toml`" solves better than a hand-maintained freeze.

If reproducible installs become a real requirement later (multiple machines,
outside contributors, a broken upstream release), the right tool is
`uv lock` or `pip-compile` — a cross-platform, hash-pinned lock, not a
`pip freeze` snapshot.

**Checked, no fix needed:** `torch` is a hard dependency of `mlx-whisper`,
not removable bloat. `dist/` is correctly gitignored.

## 2026-11-10 — items closed from `IMPROVEMENTS.md`

- **D1** — pending work committed (`2905d8e`).
- **Q4 (partial)** — rule-based filler removal, `cleanup.remove_fillers` /
  `extra_fillers` (`75753bc`). LLM stage and menu toggle still open.
- **Q2** — `pipeline.vocabulary` is passed to Whisper as `initial_prompt`.
- **Q6** — `[cleanup.replacements]` applies fixed corrections after
  transcription.

- **P1** — recordings go to Whisper in memory. No temp WAV, and the hidden
  `ffmpeg` dependency is gone.

Local state after these: `ruff`, `mypy --strict` clean, 319 tests passing.
P1 and Q2 were additionally run against the real model on a synthesised clip
with no ffmpeg on `PATH`. Nothing has been checked against real microphone
dictations yet.
