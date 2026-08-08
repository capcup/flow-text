from __future__ import annotations

from pathlib import Path

import pytest

from flowtext.config import Config, ConfigError, conflicts, load, validate


def _write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(content, encoding="utf-8")
    return path


def test_defaults_are_valid() -> None:
    validate(Config())


def test_default_hotkey_is_not_the_macos_system_shortcut() -> None:
    """Regression: <ctrl>+<alt>+<space> ist 'nächste Eingabequelle'."""
    cfg = Config()
    assert cfg.hotkey.mode == "double_tap"
    assert "<alt>" not in cfg.hotkey.combo or "<space>" not in cfg.hotkey.combo


def test_default_trigger_is_conflict_free() -> None:
    """Regression: Doppel-Tap Option ist von Claude für Mac belegt."""
    cfg = Config()
    assert cfg.hotkey.tap_key == "cmd"
    assert conflicts(cfg) == []


class TestConflicts:
    def test_option_double_tap_warns_about_claude(self) -> None:
        cfg = Config()
        cfg.hotkey.tap_key = "alt_r"
        found = conflicts(cfg)
        assert len(found) == 1
        assert "Claude" in found[0]

    def test_left_option_warns_too(self) -> None:
        cfg = Config()
        cfg.hotkey.tap_key = "alt_l"
        assert conflicts(cfg)

    @pytest.mark.parametrize(
        ("combo", "expected"),
        [
            ("<ctrl>+<alt>+<space>", "Eingabequelle"),
            ("<ctrl>+<space>", "Eingabequelle"),
            ("<cmd>+<space>", "Spotlight"),
        ],
    )
    def test_reserved_combos_warn(self, combo: str, expected: str) -> None:
        cfg = Config()
        cfg.hotkey.mode = "combo"
        cfg.hotkey.combo = combo
        found = conflicts(cfg)
        assert len(found) == 1
        assert expected in found[0]

    def test_free_combo_does_not_warn(self) -> None:
        cfg = Config()
        cfg.hotkey.mode = "combo"
        cfg.hotkey.combo = "<ctrl>+<shift>+d"
        assert conflicts(cfg) == []

    def test_tap_key_conflict_ignored_in_combo_mode(self) -> None:
        cfg = Config()
        cfg.hotkey.mode = "combo"
        cfg.hotkey.tap_key = "alt_r"
        assert conflicts(cfg) == []

    def test_invalid_combo_does_not_crash(self) -> None:
        cfg = Config()
        cfg.hotkey.mode = "combo"
        cfg.hotkey.combo = "<ctrl>+<alt>"  # nur Modifier -> parse wirft
        assert conflicts(cfg) == []


def test_loads_toml(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        [hotkey]
        mode = "combo"
        combo = "<cmd>+<shift>+j"

        [audio]
        max_seconds = 60.0
        """,
    )
    cfg = load(path)
    assert cfg.hotkey.mode == "combo"
    assert cfg.hotkey.combo == "<cmd>+<shift>+j"
    assert cfg.audio.max_seconds == 60.0
    assert cfg.audio.sample_rate == 16000  # Default bleibt erhalten
    assert cfg.source == str(path)


def test_unknown_key_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, "[audio]\nsampel_rate = 16000\n")
    with pytest.raises(ConfigError, match="Unbekannter"):
        load(path)


def test_invalid_toml_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, "[audio\n")
    with pytest.raises(ConfigError, match="TOML"):
        load(path)


def test_missing_file_falls_back_to_defaults(tmp_path: Path) -> None:
    cfg = load(tmp_path / "gibt-es-nicht.toml")
    assert cfg.source == "defaults"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: setattr(c.hotkey, "mode", "morse"),
        lambda c: setattr(c.hotkey, "double_tap_window", 0.0),
        lambda c: setattr(c.audio, "sample_rate", 12345),
        lambda c: setattr(c.audio, "channels", 2),
        lambda c: setattr(c.audio, "min_seconds", 999.0),
    ],
)
def test_validation_catches_bad_values(mutate) -> None:
    cfg = Config()
    mutate(cfg)
    with pytest.raises(ConfigError):
        validate(cfg)


class TestPipelineValidation:
    def test_default_pipeline_is_valid(self) -> None:
        validate(Config())

    def test_defaults_use_the_real_stages(self) -> None:
        """Nach Phase 4 soll FlowText ohne Zutun tatsächlich diktieren."""
        cfg = Config()
        assert cfg.pipeline.transcriber == "mlx"
        assert cfg.pipeline.injector == "clipboard"
        assert cfg.pipeline.paste is True

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("transcriber", "mlx"),
            ("transcriber", "stub"),
            ("refiner", "ollama"),
            ("refiner", "none"),
            ("injector", "clipboard"),
            ("injector", "none"),
        ],
    )
    def test_implemented_stages_are_accepted(self, field: str, value: str) -> None:
        cfg = Config()
        setattr(cfg.pipeline, field, value)
        validate(cfg)

    @pytest.mark.parametrize("field", ["transcriber", "refiner", "injector"])
    def test_unknown_stage_is_rejected(self, field: str) -> None:
        cfg = Config()
        setattr(cfg.pipeline, field, "quatsch")
        with pytest.raises(ConfigError, match="unbekannt"):
            validate(cfg)
