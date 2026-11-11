"""英文版可行性探针（2026-11-11；已被 no_ai_fluff.en 与 benchmarks/en_eval.py 取代，保留以复现探针结果）：EN→JA→EN 往返翻译选型 + 一次端到端补丁。

不是产品代码：英文模式全部用运行时替换（monkeypatch）接进现有流水线，验证能不能 work、该选哪个翻译模型。
不做盲测、不请评委；只看客观指标，外加人工通读输出。

    python benchmarks/en_probe.py rtt    # 三个翻译模型 × 日语桥接，结果写 results/en_probe/rtt.jsonl
    python benchmarks/en_probe.py report # 汇总客观指标
    python benchmarks/en_probe.py patch --model tencent/hy-mt2-30b-a3b   # DeepSeek 宿主出补丁，写 patch.jsonl

翻译模型只走 OpenRouter；宿主只走 DeepSeek 官方 API（与 CLAUDE.md 一致）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "benchmarks"))

from no_ai_fluff import masker, patcher, pipeline, rtt  # noqa: E402
from no_ai_fluff.config import load_dotenv  # noqa: E402

BENCH = ROOT / "benchmarks"
CORPUS = BENCH / "corpus" / "en_probe.jsonl"
OUT = BENCH / "results" / "en_probe"
CACHE = BENCH / ".cache"
MODELS = ("tencent/hy-mt2-30b-a3b", "google/gemma-3-4b-it", "google/gemma-4-26b-a4b-it")
BRIDGE = "ja"

# ---------------------------------------------------------------------------
# 英文模式（运行时替换）
# ---------------------------------------------------------------------------
LANG_EN = {"ja": "Japanese", "en": "English", "ru": "Russian", "fr": "French"}
_LATIN = re.compile(r"[A-Za-z]")
_KANA_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿]")


def en_prompt(text: str, target: str, batched: bool = False, context: str | None = None) -> str:
    # 混元 MT 官方“非中文互译”模板（英文提示词）；上下文模板为同一句式的英文改写
    lang = LANG_EN.get(target, target)
    if context and context.strip():
        return (f"{context.strip()}\nReferring to the information above, translate the following segment into {lang}. "
                f"Do not translate the text above and do not add explanations:\n{text}")
    return f"Translate the following segment into {lang}, without additional explanation.\n\n{text}"


def en_validate(src: str, back: str, ids: list[int]) -> str:
    chk = masker.check_placeholders(back, ids)
    if not chk.ok:
        return f"placeholder missing={chk.missing} extra={chk.extra} dup={chk.duplicated}"
    if masker.markup_signature(src) != masker.markup_signature(back):
        return "inline markup changed"
    plain = masker.PLACEHOLDER_RE.sub("", back)
    if _KANA_CJK.search(plain) or not _LATIN.search(plain):
        return "back-translation not English"
    ls, lb = len(masker.PLACEHOLDER_RE.sub("", src)), len(plain)
    if ls >= 20 and not (0.5 <= lb / ls <= 2.0):
        return f"length ratio {lb / ls:.2f} out of range"
    return ""


def enable_english() -> None:
    # 掩码：不冻结整句英文和首字母大写的词（那是中文模式防“散装英文被译坏”的规则），也不用中文场景的术语表；
    # 代码、公式、路径、URL、标识符、缩写照旧冻结
    pats = [(n, p) for n, p in masker._PATTERNS if n != "ensent"]
    masker._COMBINED = re.compile("|".join(f"(?P<{n}>{p})" for n, p in pats), re.DOTALL)
    masker.DEFAULT_TERMS = []
    masker.Masker.__init__.__defaults__ = (None, False)
    masker.Slot.translatable = property(lambda s: bool(_LATIN.search(masker.PLACEHOLDER_RE.sub("", s.masked))))
    rtt.build_prompt = en_prompt
    rtt.RoundTripTranslator.prompt_fn = staticmethod(en_prompt)  # 核心加了子类钩子后，提示词经由类属性取用
    rtt.RoundTripTranslator.validate = staticmethod(en_validate)
    rtt._SENT_END_RE = re.compile(r"(?<=[.!?;\n])(?=\s)")  # 英文句末（中文版不认半角句点）
    zh_factory = pipeline.default_translator_factory

    def en_factory(cfg):  # 现有工厂不传 source，回程默认译回中文
        tr = zh_factory(cfg)
        tr.source = "en"
        return tr
    pipeline.default_translator_factory = en_factory
    # 审校规则与雷达
    patcher.AUDIT_RULES["en1"] = AUDIT_EN1
    patcher.PROBLEM_TYPES["en1"] = PROBLEM_TYPES_EN1
    patcher.MAX_FIND_BY_RULES["en1"] = 400
    patcher.ORIGINAL_BASE_RULES.add("en1")
    pipeline.build_audit_prompt = build_audit_prompt_en
    pipeline.scan_single = scan_en


PROBLEM_TYPES_EN1 = ("telegraphic", "noun-stack", "buried-verb", "inanimate-agent", "hard-clause", "jargon",
                     "coined-label", "empty-words", "missing-link", "redundant", "unclear-reference")

AUDIT_EN1 = """You are an English editor. Read the [Original] paragraph by paragraph and patch every place that is dense, hard to parse, or unlike how an experienced colleague would explain it face to face.
For each paragraph ask: would a colleague say it this way out loud? Where they would phrase it differently, write a patch. Leave genuinely natural sentences alone; output [] only if the whole text passes this test. Rewrite as much as the problem needs; there is no cap.

