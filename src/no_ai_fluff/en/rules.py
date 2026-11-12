"""English audit rules for the host model (patches land on the original; the round trip is a reference only).

Two architecture generations are kept (intermediate tuning rounds are in benchmarks/optimization_ledger.jsonl and git history):

v1 (ledger name en2): single channel. Adapted from the Chinese first-generation rules and from the hard-to-read
    features in Clark et al. (2026): noun stacks, buried verbs (nominalisations), inanimate subjects with animate
    verbs, reduced object relatives and long dependencies, plus telegraphic compression and business jargon.
v2 (ledger name en5, default): two gated channels, the Chinese second generation ported to English.
    - The runtime radar looks only at sentence structure (no static jargon list) and adds a statistical
      "telegraphic sentence chain" hint.
    - A domain triage (telegraph_router, English prompt) asks whether those sentences come from a technical or
      engineering context. If yes, the whole document gets AUDIT_EN5_DENSE (genuine terms vs packaging jargon,
      the round trip as a jargon detector, type 12 [telegraph-stack]); if not, or no chain was found, the prompt
      is v1 verbatim.
"""

from __future__ import annotations

PROBLEM_TYPES_EN2 = ("telegraphic", "noun-stack", "buried-verb", "inanimate-agent", "hard-clause", "jargon",
                     "coined-label", "empty-words", "missing-link", "redundant", "unclear-reference")
PROBLEM_TYPES_EN5 = PROBLEM_TYPES_EN2 + ("telegraph-stack",)

AUDIT_EN2 = """You are an English editor. Read the [Original] paragraph by paragraph and patch every place that is dense, hard to parse, or unlike how an experienced colleague would explain it face to face.
For each paragraph ask: would a colleague say it this way out loud? Where they would phrase it differently, write a patch. Leave genuinely natural sentences alone; output [] only if the whole text passes this test. Rewrite as much as the problem needs; there is no cap.

The [Round-trip reference] is the original machine-translated into another language and back. Its sentences are often looser and plainer, and you may borrow its wording.
It also brings machine-translation errors: wrong terms, odd collocations, changed numbers or hedges. Check every borrowed phrase against the original; terms, names, numbers and tone follow the original.

Check every item in [Radar hints]: fix it if it is a real problem, skip it if it is not.

What counts as a problem (each patch's "type" must be one of the bracketed names; if you cannot name the type, do not patch):
1. [telegraphic] dropped articles, verbs or objects; symbols such as "/", "+", "→", "—" standing in for "or", "and", "then", "because"; "Goal: ..." labels instead of sentences. Restore the missing words.
2. [noun-stack] three or more nouns stacked as modifiers ("write-path invalidation ordering change"). Unpack with of/for/that or a verb.
3. [buried-verb] the action hidden in a noun or a made-up verb ("drive alignment", "operationalize", "perform an evaluation of"). Say the action as a plain verb with a clear doer.
4. [inanimate-agent] a thing doing what only people do ("the data wants", "the architecture is designed to deliver") when it hides who actually acts. Name the actor when it matters.
5. [hard-clause] sentences the reader must stop to unpack. Concretely:
   - a sentence over about 25 words: split it into two or three sentences, one idea each;
   - a list, aside or long phrase wedged between a subject and its verb ("The overhead the pattern imposes, spanning A, B and C, competes with ..."): say the main point first, then give the list in its own sentence;
   - an object relative clause without "that" ("the bound the iteration applies", "the benefits the architecture is meant to deliver"): add "that" or rephrase with a verb.
6. [jargon] business or spatial metaphors standing in for logic ("unlock", "landscape", "move the needle", "double-click on", "north star", "runway"). Say what actually happened, with the real quantity if there is one.
7. [coined-label] ad-hoc compressed phrases the reader must decode from context. Spell them out.
8. [empty-words] hype adjectives, adverbs and slogans with no measurable content ("robust", "seamless", "holistic", "pivotal", "meaningfully", "significantly", "truly"). Give the concrete fact or delete.
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

AUDIT_EN5_DENSE = """You are an English editor. Read the [Original] paragraph by paragraph and patch every place that is dense, hard to parse, or unlike how an experienced colleague would explain it face to face.
For each paragraph ask: would a colleague say it this way out loud? Where they would phrase it differently, write a patch. Leave genuinely natural sentences alone; output [] only if the whole text passes this test. Rewrite as much as the problem needs; there is no cap.

