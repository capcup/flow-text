"""Tests der v1.2-Pipeline-Stufen.

mlx-whisper, ollama und pyperclip sind hier nicht installiert (und laufen
ohnehin nur auf macOS). Die Tests schieben deshalb Fake-Module in
``sys.modules`` — dadurch wird die Logik geprüft, nicht die Fremdbibliothek.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

from flowtext.config import PipelineConfig
from flowtext.pipeline.factory import build_pipeline
from flowtext.pipeline.stub import LogInjector, NullRefiner, StubTranscriber


def _fake_module(name: str, **attrs: Any) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


# ------------------------------------------------------------------- Factory


class TestFactory:
    def test_stub_configuration(self) -> None:
        cfg = PipelineConfig(transcriber="stub", refiner="none", injector="none")
        pipe = build_pipeline(cfg)
        assert isinstance(pipe.transcriber, StubTranscriber)
        assert isinstance(pipe.refiner, NullRefiner)
        assert isinstance(pipe.injector, LogInjector)

    def test_missing_extras_degrade_visibly(self, caplog) -> None:
        """Fehlt mlx-whisper, muss das beim START auffallen — nicht erst
        beim ersten Diktat, wenn der Import tief in der Stufe passiert."""
        cfg = PipelineConfig(transcriber="mlx", refiner="ollama", injector="clipboard")
        with caplog.at_level("ERROR", logger="flowtext.pipeline.factory"):
            pipe = build_pipeline(cfg)
        assert isinstance(pipe.transcriber, StubTranscriber)
        assert isinstance(pipe.refiner, NullRefiner)
        assert isinstance(pipe.injector, LogInjector)
        assert any("mlx-whisper" in r.message for r in caplog.records)

    def test_already_imported_module_counts_as_available(self, monkeypatch) -> None:
        """Regression: find_spec wirft bei Modulen ohne __spec__ ein
        ValueError — das hatte die echte Stufe fälschlich abgeschaltet."""
        from flowtext.pipeline.factory import _available

        monkeypatch.setitem(sys.modules, "pyperclip", _fake_module("pyperclip"))
        assert _available("pyperclip") is True

    def test_real_stages_when_extras_present(self, monkeypatch) -> None:
        monkeypatch.setitem(sys.modules, "mlx_whisper", _fake_module("mlx_whisper"))
        monkeypatch.setitem(sys.modules, "ollama", _fake_module("ollama"))
        monkeypatch.setitem(sys.modules, "pyperclip", _fake_module("pyperclip"))

        from flowtext.pipeline.inject_macos import ClipboardInjector
        from flowtext.pipeline.refine_ollama import OllamaRefiner
        from flowtext.pipeline.transcribe_mlx import MlxTranscriber

        pipe = build_pipeline(PipelineConfig())
        assert isinstance(pipe.transcriber, MlxTranscriber)
        assert isinstance(pipe.injector, ClipboardInjector)
        pipe = build_pipeline(PipelineConfig(refiner="ollama"))
        assert isinstance(pipe.refiner, OllamaRefiner)


# -------------------------------------------------------------- Transkription


class TestMlxTranscriber:
    def _install(self, monkeypatch, result: dict[str, Any] | Exception) -> list[dict[str, Any]]:
        calls: list[dict[str, Any]] = []

        def transcribe(path: str, **kwargs: Any) -> dict[str, Any]:
            calls.append({"path": path, **kwargs})
            if isinstance(result, Exception):
                raise result
            return result

        monkeypatch.setitem(
            sys.modules, "mlx_whisper", _fake_module("mlx_whisper", transcribe=transcribe)
        )
        return calls

    def test_returns_stripped_text(self, monkeypatch, tmp_path: Path) -> None:
        from flowtext.pipeline.transcribe_mlx import MlxTranscriber

        self._install(monkeypatch, {"text": "  hallo welt  ", "language": "de"})
        result = MlxTranscriber().transcribe(tmp_path / "a.wav")
        assert result.text == "hallo welt"
        assert result.language == "de"
        assert result.seconds >= 0

    def test_language_is_pinned(self, monkeypatch, tmp_path: Path) -> None:
        """Auto-Erkennung geht bei kurzen Diktaten regelmäßig daneben."""
        from flowtext.pipeline.transcribe_mlx import MlxTranscriber

        calls = self._install(monkeypatch, {"text": "x"})
        MlxTranscriber(language="de").transcribe(tmp_path / "a.wav")
        assert calls[0]["language"] == "de"

    def test_no_language_means_autodetect(self, monkeypatch, tmp_path: Path) -> None:
        from flowtext.pipeline.transcribe_mlx import MlxTranscriber

        calls = self._install(monkeypatch, {"text": "x"})
        MlxTranscriber(language=None).transcribe(tmp_path / "a.wav")
        assert "language" not in calls[0]

    def test_model_is_passed_through(self, monkeypatch, tmp_path: Path) -> None:
        from flowtext.pipeline.transcribe_mlx import MlxTranscriber

        calls = self._install(monkeypatch, {"text": "x"})
        MlxTranscriber(model="mlx-community/whisper-tiny").transcribe(tmp_path / "a.wav")
        assert calls[0]["path_or_hf_repo"] == "mlx-community/whisper-tiny"

    def test_failure_becomes_transcription_error(self, monkeypatch, tmp_path: Path) -> None:
        from flowtext.pipeline.transcribe_mlx import MlxTranscriber, TranscriptionError

        self._install(monkeypatch, RuntimeError("Modell kaputt"))
        with pytest.raises(TranscriptionError, match="Modell kaputt"):
            MlxTranscriber().transcribe(tmp_path / "a.wav")

    def test_warmup_failure_does_not_raise(self, monkeypatch) -> None:
        """Ein fehlgeschlagenes Warmup darf den Start nicht verhindern."""
        from flowtext.pipeline.transcribe_mlx import MlxTranscriber

        self._install(monkeypatch, RuntimeError("kein Netz"))
        MlxTranscriber().warmup()


# --------------------------------------------------------------- Verfeinerung


class _FakeOllamaClient:
    def __init__(self, models: list[str], reply: str | Exception = "korrigiert.") -> None:
        self._models = models
        self._reply = reply
        self.calls: list[dict[str, Any]] = []

    def list(self) -> dict[str, Any]:
        return {"models": [{"model": name} for name in self._models]}

    def chat(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if isinstance(self._reply, Exception):
            raise self._reply
        return {"message": {"content": self._reply}}


def _install_ollama(monkeypatch, client: Any) -> None:
    monkeypatch.setitem(sys.modules, "ollama", _fake_module("ollama", Client=lambda **kw: client))


class TestOllamaRefiner:
    def test_refines_text(self, monkeypatch) -> None:
        from flowtext.pipeline.refine_ollama import OllamaRefiner

        _install_ollama(monkeypatch, _FakeOllamaClient(["qwen2.5:7b"], "Hallo Welt."))
        refiner = OllamaRefiner(model="qwen2.5:7b")
        refiner.warmup()
        assert refiner.refine("hallo welt") == "Hallo Welt."

    def test_unreachable_ollama_disables_stage(self, monkeypatch) -> None:
        """Rohtext ist immer besser als kein Text."""
        from flowtext.pipeline.refine_ollama import OllamaRefiner

        class Dead:
            def list(self) -> dict[str, Any]:
                raise ConnectionError("connection refused")

        _install_ollama(monkeypatch, Dead())
        refiner = OllamaRefiner()
        refiner.warmup()
        assert refiner.refine("hallo welt") == "hallo welt"

    def test_missing_model_disables_stage(self, monkeypatch, caplog) -> None:
        from flowtext.pipeline.refine_ollama import OllamaRefiner

        _install_ollama(monkeypatch, _FakeOllamaClient(["llama3.2:1b"]))
        refiner = OllamaRefiner(model="qwen2.5:7b")
        with caplog.at_level("WARNING", logger="flowtext.pipeline.refine_ollama"):
            refiner.warmup()
        assert "ollama pull qwen2.5:7b" in caplog.text
        assert refiner.refine("hallo") == "hallo"

    def test_model_tag_variants_are_accepted(self, monkeypatch) -> None:
        from flowtext.pipeline.refine_ollama import OllamaRefiner

        client = _FakeOllamaClient(["qwen2.5:7b-instruct-q4_K_M"], "Ja.")
        _install_ollama(monkeypatch, client)
        refiner = OllamaRefiner(model="qwen2.5:7b")
        refiner.warmup()
        assert refiner.refine("ja") == "Ja."

    def test_chat_failure_returns_raw_text(self, monkeypatch) -> None:
        from flowtext.pipeline.refine_ollama import OllamaRefiner

        _install_ollama(monkeypatch, _FakeOllamaClient(["qwen2.5:7b"], TimeoutError("zu langsam")))
        refiner = OllamaRefiner(model="qwen2.5:7b")
        refiner.warmup()
        assert refiner.refine("hallo welt") == "hallo welt"

    def test_empty_reply_returns_raw_text(self, monkeypatch) -> None:
        from flowtext.pipeline.refine_ollama import OllamaRefiner

        _install_ollama(monkeypatch, _FakeOllamaClient(["qwen2.5:7b"], "   "))
        refiner = OllamaRefiner(model="qwen2.5:7b")
        refiner.warmup()
        assert refiner.refine("hallo welt") == "hallo welt"

    def test_chatty_model_is_ignored(self, monkeypatch) -> None:
        """Manche Modelle antworten statt zu korrigieren — das darf nicht
        als Diktat im Textfeld landen."""
        from flowtext.pipeline.refine_ollama import OllamaRefiner

        essay = "Gerne! Hier ist eine ausführliche Antwort auf deine Frage. " * 5
        _install_ollama(monkeypatch, _FakeOllamaClient(["qwen2.5:7b"], essay))
        refiner = OllamaRefiner(model="qwen2.5:7b")
        refiner.warmup()
        assert refiner.refine("hallo welt") == "hallo welt"

    def test_empty_input_short_circuits(self, monkeypatch) -> None:
        from flowtext.pipeline.refine_ollama import OllamaRefiner

        client = _FakeOllamaClient(["qwen2.5:7b"])
        _install_ollama(monkeypatch, client)
        refiner = OllamaRefiner(model="qwen2.5:7b")
        refiner.warmup()
        assert refiner.refine("  ") == "  "
        assert client.calls == []


# -------------------------------------------------------------------- Ausgabe


class _FakeClipboard:
    def __init__(self) -> None:
        self.content = "vorher"
        self.history: list[str] = []

    def copy(self, text: str) -> None:
        self.content = text
        self.history.append(text)

    def paste(self) -> str:
        return self.content


class TestClipboardInjector:
    def _install(self, monkeypatch) -> tuple[Any, list[str]]:
        clipboard = _FakeClipboard()
        monkeypatch.setitem(
            sys.modules,
            "pyperclip",
            _fake_module("pyperclip", copy=clipboard.copy, paste=clipboard.paste),
        )
        presses: list[str] = []
        monkeypatch.setattr(
            "flowtext.pipeline.inject_macos.ClipboardInjector._press_paste_shortcut",
            lambda self: presses.append("cmd+v"),
        )
        return clipboard, presses

    def test_copies_and_pastes(self, monkeypatch) -> None:
        from flowtext.pipeline.inject_macos import ClipboardInjector

        clipboard, presses = self._install(monkeypatch)
        injector = ClipboardInjector(restore_after=0)
        injector.inject("hallo welt")
        assert clipboard.history == ["hallo welt"]
        assert presses == ["cmd+v"]

    def test_paste_disabled_only_copies(self, monkeypatch) -> None:
        from flowtext.pipeline.inject_macos import ClipboardInjector

        clipboard, presses = self._install(monkeypatch)
        ClipboardInjector(paste=False).inject("hallo welt")
        assert clipboard.content == "hallo welt"
        assert presses == []

    def test_previous_clipboard_is_restored(self, monkeypatch) -> None:
        from flowtext.pipeline.inject_macos import ClipboardInjector

        clipboard, _ = self._install(monkeypatch)
        injector = ClipboardInjector(restore_after=0.01)
        injector.inject("diktat")
        assert clipboard.content == "diktat"
        timer = injector._timer
        assert timer is not None
        timer.join(timeout=2.0)
        assert clipboard.content == "vorher"

    def test_empty_text_does_nothing(self, monkeypatch) -> None:
        from flowtext.pipeline.inject_macos import ClipboardInjector

        clipboard, presses = self._install(monkeypatch)
        ClipboardInjector().inject("")
        assert clipboard.history == []
        assert presses == []

    def test_paste_failure_keeps_text_in_clipboard(self, monkeypatch) -> None:
        from flowtext.pipeline.inject_macos import ClipboardInjector

        clipboard, _ = self._install(monkeypatch)

        def boom(self: Any) -> None:
            raise RuntimeError("keine Bedienungshilfen")

        monkeypatch.setattr(
            "flowtext.pipeline.inject_macos.ClipboardInjector._press_paste_shortcut", boom
        )
        ClipboardInjector().inject("wichtiger text")
        assert clipboard.content == "wichtiger text"

    def test_shutdown_cancels_pending_restore(self, monkeypatch) -> None:
        from flowtext.pipeline.inject_macos import ClipboardInjector

        clipboard, _ = self._install(monkeypatch)
        injector = ClipboardInjector(restore_after=5.0)
        injector.inject("diktat")
        injector.shutdown()
        assert injector._timer is None
        assert clipboard.content == "diktat"


# ------------------------------------------------- Rückwirkung auf den Hotkey


def test_simulated_paste_does_not_trigger_the_hotkey() -> None:
    """Regression: das eigene Cmd+V sieht unser Listener selbst.

    Die Sequenz cmd↓ v↓ v↑ cmd↑ darf keinen Doppel-Tap ergeben — sonst
    startet FlowText direkt nach dem Einfügen eine neue Aufnahme.
    """
    from flowtext.hotkey.detectors import DoubleTapDetector

    detector = DoubleTapDetector("cmd")
    fired = [
        detector.on_press("cmd_l", 0.00),
        detector.on_press("v", 0.01),
        detector.on_release("v", 0.02),
        detector.on_release("cmd_l", 0.03),
    ]
    assert fired == [False, False, False, False]

    # …und direkt danach muss ein echter Doppel-Tap wieder greifen.
    detector.on_press("cmd_l", 1.00)
    assert detector.on_release("cmd_l", 1.03) is False
    detector.on_press("cmd_l", 1.15)
    assert detector.on_release("cmd_l", 1.18) is True