The [Round-trip reference] is the original machine-translated into Japanese and back. Its sentences are often looser and plainer, and you may borrow its wording.
It also brings machine-translation errors: wrong terms, odd collocations, changed numbers or hedges. Check every borrowed phrase against the original; terms, names, numbers and tone follow the original.

Check every item in [Radar hints]: fix it if it is a real problem, skip it if it is not.

What counts as a problem (each patch's "type" must be one of the bracketed names; if you cannot name the type, do not patch):
1. [telegraphic] dropped articles, verbs or objects; symbols such as "/", "+", "→", "—" standing in for "or", "and", "then"; "Goal: ..." labels instead of sentences. Restore the missing words.
2. [noun-stack] three or more nouns stacked as modifiers ("write-path invalidation ordering change"). Unpack with of/for/that or a verb.
3. [buried-verb] the action hidden in a noun or a made-up verb ("drive alignment", "operationalize", "perform an evaluation of"). Say the action as a plain verb with a clear doer.
4. [inanimate-agent] a thing doing what only people do ("the data wants", "the architecture is designed to deliver") when it hides who actually acts. Name the actor when it matters.
5. [hard-clause] object relative clauses without "that" ("the bound the iteration applies"), long gaps between subject and verb, or several clauses packed into one sentence. Reorder or split into two or three sentences.
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


def build_audit_prompt_en(original, rtt_draft, radar_text, rules="en1", question=None):
    q = f"[User question]\n{question.strip()}\n\n" if question and question.strip() else ""
    return (f"{AUDIT_EN1}\n\n{q}[Original]\n{original}\n\n"
            f"[Round-trip reference (plainer wording you may borrow, but it contains MT errors; check against the original)]\n"
            f"{rtt_draft}\n\n[Radar hints (machine-detected suspects in the original, for reference only)]\n{radar_text}\n\n"
            f"Output only the JSON array:")


# ---------------------------------------------------------------------------
# 英文表层指标（标准库实现；论文用 spaCy 依存句法，这里只取能用正则近似的几项）
# ---------------------------------------------------------------------------
BUZZ = {"leverage", "leveraging", "robust", "seamless", "seamlessly", "holistic", "unlock", "unlocking", "empower",
        "empowers", "ecosystem", "landscape", "journey", "pivotal", "frictionless", "operationalize", "alignment",
        "north-star", "needle", "double-click", "runway", "net-net", "step-function", "posture", "de-risks",
        "unblocks", "surface", "synergy", "streamline", "delve", "reimagined", "unveil", "meaningful", "meaningfully"}
_WORD = re.compile(r"[A-Za-z][A-Za-z'’-]*")
_SENT = re.compile(r"(?<=[.!?])\s+|\n+")
_NOMINAL = re.compile(r"(?i)\b[a-z]{3,}(?:tion|sion|ment|ance|ence|ity|ness|ization)s?\b")
_NUM = re.compile(r"\d+(?:[.,:]\d+)*")


def strip_code(t: str) -> str:
    return re.sub(r"```.*?```|`[^`]+`|\$[^$]+\$", " ", t, flags=re.S)


def surface(text: str) -> dict:
    t = strip_code(text)
    words = _WORD.findall(t)
    sents = [s for s in _SENT.split(t) if _WORD.search(s)]
    lens = [len(_WORD.findall(s)) for s in sents]
    n = max(1, len(words))
    lw = [w.lower() for w in words]
    hyph = sum(1 for w in words if w.count("-") >= 1 and not w.startswith("-"))
    return {
        "words": len(words),
        "words_per_sent": round(sum(lens) / max(1, len(lens)), 1),
        "long_sent_pct": round(100 * sum(1 for x in lens if x > 30) / max(1, len(lens)), 1),
        "nominal_per100": round(100 * len(_NOMINAL.findall(t)) / n, 1),
        "buzz_per1k": round(1000 * sum(1 for w in lw if w in BUZZ) / n, 1),
        "hyphen_compounds_per100": round(100 * hyph / n, 1),
        "relativizer_per100": round(100 * sum(1 for w in lw if w in ("that", "which", "who")) / n, 1),
        "symbols": len(re.findall(r"[→—/+]", t)),
    }


def nums(t: str) -> Counter:
    return Counter(_NUM.findall(t))


def keep_rate(text: str, keys: list[str]) -> float:
    low = text.lower()
    return sum(1 for k in keys if k.lower() in low) / max(1, len(keys))


class _Radar:
    def __init__(self, hints):
        self.hints = hints

    @property
    def counts(self):
        return dict(Counter(k for k, _ in self.hints))

    def to_dict(self):
        return {"counts": self.counts, "hints": [{"kind": k, "excerpt": e} for k, e in self.hints]}

    def to_prompt(self, limit: int = 30):
        if not self.hints:
            return "(no surface issues found; still review the whole text)"
        return "\n".join(f"- [{k}] \"{e}\"" for k, e in self.hints[:limit])


def scan_en(slots: list[str]) -> _Radar:
    hints = []
    for s in slots:
        t = masker.PLACEHOLDER_RE.sub("X", s)
        for sent in _SENT.split(t):
            if len(_WORD.findall(sent)) > 30:
                hints.append(("long sentence", sent.strip()[:80]))
        for w in _WORD.findall(t):
            if w.lower() in BUZZ:
                hints.append(("jargon", w))
        for m in re.finditer(r"(?:\b[a-z]+(?:-[a-z]+)+\b\s+){1,}\b[a-z]+(?:tion|ment|ance|ence|ity|ness|ing|er|s)\b", t):
            hints.append(("noun stack", m.group(0)))
        for m in re.finditer(r"[→+]|(?<=\w) — (?=\w)", t):
            hints.append(("symbol for word", t[max(0, m.start() - 15):m.end() + 15]))
    return _Radar(hints)


# ---------------------------------------------------------------------------
# 各阶段
# ---------------------------------------------------------------------------
def samples() -> list[dict]:
    return [json.loads(x) for x in CORPUS.read_text(encoding="utf-8").splitlines() if x.strip()]


def make_pipe(model: str) -> pipeline.Pipeline:
    cfg = pipeline.PipelineConfig(provider="openrouter", model=model, bridge=BRIDGE, audit_rules="en1",
                                  cache_dir=str(CACHE / "openrouter"), store_dir=str(OUT / "drafts"),
                                  doc_timeout_s=120, chat_timeout_s=120, request_timeout_s=60, telemetry=False)
    return pipeline.Pipeline(cfg)


def stage_rtt(args) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []

    def run(job):
        model, s = job
        t0 = time.monotonic()
        prep = make_pipe(model).prepare(s["text"], exit="file", question=s.get("question"))
        return {"id": s["id"], "scenario": s["scenario"], "model": model, "bridge": BRIDGE,
                "rtt": prep.rtt_draft, "original": prep.original, "stats": prep.stats,
                "fallback": prep.fallback_reason, "wall_s": round(time.monotonic() - t0, 2)}

    jobs = [(m, s) for m in args.models for s in samples()]
    with ThreadPoolExecutor(max_workers=6) as ex:
        rows = list(ex.map(run, jobs))
    with open(OUT / "rtt.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"wrote {len(rows)} rows")


def stage_report(args) -> None:
    keep = {s["id"]: s["must_keep"] for s in samples()}
    rows = [json.loads(x) for x in (OUT / "rtt.jsonl").read_text(encoding="utf-8").splitlines()]
    by = {}
    for r in rows:
        by.setdefault(r["model"], []).append(r)
    orig = {r["id"]: r["original"] for r in rows}
    base = [surface(t) for t in orig.values()]
    summary = {"original": {k: round(sum(b[k] for b in base) / len(base), 1) for k in base[0]}}
    for m, rs in by.items():
        st = [r["stats"] for r in rs]
        sf = [surface(r["rtt"]) for r in rs]
        summary[m] = {
            "latency_p50_s": sorted(x["rtt_latency_s"] or 0 for x in st)[len(st) // 2],
            "seg_fallback_pct": round(100 * sum(x["segment_fallbacks"] for x in st) / max(1, sum(x["translated_slots"] for x in st)), 1),
            "doc_fallback": sum(1 for r in rs if r["fallback"]),
            "unchanged_docs": sum(1 for r in rs if r["rtt"].strip() == r["original"].strip()),
            "num_mismatch_docs": sum(1 for r in rs if nums(r["rtt"]) != nums(r["original"])),
            "keep_rate": round(sum(keep_rate(r["rtt"], keep[r["id"]]) for r in rs) / len(rs), 3),
            "cost_usd": round(sum(x["cost_usd"] or 0 for x in st), 5),
            **{k: round(sum(x[k] for x in sf) / len(sf), 1) for k in sf[0]},
        }
    (OUT / "rtt_report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def stage_patch(args) -> None:
    import run_ablation as ra
    load_dotenv()
    client = rtt.ChatClient(provider="deepseek", timeout=600, cache_dir=CACHE / "deepseek")
    pipe = make_pipe(args.model)

    def reviewer(prompt: str):
        raw, usage, lat = ra.chat_json(client, "deepseek-flash", prompt, max_tokens=64000, effort="high")
        reviewer.log.append({"raw": raw, "usage": usage, "latency_s": round(lat, 2)})
        return raw

    out = []
    for s in samples():
        reviewer.log = []
        prep, fin = pipe.run(s["text"], reviewer=reviewer, exit="file", question=s.get("question"))
        out.append({"id": s["id"], "model": args.model, "bridge": BRIDGE, "rules": "en1", "host": "deepseek-flash",
                    "effort": "high", "original": prep.original, "rtt": prep.rtt_draft, "radar": prep.radar,
                    "final": fin.final_text, "applied": fin.applied, "rejected": fin.rejected,
                    "patches": fin.patches, "fallback_reason": fin.fallback_reason, "host_raw": reviewer.log,
                    "num_changed": nums(fin.final_text) != nums(prep.original),
                    "keep_rate": keep_rate(fin.final_text, s["must_keep"]),
                    "surface_before": surface(prep.original), "surface_after": surface(fin.final_text)})
        print(f"{s['id']}: applied {fin.applied} rejected {fin.rejected} num_changed {out[-1]['num_changed']}", flush=True)
    with open(OUT / "patch.jsonl", "w", encoding="utf-8") as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["rtt", "report", "patch"])
    ap.add_argument("--models", nargs="+", default=list(MODELS))
    ap.add_argument("--model", default=MODELS[0])
    args = ap.parse_args()
    load_dotenv()
    enable_english()
    {"rtt": stage_rtt, "report": stage_report, "patch": stage_patch}[args.stage](args)


if __name__ == "__main__":
    main()