The [Round-trip reference] is the original machine-translated into another language and back. Its sentences are often looser and plainer, and you may borrow its wording.
It also brings machine-translation errors: wrong terms, odd collocations, changed numbers or hedges. Check every borrowed phrase against the original; terms, names, numbers and tone follow the original.

[First tell genuine terms from packaging jargon]
- Genuine terms stay exactly as written: concepts with one strict meaning in textbooks, papers, standards or code, where any plain substitute would mislead an expert. Examples: idempotent, race condition, deadlock, P99 latency, amortized O(1), Hessian eigenvalue, ablation study, CPE, TTL, monorepo.
- Packaging jargon is not protected: words borrowed from business, sports, the military or physics to dress up an ordinary action or state. Examples: "drive alignment" (agree on), "de-risk the migration runway" (lower the risk of the migration), "step-function improvement in tail-latency posture" (a large drop in tail latency), "operationalize", "unlock", "surface area", "optionality", "north star". Turn it into the plain action a colleague would say, without changing any fact.
- When unsure, ask: would a plain substitute make an expert misunderstand? If yes, it is a genuine term; keep it. If no, it is jargon; rewrite it.
- Rewriting jargon means saying what the word itself refers to (you may borrow the reference's plainer wording); it never means adding consequences, branches or mechanisms the author did not write.

[The round-trip reference as a jargon detector]
Genuine terms survive the round trip almost unchanged (idempotent, deadlock, P99). Coined jargon has no idiom in the other language, so it usually comes back as plain words ("drive alignment" -> "agree", "de-risks the runway" -> "reduces the risk of the migration"). If a technical-looking phrase in the original came back as an ordinary phrase meaning the same thing, it is most likely jargon: follow the reference toward the plain wording.

Check every item in [Radar hints]: fix it if it is a real problem, skip it if it is not. A "telegraphic sentence chain" that only lists concrete things (fields, test cases, tools) does not need to be split, but its dropped verbs and articles are still type 1; a chain of action + abstract-noun phrases is type 12.

What counts as a problem (each patch's "type" must be one of the bracketed names; if you cannot name the type, do not patch):
1. [telegraphic] dropped articles, verbs or objects; symbols such as "/", "+", "→", "—" standing in for "or", "and", "then", "because"; "Goal: ..." labels instead of sentences. Restore the missing words.
2. [noun-stack] three or more nouns stacked as modifiers ("write-path invalidation ordering change"). Unpack with of/for/that or a verb.
3. [buried-verb] the action hidden in a noun or a made-up verb ("drive alignment", "operationalize", "perform an evaluation of"). Say the action as a plain verb with a clear doer.
4. [inanimate-agent] a thing doing what only people do ("the data wants", "the architecture is designed to deliver") when it hides who actually acts. Name the actor when it matters.
5. [hard-clause] sentences the reader must stop to unpack. Concretely:
   - a sentence over about 25 words: split it into two or three sentences, one idea each;
   - a list, aside or long phrase wedged between a subject and its verb ("The overhead the pattern imposes, spanning A, B and C, competes with ..."): say the main point first, then give the list in its own sentence;
   - an object relative clause without "that" ("the bound the iteration applies", "the benefits the architecture is meant to deliver"): add "that" or rephrase with a verb.
6. [jargon] business or spatial metaphors standing in for logic ("unlock", "landscape", "move the needle", "double-click on", "north star", "runway"). Say what actually happened, with the real quantity if there is one.
7. [coined-label] ad-hoc compressed phrases the reader must decode from context. Spell them out.
8. [empty-words] hype adjectives, adverbs and slogans with no measurable content ("robust", "seamless", "holistic", "pivotal", "meaningfully", "significantly", "truly"). Give the concrete fact or delete.
9. [missing-link] reasoning jumps where the reader must supply "because", "so" or "which means".
10. [redundant] saying the same thing twice, preambles that only announce what follows, slogan endings.
11. [unclear-reference] "it", "this", "that" whose referent is ambiguous.
12. [telegraph-stack] one sentence chaining several action + abstract-noun phrases ("Post-merge, stale-read windows the old flow never exposed now surface under concurrent checkout load"), which the reader must take apart. Split it into two or three complete sentences, even if it contains genuine terms; add only the subjects and linking words ("first", "then", "so") that the context already gives; never add conditions, branches or mechanisms the original does not state.

Invariants:
- After reading the result, the reader must know exactly what the original said: numbers, units, conditions, hedges, causal direction, who does what, and genuine technical terms stay the same; add and remove no information. When you turn jargon into plain words, say only what the original meant and invent no mechanism or detail. Keep signs, order, direction and sequence exactly as written, even if you think the original is wrong.
- If a [User question] is given, keep the terms, metaphors and abbreviations it uses.
- Do not change document structure: headings, lists, tables and code blocks stay; the words inside them may change.
- Code, paths, formulas, URLs, numbers and anything in backticks stay verbatim and appear exactly once; you may move them within the sentence.

Output only a JSON array; each patch:
{"find": "exact span from the original", "replace": "new text", "type": "one of the twelve types", "reason": "why this is hard to read"}
- "find" is copied verbatim from the [Original], unique in the text, on one line; it may be a word, a sentence or a whole paragraph on that line (at most 400 characters).
- One patch per problem; patches must not overlap."""
assert "about 25 words" in AUDIT_EN2 and "meaningfully" in AUDIT_EN2 and "telegraph-stack" not in AUDIT_EN2
assert AUDIT_EN5_DENSE.count("12. [telegraph-stack]") == 1 and "still type 1" in AUDIT_EN5_DENSE

TELEGRAPH_MARK = "[telegraphic sentence chain]"
RULE_ALIASES = {"v1": "en2", "v2": "en5"}  # generation name -> ledger name
RULES: dict[str, str] = {"v1": AUDIT_EN2, "en2": AUDIT_EN2, "v2": AUDIT_EN5_DENSE, "en5": AUDIT_EN5_DENSE}
PROBLEM_TYPES: dict[str, tuple[str, ...]] = {"v1": PROBLEM_TYPES_EN2, "en2": PROBLEM_TYPES_EN2,
                                             "v2": PROBLEM_TYPES_EN5, "en5": PROBLEM_TYPES_EN5}
MAX_FIND: dict[str, int] = {r: 400 for r in RULES}
GATED_RULES = {"en5": "en2", "v2": "v1"}  # no technical telegraphic chain -> the v1 prompt verbatim
ROUTED_RULES = {"en5", "v2"}  # telegraph hints go through the domain triage first
DENSE_RADAR_RULES = {"en5", "v2"}
NO_JARGON_RADAR_RULES = {"en5", "v2"}
DEFAULT_RULES = "en5"  # 2026-11-12: second generation (= v2)


def resolve_rules(rules: str) -> str:
    """Map a generation name (v1 / v2) to its ledger name (en2 / en5); unknown names pass through."""
    return RULE_ALIASES.get(rules, rules)


def effective_rules(rules: str, radar_text: str) -> str:
    if rules in GATED_RULES and TELEGRAPH_MARK not in radar_text:
        return GATED_RULES[rules]
    return rules


def build_audit_prompt(original: str, rtt_draft: str, radar_text: str, rules: str = DEFAULT_RULES,
                       question: str | None = None) -> str:
    if rules not in RULES:
        raise ValueError(f"unknown audit rules {rules}; available: {', '.join(RULES)}")
    rules = effective_rules(rules, radar_text)
    q = f"[User question]\n{question.strip()}\n\n" if question and question.strip() else ""
    return (f"{RULES[rules]}\n\n{q}[Original]\n{original}\n\n"
            "[Round-trip reference (plainer wording you may borrow, but it contains machine-translation errors; "
            f"check against the original)]\n{rtt_draft}\n\n"
            f"[Radar hints (machine-detected suspects in the original, for reference only)]\n{radar_text}\n\n"
            "Output only the JSON array:")
