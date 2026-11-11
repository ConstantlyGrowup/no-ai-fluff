"""Deterministic English punctuation cleanup after patch merging (respects what the original slot already had)."""

from __future__ import annotations

import re

_RULES: list[tuple[str, re.Pattern[str], str]] = [
    ("space before punctuation", re.compile(r"(?<=\S)[ \t]+(?=[,.;:!?](?:\s|$))"), ""),
    ("doubled period", re.compile(r"(?<![.])\.\.(?!\.)"), "."),
    ("comma then period", re.compile(r",(?=\.)"), ""),
    ("period then comma", re.compile(r"(?<=\w)\.,"), ","),
    ("doubled comma", re.compile(r",{2,}"), ","),
    ("doubled space", re.compile(r"(?<=\S) {2,}(?=\S)"), " "),
    # patch merge artefact: "stems from the a change" (a patch replaced the noun phrase after an article)
    ("doubled article", re.compile(r"(?i)\b(?:the|a|an) (?=(?:the|a|an) )"), ""),
]


def clean_punctuation_en(text: str, reference: str = "") -> tuple[str, int]:
    total = 0
    for _name, pat, rep in _RULES:
        if reference and pat.search(reference):
            continue
        text, n = pat.subn(rep, text)
        total += n
    return text, total
