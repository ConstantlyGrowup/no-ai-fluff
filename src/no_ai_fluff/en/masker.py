"""English masker: freeze code-like content only.

Differences from the Chinese masker:
- no "whole English sentence" freezing and no capitalised-word freezing (in English text that is the prose itself);
- no domain term list (in English, "Agent", "Token", "Prompt" are ordinary words the host may rephrase);
- hyphenated words are prose ("north-star-aligned", "flake-rate-driven") unless they contain a digit
  ("utf-8", "x86-64", "v2-beta"); identifiers in backticks are frozen by the code rule anyway;
- abbreviations such as "e.g." / "i.e." / "a.m." are not identifiers, and neither are word pairs such as "and/or";
- no soft placeholders: frozen content must survive verbatim.
"""

from __future__ import annotations

import re

from ..masker import _L, _PATTERNS, _R, Masker

_HYPHEN_WITH_DIGIT = _L + r"(?=[A-Za-z0-9]*-)(?=[A-Za-z0-9-]*\d)[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+" + _R
EN_PATTERNS: list[tuple[str, str]] = [(n, p) for n, p in _PATTERNS if n not in ("ensent", "kebab")]
EN_PATTERNS.insert([n for n, _ in EN_PATTERNS].index("acronym"), ("kebab", _HYPHEN_WITH_DIGIT))
EN_COMBINED = re.compile("|".join(f"(?P<{n}>{p})" for n, p in EN_PATTERNS), re.DOTALL)

ABBREVIATIONS = {"e.g", "i.e", "a.m", "p.m", "u.s", "u.k", "et.al", "vs"}
# "emails/messages", "Norway/Mexico", "and/or": two plain words joined by a slash are prose, not a path
_WORD_PAIR = re.compile(r"[A-Za-z]+/[A-Za-z]+")


class EnMasker(Masker):
    combined = EN_COMBINED
    default_terms: list[str] = []

    def __init__(self, extra_terms: list[str] | None = None, freeze_proper_nouns: bool = False):
        super().__init__(extra_terms, freeze_proper_nouns)

    def soft_kind(self, m: re.Match) -> bool:
        return False

    def _spans(self, text: str) -> list[tuple[int, int]]:
        return [(s, e) for s, e in super()._spans(text)
                if text[s:e].lower().rstrip(".") not in ABBREVIATIONS and not _WORD_PAIR.fullmatch(text[s:e])]
