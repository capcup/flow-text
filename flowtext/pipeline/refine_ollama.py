"""Nachbearbeitung des Rohtranskripts durch ein lokales LLM via Ollama.

Grundsatz: **die Verfeinerung darf nie zwischen dir und deinem Text stehen.**
Läuft Ollama nicht, ist das Modell nicht gezogen oder antwortet es zu
langsam, wird das Rohtranskript unverändert durchgereicht — mit einer
Warnung im Log, aber ohne Abbruch.

Deshalb prüft :meth:`warmup` die Erreichbarkeit beim Start und schaltet die
Stufe bei Bedarf für die Laufzeit ab, statt bei jedem Diktat in einen
Timeout zu laufen.
"""

from __future__ import annotations

import logging
import time
from typing import Any

log = logging.getLogger(__name__)

#: Schutz vor Modellen, die statt zu korrigieren anfangen zu antworten.
_MAX_GROWTH_FACTOR = 3.0


class OllamaRefiner:
    def __init__(
        self,
        model: str = "qwen2.5:7b",
        prompt: str = "",
        timeout: float = 20.0,
    ) -> None:
        self._model = model
        self._prompt = prompt
        self._timeout = timeout
        self._enabled = True

    # ------------------------------------------------------------------ API

    def warmup(self) -> None:
        """Prüft, ob Ollama läuft und das Modell vorhanden ist."""
        try:
            import ollama
        except ImportError as exc:
            self._disable(f"ollama-Bibliothek fehlt ({exc})")
            return

        try:
            client = ollama.Client(timeout=self._timeout)
            available = {m.get("model") or m.get("name") for m in client.list().get("models", [])}
        except Exception as exc:  # noqa: BLE001 — jede Störung heißt: Stufe aus
            self._disable(f"Ollama ist nicht erreichbar ({exc}) — läuft 'ollama serve'?")
            return

        if available and not self._matches_available(available):
            self._disable(
                f"Modell {self._model!r} ist nicht installiert. "
                f"Nachholen mit: ollama pull {self._model}"
            )
            return
        log.info("Ollama bereit (Modell %s).", self._model)

    def refine(self, text: str) -> str:
        if not self._enabled or not text.strip():
            return text

        started = time.perf_counter()
        try:
            refined = self._chat(text)
        except Exception as exc:  # noqa: BLE001 — Rohtext ist besser als kein Text
            log.warning("Ollama-Aufruf fehlgeschlagen (%s) — nutze das Rohtranskript.", exc)
            return text

        refined = refined.strip()
        if not refined:
            log.warning("Ollama lieferte leeren Text — nutze das Rohtranskript.")
            return text
        if len(refined) > len(text) * _MAX_GROWTH_FACTOR:
            log.warning(
                "Ollama-Antwort ist %.1f× so lang wie das Transkript — das Modell hat "
                "vermutlich geantwortet statt korrigiert. Nutze das Rohtranskript.",
                len(refined) / max(len(text), 1),
            )
            return text

        log.debug("Verfeinert in %.2fs.", time.perf_counter() - started)
        return refined

    # -------------------------------------------------------------- intern

    def _chat(self, text: str) -> str:
        import ollama

        client = ollama.Client(timeout=self._timeout)
        response: Any = client.chat(
            model=self._model,
            messages=[
                {"role": "system", "content": self._prompt},
                {"role": "user", "content": text},
            ],
            options={"temperature": 0.0},
        )
        return str(response["message"]["content"])

    def _matches_available(self, available: set[Any]) -> bool:
        """``qwen2.5:7b`` soll auch auf ``qwen2.5:7b-instruct-q4`` passen."""
        base = self._model.split(":")[0]
        return any(
            name and (name == self._model or str(name).split(":")[0] == base) for name in available
        )

    def _disable(self, reason: str) -> None:
        self._enabled = False
        log.warning("Verfeinerung deaktiviert: %s", reason)
