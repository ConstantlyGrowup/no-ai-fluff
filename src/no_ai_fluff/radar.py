"""Stage 2a：本地确定性雷达 —— 5 类明显且通用的表层/结构规则。

只做辅助高亮与客观统计，绝不据此放行：无论是否报警，RTT 初稿都会交给宿主模型做全量语义审校。

1. long_clause    长句与定语堆积：单个逗号分句 > 40 字，或单分句内 ≥ 3 个「的」
2. number         数字与量级一致性：原文与 RTT 初稿的数字多重集合做差集
3. cliche         典型套话 / 空洞黑话 / 无度量夸张修饰
4. loose_english  未被掩码保护却残留在正文里的散装英文
5. pronoun        密集代词：单句 ≥ 3 个「它/其」，或单分句 ≥ 2 个
6. telegraph      电报式短句群（第二代规则，只在 dense=True 时扫描）：纯句法/密度规则，不依赖任何黑话词表
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass, field

from .masker import PLACEHOLDER_RE

KINDS = ("long_clause", "number", "cliche", "loose_english", "pronoun", "telegraph")
KIND_LABELS = {
    "long_clause": "长句/定语堆积",
    "number": "数字不一致",
    "cliche": "套话/黑话",
    "loose_english": "散装英文",
    "pronoun": "密集代词",
    "telegraph": "电报式短句群",
}

LONG_CLAUSE_CHARS = 40
DE_LIMIT = 3
# 优化闭环第 2 轮：原有分句切分把顿号、括号都当断点，一句话塞满信息的“高密度句”从不报警。
# 以下两条仍归入 long_clause（不改报表口径），只对正文段落生效（短于 PROSE_MIN_CHARS 的槽位多是标题、表格格子）。
DENSE_SENT_CHARS = 70  # 一整句（到句号 / 问号 / 分号为止）超过这么多字……
DENSE_SENT_SEPS = 4  # ……且内部有这么多个逗号、顿号、冒号：信息太密，读者要停下来拆
LIST_FIRST_ITEMS = 4  # 分句里连列这么多项（≥3 个顿号）……
LIST_FIRST_WITHIN = 12  # ……且第一个顿号出现在分句开头这么多字以内：先堆名词、最后才说要做什么
PROSE_MIN_CHARS = 40
PROMPT_HINT_LIMIT = 30  # v7 要求逐条核对雷达，原来只给前 12 条，长文后半段的疑点会被截掉
_SENT_RE = re.compile(r"[^。！？!?；;\n]+[。！？!?；;]?")

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

# 6. 电报式短句群（v11）：黑话一直在变，静态词表抓不住，也不该无限扩表；这里只看句子的“形状”。
# 一句话被逗号、顿号、分号、冒号切成很多短片段，片段平均只有几个字，又几乎不用虚词和连接词
# （的、了、在、把、就、会、因为、如果、我们、这……都是封闭词类，不是黑话词表），读者只能看到一串
# “动作 + 抽象名词”，看不到谁在做、什么条件下做、先后怎样。真正的并列罗列（地名、字段名、测试项）
# 形状相同，所以这条只作提示，由宿主判断是否该拆。
TELE_MIN_CHARS = 24       # 句子（去掉标点、空白、占位符）至少这么多字
TELE_MIN_FRAGS = 4        # 至少切成这么多个片段
TELE_AVG = (5, 11)        # 片段平均字数落在这个区间（太短多是名词罗列，太长就不是电报腔）
TELE_MAX_FN = 0.04        # 虚词与连接词占比上限
_TELE_SENT_RE = re.compile(r"[^。！？!?\n]+[。！？!?]?")
_TELE_SEP_RE = re.compile(r"[，,、；;：:]")
_TELE_STRIP_RE = re.compile(r"[\s“”\"'（）()*#>\-—…/【】\[\]]")
_FUNCTION_RE = re.compile(
    r"的|了|着|过|在|把|被|将|就|才|会|要|能|可以|可能|是|有|没|不|也|都|还|又|再|先|然后|如果|若|当|因为|由于"
    r"|所以|因此|但|而|则|从|对|向|给|让|使|用|以|为|我|你|他|她|它|这|那|一个|一些|些|吗|呢|吧")


def telegraph_stats(sentence: str) -> tuple[int, float, float] | None:
    """电报式短句群判定：命中返回 (片段数, 平均字数, 虚词占比)，否则 None。sentence 为掩码文本。"""
    vis = PLACEHOLDER_RE.sub("码", sentence)
    frags = [_TELE_STRIP_RE.sub("", f) for f in _TELE_SEP_RE.split(vis.rstrip("。！？!?"))]
    frags = [f for f in frags if f]
    core = "".join(frags)
    n = len(core)
    if n < TELE_MIN_CHARS or len(frags) < TELE_MIN_FRAGS:
        return None
    avg = n / len(frags)
    fn = sum(len(m.group(0)) for m in _FUNCTION_RE.finditer(core)) / n
    if TELE_AVG[0] <= avg <= TELE_AVG[1] and fn <= TELE_MAX_FN:
        return len(frags), round(avg, 1), round(fn, 3)
    return None

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

    def to_prompt(self, limit: int = PROMPT_HINT_LIMIT) -> str:
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


def scan_slot(masked: str, slot: int | None = None, dense: bool = False, cliche: bool = True) -> list[Hint]:
    """dense=True 时加扫第 6 类“电报式短句群”（第二代规则使用；第一代不扫，保证其提示词逐字一致）。

    cliche=False 时不跑静态黑话词表（第二代：运行时只留结构型规则）；评测统计口径仍用默认的 True。
    """
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
    if len(re.sub(r"\s", "", vis)) >= PROSE_MIN_CHARS:
        for m in _SENT_RE.finditer(vis):
            sent = m.group(0).strip()
            n = len(re.sub(r"\s", "", sent))
            seps = len(re.findall(r"[，,；;：:、]", sent))
            if n > DENSE_SENT_CHARS and seps >= DENSE_SENT_SEPS:
                hints.append(Hint("long_clause", slot, _clip(sent), f"一句 {n} 字、{seps} 处停顿，信息太密，考虑拆成几句"))
            for clause in re.split(r"[，,。；;：:！？!?]", sent):
                c = clause.strip()
                if c.count("、") >= LIST_FIRST_ITEMS - 1 and 0 <= c.find("、") <= LIST_FIRST_WITHIN:
                    hints.append(Hint("long_clause", slot, _clip(c),
                                      f"分句开头连列 {c.count('、') + 1} 项，读者要读到最后才知道要做什么；考虑先说动作，或写成完整句"))
    # 3. 套话 / 黑话
    if cliche:
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
    # 6. 电报式短句群（只看结构，不看词）
    if dense:
        for m in _TELE_SENT_RE.finditer(masked):
            st = telegraph_stats(m.group(0))
            if st:
                k, avg, _fn = st
                hints.append(Hint("telegraph", slot, _clip(_visible(m.group(0)), 40),
                                  f"一句切成 {k} 个短片段、平均 {avg} 字、几乎没有连接词。若只是并列罗列事物（地名、景点、字段名、测试项）可以不改；"
                                  "若是一串“动作 + 抽象名词”压成的电报，请拆成两三句完整的话，补上上下文里本来就有的主语和连接词，"
                                  "把包装式行话换成字面本来的具体动作，但不要脑补原文没写的条件或机制"))
    return hints


_ZH_DIGITS = "零一二三四五六七八九"


def to_chinese_numeral(n: int) -> set[str]:
    """0~99999 的常见中文写法（3 -> 三，12 -> 十二，20 -> 二十，2 -> 二/两）。"""
    if n < 0 or n > 99999:
        return set()
    if n < 10:
        forms = {_ZH_DIGITS[n]}
        if n == 2:
            forms.add("两")
        return forms
    out, units = [], [(10000, "万"), (1000, "千"), (100, "百"), (10, "十")]
    rest, zero = n, False
    for val, ch in units:
        d, rest = divmod(rest, val)
        if d:
            if zero:
                out.append("零")
                zero = False
            out.append(("" if (d == 1 and ch == "十" and not out) else _ZH_DIGITS[d]) + ch)
        elif out:
            zero = True
    if rest:
        if zero:
            out.append("零")
        out.append(_ZH_DIGITS[rest])
    s = "".join(out)
    forms = {s}
    if s.startswith("二") and len(s) > 1 and s[1] in "千万百":
        forms.add("两" + s[1:])
    return forms


def _spelled_out(num: str, text: str) -> bool:
    """num 是否以中文数字形式出现在 text 里（只认整数）。"""
    if not num.isdigit():
        return False
    return any(f in text for f in to_chinese_numeral(int(num)))


def number_mismatch(original: str, draft: str) -> tuple[Counter, Counter]:
    """返回 (初稿缺少的数字, 初稿多出的数字)。阿拉伯数字与中文数字互换（3 ↔ 三）不算不一致。"""
    a, b = extract_numbers(original), extract_numbers(draft)
    missing, added = a - b, b - a
    pa, pb = PLACEHOLDER_RE.sub(" ", original), PLACEHOLDER_RE.sub(" ", draft)
    missing = Counter({k: v for k, v in missing.items() if not _spelled_out(k, pb)})
    added = Counter({k: v for k, v in added.items() if not _spelled_out(k, pa)})
    return missing, added


def compare_numbers(original: str, draft: str, slot: int | None = None) -> list[Hint]:
    missing, added = number_mismatch(original, draft)
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


def telegraph_sentences(slots: list[str]) -> list[tuple[int, str]]:
    """列出命中“电报式短句群”的句子 (槽位, 掩码句子)，顺序与 scan_single(dense=True) 里 telegraph 提示的顺序一致。

    这些句子交给领域分诊（telegraph_router）判断是否出自技术/工程语境；雷达本身只看句法统计量。
    """
    out = []
    for i, s in enumerate(slots):
        for m in _TELE_SENT_RE.finditer(s):
            if telegraph_stats(m.group(0)):
                out.append((i, m.group(0)))
    return out


def scan_single(slots: list[str], dense: bool = False, cliche: bool = True) -> RadarReport:
    """只扫描一份文本（用于对原文/定稿做表层统计，不含数字对照）。dense、cliche 见 scan_slot。"""
    report = RadarReport()
    for i, s in enumerate(slots):
        report.hints += scan_slot(s, i, dense, cliche)
    return report


def scan_document(text: str, fmt: str = "auto") -> RadarReport:
    """便捷入口：直接对一篇任意文本做表层统计（评测台用）。"""
    from .masker import parse

    doc = parse(text, fmt)
    return scan_single([s.masked for s in doc.slots])
