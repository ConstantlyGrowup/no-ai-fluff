"""English surface radar: cheap regex hints for the host, plus the surface metrics used in benchmarks.

The paper (Clark et al. 2026) measures these features with spaCy parses; here we only approximate the ones a
regex can see. Hints are suspects for the host to check, never edits.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass, field

from ..masker import PLACEHOLDER_RE

KINDS = ("long_sentence", "noun_stack", "nominal_heavy", "jargon", "symbol_for_word", "label_fragment")
KIND_LABELS = {
    "long_sentence": "long sentence",
    "noun_stack": "noun stack",
    "nominal_heavy": "action buried in nouns",
    "jargon": "jargon / hype word",
    "symbol_for_word": "symbol standing in for a word",
    "label_fragment": "label instead of a sentence",
}
PROMPT_HINT_LIMIT = 30
LONG_SENTENCE_WORDS = 30  # surface metric
HINT_SENTENCE_WORDS = 25  # radar hint for the host (en2)

JARGON = {
    "leverage", "leverages", "leveraging", "leveraged", "robust", "seamless", "seamlessly", "holistic", "holistically",
    "unlock", "unlocks", "unlocking", "empower", "empowers", "empowering", "ecosystem", "landscape", "journey",
    "pivotal", "frictionless", "operationalize", "operationalizing", "alignment", "north-star", "needle",
    "double-click", "runway", "net-net", "step-function", "posture", "de-risk", "de-risks", "de-risking", "unblock",
    "unblocks", "synergy", "synergies", "streamline", "streamlines", "delve", "delves", "reimagined", "reimagine",
    "unveil", "meaningful", "meaningfully", "game-changer", "game-changing", "cutting-edge", "best-in-class",
    "world-class", "actionable", "learnings", "bandwidth", "deep-dive", "move the needle", "low-hanging",
    "paradigm", "tapestry", "testament", "underscore", "underscores", "crucial", "vital", "foster", "fosters",
    "elevate", "elevates", "harness", "harnessing", "transformative", "comprehensive", "nuanced", "realm",
}
FUNCTION_WORDS = set("""a an the and or but nor so yet for of to in on at by with from into onto over under about after
before between through during without within across against among around as than then that this these those which who
whom whose what when where why how if because while although though since until unless whether is are was were be been
being am do does did done has have had having will would shall should can could may might must not no it its it's they
them their there here we our us you your i me my he she his her him also just only even very more most less least much
many some any each every all both either neither such own same other another one two three""".split())
_NOMINAL = re.compile(r"(?i)\b[a-z]{3,}(?:tion|sion|ment|ance|ence|ity|ness|ization|isation)s?\b")
_WORD = re.compile(r"[A-Za-z][A-Za-z'’]*(?:-[A-Za-z0-9]+)*")
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_SYMBOL = re.compile(r"(?<=\w)\s*(?:→|->|=>|\+|/)\s*(?=\w)|(?<=\w)\s+[—–]\s+(?=\w)")
_LABEL = re.compile(r"^\s*(?:[-*]\s+)?[A-Z][A-Za-z]*(?:\s+[A-Za-z]+)?:\s+\S")
_CODEISH = re.compile(r"```.*?```|`[^`\n]+`|\$[^$\n]+\$", re.S)


@dataclass
class Hint:
    kind: str
    slot: int | None
    excerpt: str


@dataclass
class EnRadarReport:
    hints: list[Hint] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        c = Counter(h.kind for h in self.hints)
        return {k: c.get(k, 0) for k in KINDS}

    def to_dict(self) -> dict:
        return {"counts": self.counts, "hints": [asdict(h) for h in self.hints]}

    def to_prompt(self, limit: int = PROMPT_HINT_LIMIT) -> str:
        if not self.hints:
            return "(no surface issues found; still read the whole text)"
        lines = [f'- [{KIND_LABELS[h.kind]}] "{h.excerpt}"' for h in self.hints[:limit]]
        if len(self.hints) > limit:
            lines.append(f"- ... and {len(self.hints) - limit} more")
        return "\n".join(lines)


def _content(w: str) -> bool:
    lw = w.lower()
    return lw not in FUNCTION_WORDS and not lw.endswith("ly") and not w[0].isupper()


def _noun_stacks(sentence: str) -> list[str]:
    """Runs of 4+ content words with no function word between them, or a hyphenated compound + 2 content words."""
    out: list[str] = []
    toks = [(m.group(0), m.start(), m.end()) for m in _WORD.finditer(sentence)]
    run: list[tuple[str, int, int]] = []

    def flush():
        if len(run) >= 4 or (len(run) >= 3 and any("-" in w for w, _, _ in run[:-1])):
            out.append(sentence[run[0][1]:run[-1][2]])
        run.clear()

    for k, tok in enumerate(toks):
        gap = sentence[toks[k - 1][2]:tok[1]] if k else " "
        if run and gap.strip():  # punctuation between words ends the run
            flush()
        if _content(tok[0]):
            run.append(tok)
        else:
            flush()
    flush()
    return out


def scan(slots: list[str]) -> EnRadarReport:
    rep = EnRadarReport()
    for i, s in enumerate(slots):
        t = PLACEHOLDER_RE.sub("X", s)
        if _LABEL.match(t):
            rep.hints.append(Hint("label_fragment", i, t.strip()[:80]))
        for sent in _SENT_SPLIT.split(t):
            words = _WORD.findall(sent)
            if len(words) > HINT_SENTENCE_WORDS:
                rep.hints.append(Hint("long_sentence", i, sent.strip()[:100]))
            if len(_NOMINAL.findall(sent)) >= 3:
                rep.hints.append(Hint("nominal_heavy", i, sent.strip()[:100]))
            for st in _noun_stacks(sent):
                rep.hints.append(Hint("noun_stack", i, st))
        low = t.lower()
        for w in sorted(JARGON):
            for m in re.finditer(r"(?<![A-Za-z-])" + re.escape(w) + r"(?![A-Za-z-])", low):
                rep.hints.append(Hint("jargon", i, t[m.start():m.end()]))
        for m in _SYMBOL.finditer(t):
            rep.hints.append(Hint("symbol_for_word", i, t[max(0, m.start() - 15):m.end() + 15].strip()))
    return rep


def surface(text: str) -> dict:
    """Surface metrics over a whole document (code, inline code and formulas removed)."""
    t = _CODEISH.sub(" ", text)
    words = _WORD.findall(t)
    sents = [s for s in _SENT_SPLIT.split(t) if _WORD.search(s)]
    lens = [len(_WORD.findall(s)) for s in sents]
    n = max(1, len(words))
    low = t.lower()
    rep = scan([t])
    return {
        "words": len(words),
        "sentences": len(sents),
        "words_per_sentence": round(sum(lens) / max(1, len(lens)), 2),
        "long_sentence_pct": round(100 * sum(1 for x in lens if x > LONG_SENTENCE_WORDS) / max(1, len(lens)), 2),
        "nominal_per_100w": round(100 * len(_NOMINAL.findall(t)) / n, 2),
        "jargon_per_1kw": round(1000 * rep.counts["jargon"] / n, 2),
        "noun_stack_per_1kw": round(1000 * rep.counts["noun_stack"] / n, 2),
        "symbol_per_1kw": round(1000 * rep.counts["symbol_for_word"] / n, 2),
        "relativizer_per_100w": round(100 * len(re.findall(r"\b(?:that|which|who)\b", low)) / n, 2),
    }
