"""English audit rules for the host model (patches land on the original; the round trip is a reference only).

en1: first version, adapted from the Chinese v8 rules and from the hard-to-read features in Clark et al. (2026):
noun stacks, buried verbs (denominal verbs / nominalisations), inanimate subjects with animate verbs, reduced
object relatives and long dependencies, plus telegraphic compression and business jargon.
"""

from __future__ import annotations

PROBLEM_TYPES_EN1 = ("telegraphic", "noun-stack", "buried-verb", "inanimate-agent", "hard-clause", "jargon",
                     "coined-label", "empty-words", "missing-link", "redundant", "unclear-reference")

AUDIT_EN1 = """You are an English editor. Read the [Original] paragraph by paragraph and patch every place that is dense, hard to parse, or unlike how an experienced colleague would explain it face to face.
For each paragraph ask: would a colleague say it this way out loud? Where they would phrase it differently, write a patch. Leave genuinely natural sentences alone; output [] only if the whole text passes this test. Rewrite as much as the problem needs; there is no cap.

The [Round-trip reference] is the original machine-translated into another language and back. Its sentences are often looser and plainer, and you may borrow its wording.
It also brings machine-translation errors: wrong terms, odd collocations, changed numbers or hedges. Check every borrowed phrase against the original; terms, names, numbers and tone follow the original.

Check every item in [Radar hints]: fix it if it is a real problem, skip it if it is not.

What counts as a problem (each patch's "type" must be one of the bracketed names; if you cannot name the type, do not patch):
1. [telegraphic] dropped articles, verbs or objects; symbols such as "/", "+", "→", "—" standing in for "or", "and", "then", "because"; "Goal: ..." labels instead of sentences. Restore the missing words.
2. [noun-stack] three or more nouns stacked as modifiers ("write-path invalidation ordering change"). Unpack with of/for/that or a verb.
3. [buried-verb] the action hidden in a noun or a made-up verb ("drive alignment", "operationalize", "perform an evaluation of"). Say the action as a plain verb with a clear doer.
4. [inanimate-agent] a thing doing what only people do ("the data wants", "the architecture is designed to deliver") when it hides who actually acts. Name the actor when it matters.
5. [hard-clause] object relative clauses without "that" ("the bound the iteration applies"), a long gap between subject and verb, or one sentence carrying several ideas the reader must stop to unpack. Reorder, or split it into two or three sentences.
6. [jargon] business or spatial metaphors standing in for logic ("unlock", "landscape", "move the needle", "double-click on", "north star", "runway"). Say what actually happened, with the real quantity if there is one.
7. [coined-label] ad-hoc compressed phrases the reader must decode from context. Spell them out.
8. [empty-words] hype adjectives and slogans with no measurable content ("robust", "seamless", "holistic", "pivotal"). Give the concrete fact or delete.
9. [missing-link] reasoning jumps where the reader must supply "because", "so" or "which means".
10. [redundant] saying the same thing twice, preambles that only announce what follows, slogan endings.
11. [unclear-reference] "it", "this", "that" whose referent is ambiguous.

Invariants:
- After reading the result, the reader must know exactly what the original said: numbers, units, conditions, hedges, causal direction, who does what, and technical terms stay the same; add and remove no information.
- If a [User question] is given, keep the terms, metaphors and abbreviations it uses.
- Do not change document structure: headings, lists, tables and code blocks stay; the words inside them may change.
- Code, paths, formulas, URLs, numbers and anything in backticks stay verbatim and appear exactly once; you may move them within the sentence.

Output only a JSON array; each patch:
{"find": "exact span from the original", "replace": "new text", "type": "one of the eleven types", "reason": "why this is hard to read"}
- "find" is copied verbatim from the [Original], unique in the text, on one line; it may be a word, a sentence or a whole paragraph on that line (at most 400 characters).
- One patch per problem; patches must not overlap."""

# en2 (tuning round 2, 2026-11-11): on the tune set en1 removed jargon well but left long sentences unsplit
# (a 30-word sentence with a list wedged between subject and verb) and kept reduced relatives
# ("the benefits the architecture is meant to deliver"); hype adverbs such as "meaningfully" also survived.
# en2 = en1 with concrete triggers for [hard-clause] and [empty-words]; everything else unchanged.
AUDIT_EN2 = AUDIT_EN1.replace(
    "5. [hard-clause] object relative clauses without \"that\" (\"the bound the iteration applies\"), a long gap between subject and verb, or one sentence carrying several ideas the reader must stop to unpack. Reorder, or split it into two or three sentences.",
    "5. [hard-clause] sentences the reader must stop to unpack. Concretely:\n"
    "   - a sentence over about 25 words: split it into two or three sentences, one idea each;\n"
    "   - a list, aside or long phrase wedged between a subject and its verb (\"The overhead the pattern imposes, spanning A, B and C, competes with ...\"): "
    "say the main point first, then give the list in its own sentence;\n"
    "   - an object relative clause without \"that\" (\"the bound the iteration applies\", \"the benefits the architecture is meant to deliver\"): add \"that\" or rephrase with a verb.",
).replace(
    "8. [empty-words] hype adjectives and slogans with no measurable content (\"robust\", \"seamless\", \"holistic\", \"pivotal\"). Give the concrete fact or delete.",
    "8. [empty-words] hype adjectives, adverbs and slogans with no measurable content (\"robust\", \"seamless\", \"holistic\", \"pivotal\", \"meaningfully\", \"significantly\", \"truly\"). Give the concrete fact or delete.",
)
assert AUDIT_EN2 != AUDIT_EN1 and "about 25 words" in AUDIT_EN2 and "meaningfully" in AUDIT_EN2

RULES: dict[str, str] = {"en1": AUDIT_EN1, "en2": AUDIT_EN2}
PROBLEM_TYPES: dict[str, tuple[str, ...]] = {"en1": PROBLEM_TYPES_EN1, "en2": PROBLEM_TYPES_EN1}
MAX_FIND: dict[str, int] = {"en1": 400, "en2": 400}
DEFAULT_RULES = "en2"  # tuning round 2 (2026-11-11): best on the tune set; then frozen for the test set


def build_audit_prompt(original: str, rtt_draft: str, radar_text: str, rules: str = DEFAULT_RULES,
                       question: str | None = None) -> str:
    if rules not in RULES:
        raise ValueError(f"unknown audit rules {rules}; available: {', '.join(RULES)}")
    q = f"[User question]\n{question.strip()}\n\n" if question and question.strip() else ""
    return (f"{RULES[rules]}\n\n{q}[Original]\n{original}\n\n"
            "[Round-trip reference (plainer wording you may borrow, but it contains machine-translation errors; "
            f"check against the original)]\n{rtt_draft}\n\n"
            f"[Radar hints (machine-detected suspects in the original, for reference only)]\n{radar_text}\n\n"
            "Output only the JSON array:")
