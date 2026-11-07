"""Stage 2a：本地确定性雷达 —— 5 类明显且通用的表层/结构规则。

只做辅助高亮与客观统计，绝不据此放行：无论是否报警，RTT 初稿都会交给宿主模型做全量语义审校。

1. long_clause    长句与定语堆积：单个逗号分句 > 40 字，或单分句内 ≥ 3 个「的」
2. number         数字与量级一致性：原文与 RTT 初稿的数字多重集合做差集
3. cliche         典型套话 / 空洞黑话 / 无度量夸张修饰
4. loose_english  未被掩码保护却残留在正文里的散装英文
5. pronoun        密集代词：单句 ≥ 3 个「它/其」，或单分句 ≥ 2 个
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass, field

from .masker import PLACEHOLDER_RE

KINDS = ("long_clause", "number", "cliche", "loose_english", "pronoun")
KIND_LABELS = {
    "long_clause": "长句/定语堆积",
    "number": "数字不一致",
    "cliche": "套话/黑话",
    "loose_english": "散装英文",
    "pronoun": "密集代词",
}

LONG_CLAUSE_CHARS = 40
DE_LIMIT = 3

CLICHES = [
    # 起承转合套话
    "值得注意的是", "需要注意的是", "需要指出的是", "值得一提的是", "值得强调的是", "综上所述", "总的来说",
    "总而言之", "总结来说", "一言以蔽之", "归根结底", "不难看出", "显而易见", "毋庸置疑", "众所周知",
    "在当今", "随着.{1,12}的(?:不断|快速|飞速)?发展", "未来可期", "具有重要意义", "具有深远意义",
    "至关重要", "不可或缺", "举足轻重", "让我们", "希望这(?:些|个)?.{0,6}(?:对你|能)",
    # 互联网空洞黑话
    "赋能", "闭环", "抓手", "底层逻辑", "打通", "拉通", "沉淀", "颗粒度", "心智", "赛道", "护城河",
    "组合拳", "打法", "降本增效", "全链路", "范式转移", "一站式", "无缝", "重塑", "深度融合", "倒逼",
    # 无度量夸张修饰
    "极其", "极度", "极为", "卓越", "颠覆性", "革命性", "前所未有", "全方位", "多维度",
]
_CLICHE_RE = re.compile("|".join(f"(?:{c})" for c in CLICHES))

_CLAUSE_SPLIT_RE = re.compile(r"[，,。；;！!？?：:\n、（）()]")
_SENT_SPLIT_RE = re.compile(r"[。！!？?；;\n]")
_NUM_RE = re.compile(r"(?<![A-Za-z0-9_.])\d+(?:[.,]\d+)*")
_EN_WORD_RE = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z][A-Za-z'\-]*[A-Za-z](?![A-Za-z0-9_])")
_UNIT_AFTER_NUM_RE = re.compile(r"\d\s*$")
_PRONOUN_RE = re.compile(r"它们|它|(?<![尤极与])其(?![他它中实次余后间])")


@dataclass
class Hint:
    kind: str
    slot: int | None
    excerpt: str
    message: str


@dataclass
class RadarReport:
    hints: list[Hint] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        c = Counter(h.kind for h in self.hints)
        return {k: c.get(k, 0) for k in KINDS}

    @property
    def total(self) -> int:
        return len(self.hints)

    def to_dict(self) -> dict:
        return {"counts": self.counts, "hints": [asdict(h) for h in self.hints]}

    def to_prompt(self, limit: int = 12) -> str:
        if not self.hints:
            return "（雷达未发现表层问题，仍需全量语义审校）"
        lines = [f"- [{KIND_LABELS[h.kind]}] {h.message}：「{h.excerpt}」" for h in self.hints[:limit]]
        if len(self.hints) > limit:
            lines.append(f"- ……另有 {len(self.hints) - limit} 条")
        return "\n".join(lines)


def _visible(masked: str) -> str:
    """把占位符折算成 2 个字宽，用于长度统计。"""
    return PLACEHOLDER_RE.sub("码", masked)


def _clip(s: str, n: int = 30) -> str:
    s = s.strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def normalize_number(tok: str) -> str:
    # 逗号后恰好三位数字视为千分位，否则视为小数点（法语/俄语回译常见 82,7）
    tok = re.sub(r",(?=\d{3}(?!\d))", "", tok)
    tok = tok.replace(",", ".")
    if "." in tok:
        tok = tok.rstrip("0").rstrip(".") or "0"
    return tok


def extract_numbers(masked: str) -> Counter:
    return Counter(normalize_number(t) for t in _NUM_RE.findall(PLACEHOLDER_RE.sub(" ", masked)))


def scan_slot(masked: str, slot: int | None = None) -> list[Hint]:
    hints: list[Hint] = []
    vis = _visible(masked)
    # 1. 长句与定语堆积
    for clause in _CLAUSE_SPLIT_RE.split(vis):
        c = clause.strip()
        n = len(re.sub(r"\s", "", c))
        de = c.count("的")
        if n > LONG_CLAUSE_CHARS:
            hints.append(Hint("long_clause", slot, _clip(c), f"分句 {n} 字未断句"))
        elif de >= DE_LIMIT:
            hints.append(Hint("long_clause", slot, _clip(c), f"单分句 {de} 个「的」连环定语"))
    # 3. 套话 / 黑话
    for m in _CLICHE_RE.finditer(masked):
        hints.append(Hint("cliche", slot, m.group(0), "套话/空洞黑话/无度量修饰"))
    # 4. 散装英文残留
    plain = PLACEHOLDER_RE.sub(" ", masked)
    words = []
    for m in _EN_WORD_RE.finditer(plain):
        if _UNIT_AFTER_NUM_RE.search(plain[: m.start()]) and len(m.group(0)) <= 3:
            continue  # 120ms、15s、3x 这类数字单位
        words.append(m.group(0))
    if words:
        hints.append(Hint("loose_english", slot, ", ".join(dict.fromkeys(words)), "未保护的散装英文"))
    # 5. 密集代词
    for sent in _SENT_SPLIT_RE.split(masked):
        k = len(_PRONOUN_RE.findall(sent))
        worst_clause = max((len(_PRONOUN_RE.findall(c)) for c in re.split(r"[，,]", sent)), default=0)
        if k >= 3 or worst_clause >= 2:
            hints.append(Hint("pronoun", slot, _clip(sent), f"单句 {k} 个「它/其」"))
    return hints


def compare_numbers(original: str, draft: str, slot: int | None = None) -> list[Hint]:
    a, b = extract_numbers(original), extract_numbers(draft)
    missing, added = a - b, b - a
    if not missing and not added:
        return []
    parts = []
    if missing:
        parts.append("初稿缺少 " + "、".join(sorted(missing.elements())))
    if added:
        parts.append("初稿多出 " + "、".join(sorted(added.elements())))
    return [Hint("number", slot, _clip(draft, 40), "；".join(parts))]


def scan(original_slots: list[str], draft_slots: list[str], only: list[int] | None = None) -> RadarReport:
    """对 RTT 初稿逐槽位扫描 5 类问题（数字一致性对照原文）。槽位文本均为掩码形式。"""
    report = RadarReport()
    idx = only if only is not None else range(len(draft_slots))
    for i in idx:
        report.hints += compare_numbers(original_slots[i], draft_slots[i], i)
        report.hints += scan_slot(draft_slots[i], i)
    return report


def scan_single(slots: list[str]) -> RadarReport:
    """只扫描一份文本（用于对原文/定稿做表层统计，不含数字对照）。"""
    report = RadarReport()
    for i, s in enumerate(slots):
        report.hints += scan_slot(s, i)
    return report


def scan_document(text: str, fmt: str = "auto") -> RadarReport:
    """便捷入口：直接对一篇任意文本做表层统计（评测台用）。"""
    from .masker import parse

    doc = parse(text, fmt)
    return scan_single([s.masked for s in doc.slots])
