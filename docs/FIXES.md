# FlowText — Open Fixes

**Status 2026-08-10:** All previously documented issues have been fixed and
verified. Current state, measured by actually running the tools:

- `ruff check` — clean
- `ruff format --check` — 44 files, clean
- `mypy` — 27 source files, no issues
- `pytest` — 223 tests, all passing

One item remains, and it can only be resolved by pushing.

---

## 1. CI has never actually run

- **Location:** `.github/workflows/ci.yml`
- **Finding:** The workflow was added but never pushed, so its "green"
  status is unproven. Two concrete risks:
  - Every run installs `mlx-whisper` including `torch` (~2 GB) on a macOS
    runner, which may be slow or flaky even with `cache: 'pip'`.
  - Everything was verified locally on **Python 3.12 / arm64**; the CI
    matrix also tests **3.11**, which is untested territory.
- **Fix:** Push once and check the actual run. If it turns out too slow,
  split into a fast job (ruff, mypy, tests with stub stages) and a separate,
  less frequent job that installs the heavy pipeline extras.
- **Effort:** Depends on the first real run.

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
6. **No CI** — `.github/workflows/ci.yml` added.
7. **Orphaned packages** — the `PyAutoGUI` stack was removed from `.venv`.
8. **CI checked less than the local run** — the workflow ran
   `mypy --strict flowtext` (25 files), skipping `tools/`. It now runs plain
   `mypy`, which picks up the `pyproject.toml` config and checks the same 27
   files a local run does.

Beyond the documented scope, `tests/test_bundle.py` was also made hermetic by
mocking `subprocess.run` in `test_interpreter_without_dependencies` — the same
class of fix as item 2.

### Deliberately dropped: the lock file

A `requirements.lock` was generated (via `pip freeze`) and then deleted again.
Reasoning, recorded so the question doesn't get reopened by accident:

- Nothing consumed it. CI installs via `pip install -e ".[pipeline,menubar,dev]"`.
- Wiring it into CI would conflict with the test matrix: the freeze came from
  Python 3.12 / arm64, while CI also tests 3.11. A dedicated lock job would
  double CI runtime (another ~2 GB torch install) to validate a file nobody
  needs.
- It had no hashes and was platform-specific — it looked like reproducibility
  without providing it.
- `pyproject.toml` already carries version floors, and the actual problem
  found during the audit was venv drift, which "rebuild the venv from
  `pyproject.toml`" solves better than a hand-maintained freeze.

If reproducible installs become a real requirement later (multiple machines,
outside contributors, a broken upstream release), the right tool is
`uv lock` or `pip-compile` — a cross-platform, hash-pinned lock, not a
`pip freeze` snapshot.

**Checked, no fix needed:** `torch` (~1 GB) is a hard dependency of
`mlx-whisper`, not removable bloat. `dist/` is correctly gitignored.
