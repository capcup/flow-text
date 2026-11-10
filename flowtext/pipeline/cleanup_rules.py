"""Regelbasierte Textbereinigung — ohne LLM, ohne Abhängigkeiten.

Entfernt nur, was **eindeutig** kein Inhalt ist:

* Zögerlaute ("äh", "ähm", "hm" …) samt der Kommas, die Whisper um sie setzt,
* direkte Wiederholungen von Funktionswörtern ("ich ich glaube"),
* Wörter, die der Nutzer selbst als Füllwort einträgt (``extra_fillers``).

Kontextabhängige Füller wie "halt" oder "also" bleiben bewusst stehen:
"Halt an!" und "das ist halt so" kann eine Wortliste nicht unterscheiden.
Wer sie trotzdem immer loswerden will, trägt sie in ``extra_fillers`` ein.

Die Stufe erfüllt das ``Refiner``-Protokoll und läuft vor der optionalen
LLM-Verfeinerung.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable

log = logging.getLogger(__name__)

#: Zögerlaute je Sprache. Bewusst ohne "eh" (dt. "das ist eh egal") und ohne
#: engl. "um"/"er" im Deutschen ("um zu", "er sagt").
_HESITATIONS: dict[str, str] = {
    "de": r"ä+h+m*|ö+h+m*|e+h+m+|h+m+|m+h+m+",
    "en": r"u+h+m*|u+m+|e+r+m+|h+m+|m+h+m+",
}
_HESITATIONS_FALLBACK = r"h+m+|m+h+m+"

#: Nach einem entfernten Zögerlaut bleibt das Komma nur vor diesen Wörtern
#: stehen ("Ich glaube, äh, dass …" → "Ich glaube, dass …").
_COMMA_BEFORE: dict[str, frozenset[str]] = {
    "de": frozenset(
        [
            "dass",
            "weil",
            "ob",
            "wenn",
            "obwohl",
            "damit",
            "sodass",
            "aber",
            "sondern",
            "denn",
            "während",
            "nachdem",
            "bevor",
            "falls",
        ]
    ),
    "en": frozenset(["but", "because", "although", "which", "while", "unless"]),
}

#: Nur diese Wörter gelten doppelt hintereinander als Versprecher. Artikel
#: und "sie" fehlen absichtlich ("der Mann, der der Frau hilft", "ob sie sie
#: sieht"), ebenso Verstärkungen wie "sehr sehr".
_REPEATABLE: dict[str, frozenset[str]] = {
    "de": frozenset(
        [
            "ich",
            "du",
            "wir",
            "ihr",
            "und",
            "oder",
            "aber",
            "ein",
            "eine",
            "in",
            "im",
            "mit",
            "für",
            "auf",
            "von",
            "ist",
            "hat",
            "wenn",
            "weil",
            "dass",
            "also",
            "dann",
        ]
    ),
    "en": frozenset(["i", "the", "a", "an", "and", "to", "of", "we", "in", "is"]),
}

#: Markiert eine Stelle, an der ein Satzanfang entfernt wurde; das nächste
#: Wort wird dort großgeschrieben.
_CAP = "\x00"

_SENTENCE_END = ".!?…"


class RuleBasedCleaner:
    def __init__(
        self,
        language: str | None = "de",
        *,
        remove_fillers: bool = True,
        extra_fillers: Iterable[str] = (),
    ) -> None:
        lang = (language or "").lower()
        self._remove_fillers = remove_fillers
        self._conjunctions = _COMMA_BEFORE.get(lang, frozenset())
        self._repeatable = _REPEATABLE.get(lang, frozenset())

        alternatives = [_HESITATIONS.get(lang, _HESITATIONS_FALLBACK)]
        # Längere Einträge zuerst, damit "so zu sagen" vor "so" greift.
        extras = sorted({w.strip() for w in extra_fillers if w.strip()}, key=len, reverse=True)
        alternatives += [r"\s+".join(map(re.escape, w.split())) for w in extras]

        self._filler = re.compile(
            r"(?P<pre>[,;]?\s*)"
            r"(?<!\w)(?:" + "|".join(alternatives) + r")(?!\w)"
            r"(?:\s*(?:\.{2,}|…))?"
            r"(?P<post>\s*[,;]?)"
            r"(?P<end>\s*[.!?])?",
            re.IGNORECASE,
        )

    # ------------------------------------------------------------------ API

    def warmup(self) -> None:
        if self._remove_fillers:
            log.info("Textbereinigung aktiv: Füllwörter werden entfernt (regelbasiert).")

    def refine(self, text: str) -> str:
        if not self._remove_fillers or not text.strip():
            return text

        cleaned = self._filler.sub(self._replace_filler, text)
        cleaned = self._drop_repetitions(cleaned)
        cleaned = self._tidy(cleaned)
        if cleaned != text.strip():
            log.debug("Bereinigt: %d → %d Zeichen.", len(text), len(cleaned))
        return cleaned

    # -------------------------------------------------------------- intern

    def _replace_filler(self, match: re.Match[str]) -> str:
        before = match.string[: match.start()].rstrip()
        after = match.string[match.end() :].lstrip()
        end = (match.group("end") or "").strip()

        at_start = not before or before[-1] in _SENTENCE_END or before[-1] == _CAP
        if at_start:
            # "Äh, das …" → "Das …"; ein Satz nur aus "Ähm." verschwindet ganz.
            return " " + _CAP
        if end or not after:
            # "… gut, ähm." → "… gut."
            return end

        had_pre = "," in match.group("pre") or ";" in match.group("pre")
        had_post = "," in match.group("post") or ";" in match.group("post")
        next_word = re.match(r"\w+", after)
        before_conjunction = (
            next_word is not None and next_word.group().lower() in self._conjunctions
        )

        # Ein Komma nur vor dem Füller gehörte zum Satz; eines nur danach oder
        # ein Paar gehörte zum Füller — außer es folgt eine Konjunktion.
        keep_comma = (had_pre and not had_post) or (had_post and before_conjunction)
        return ", " if keep_comma else " "

    def _drop_repetitions(self, text: str) -> str:
        if not self._repeatable:
            return text

        def repl(match: re.Match[str]) -> str:
            word = match.group(1)
            return word if word.lower() in self._repeatable else match.group(0)

        return re.sub(r"\b(\w+)(?:\s+\1\b)+", repl, text, flags=re.IGNORECASE)

    @staticmethod
    def _tidy(text: str) -> str:
        text = re.sub(r"\s+", " ", text)
        text = re.sub(r"\s+([,.;:!?…])", r"\1", text)
        text = re.sub(r",(?:\s*,)+", ",", text)
        text = re.sub(
            _CAP + r"+\s*(\w?)",
            lambda m: m.group(1).upper(),
            text,
        )
        text = text.replace(_CAP, "")
        return re.sub(r"\s+", " ", text).strip(" ,;")
