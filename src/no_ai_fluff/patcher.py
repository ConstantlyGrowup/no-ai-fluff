"""Stage 2b & 3：宿主模型审校提示词 + JSON 局部补丁的安全合并。

宿主大模型（运行在独立子上下文里）只被允许输出
    [{"find": "初稿原片段", "replace": "修改后片段", "reason": "原因"}]
这样的局部替换补丁；本模块用确定性代码逐条合并，任何一条补丁只要
- 在初稿里找不到 / 出现不止一次（有歧义）
- 跨越了结构边界（跨行、跨表格单元格、跨代码块）
- 切断、丢失或复制了被冻结的代码 / 公式 / 路径（{{Pn}}）
就整条拒收，其余补丁照常生效。
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

from .masker import _LINE_PREFIX_RE, PLACEHOLDER, PLACEHOLDER_RE, Document, check_placeholders

AUDIT_INSTRUCTIONS_V1 = """你是中文技术文本审校员。【RTT 初稿】由【原文】经机器往返翻译得到，句式已变松弛。请对照原文全量审校初稿，只修下列问题：
1. 术语翻偏：专业术语被误译或直译（如“正定矩阵→积极确定矩阵”“逃逸分析→逃跑分析”），改回中文标准名；首次出现的硬核学科概念写成“中文标准名（English Term）”。
2. 逻辑失真：语气被弱化（“仅在…时”变“通常在…时”）、因果倒置、充要条件改变、推理步骤丢失、数字或量级与原文不符。
3. 隐喻与指代：空间动作隐喻改回直白因果；指代不明的“它/其”换成明确的名词。
4. 雷达提示里确实成立的问题（套话、散装英文、过长分句）可顺手修；误报不必理会。
输出规则：
- 只输出 JSON 数组：[{"find": "初稿原片段", "replace": "修改后片段", "reason": "原因"}]；初稿准确通顺就输出 []。
- find 必须逐字摘自【RTT 初稿】，在全文中唯一，不跨行，尽量短。
- 严禁重写全文或润色本来正确的句子；保持初稿松弛的短句，不要压缩回长句。
- 代码、路径、公式、URL、数字原样保留。"""


# v2（2026-11-08）：M2 双轴评测显示，v1 “只修错、不润色”让翻译腔与断句粘连原样保留，
# 补丁版易读度比原文低 0.31。v2 允许用局部补丁修明显的翻译腔，但仍禁止整句以上的重写。
AUDIT_INSTRUCTIONS_V2 = """你是中文审校员。【RTT 初稿】由【原文】经机器往返翻译得到，句式已变松弛。请对照原文全量审校初稿，修下列问题：
1. 术语翻偏：专业术语或固定说法被误译、直译或换了说法（如“正定矩阵→积极确定矩阵”“竞态条件→竞争状态”），改回原文用词；首次出现的硬核学科概念可写成“中文标准名（English Term）”。
2. 逻辑失真：语气被弱化（“仅在…时”变“通常在…时”）、因果倒置、条件改变、主客体颠倒、推理步骤丢失、数字或量级与原文不符、原文信息被遗漏或凭空添加。
3. 隐喻与指代：空间动作隐喻改回直白因果；指代不明的“它/其”换成明确的名词。
4. 明显的翻译腔与断句问题：生硬直译的搭配（如“享受更安全的授权流程”“对数据进行处理”）、欧化长定语、丢标点导致两句粘连。改成同事之间说话那样的完整句子：有主语、有动词，该用的“的、了、会、可以”不省。不要改成公文腔或名词堆叠（如“支持签名校验，保障回调安全”这种也算问题）。
5. 雷达提示里确实成立的问题（套话、散装英文、过长分句）可顺手修；误报不必理会。
输出规则：
- 只输出 JSON 数组：[{"find": "初稿原片段", "replace": "修改后片段", "type": "术语|逻辑|指代|翻译腔|其他", "reason": "原因"}]；初稿准确通顺就输出 []。
- find 必须逐字摘自【RTT 初稿】，在全文中唯一，不跨行，最长一句话。
- 只改有问题的局部；读起来自然、意思正确的句子不要动。保持初稿松弛的短句，不要合并成长句，也不要扩写。
- 代码、路径、公式、URL、数字原样保留。"""

AUDIT_RULES = {"v1": AUDIT_INSTRUCTIONS_V1, "v2": AUDIT_INSTRUCTIONS_V2}
DEFAULT_AUDIT_RULES = "v2"
AUDIT_INSTRUCTIONS = AUDIT_RULES[DEFAULT_AUDIT_RULES]  # 向后兼容
MAX_FIND_CHARS = 200  # 单条补丁的 find 上限：再长就是整段重写，拒收（M2 中超过 200 字的补丁只占 0.1%）


def build_audit_prompt(original: str, rtt_draft: str, radar_text: str, rules: str = DEFAULT_AUDIT_RULES) -> str:
    if rules not in AUDIT_RULES:
        raise ValueError(f"未知审校规则 {rules}，可选：{', '.join(AUDIT_RULES)}")
    return (
        f"{AUDIT_RULES[rules]}\n\n【原文】\n{original}\n\n【RTT 初稿】\n{rtt_draft}\n\n"
        f"【雷达提示】\n{radar_text}\n\n只输出 JSON 数组："
    )


# ---------------------------------------------------------------------------
# 补丁解析
# ---------------------------------------------------------------------------


class PatchFormatError(ValueError):
    pass


def _salvage_objects(s: str) -> list[dict]:
    """从格式损坏的 JSON 数组里逐个解析出含 find/replace 的完整对象。

    从每个 “{” 起用标准解码器尝试解析一个完整对象；成功则跳过该对象，失败则前进一个字符。
    字符串内容由解码器负责转义处理，不会被当作结构字符改写。
    """
    dec = json.JSONDecoder()
    out: list[dict] = []
    i = s.find("{")
    while i != -1:
        try:
            obj, end = dec.raw_decode(s, i)
        except json.JSONDecodeError:
            i = s.find("{", i + 1)
            continue
        if isinstance(obj, dict) and "find" in obj and "replace" in obj:
            out.append(obj)
        i = s.find("{", end)
    return out


@dataclass
class Patch:
    find: str
    replace: str
    reason: str = ""
    type: str = ""


def parse_patches(raw) -> list[Patch]:
    """接受 list / {"patches": [...]} / JSON 字符串（容忍 ```json 围栏和前后废话）。"""
    if isinstance(raw, str):
        s = raw.strip()
        s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
        if not s:
            return []
        try:
            raw = json.loads(s)
        except json.JSONDecodeError:
            a, b = s.find("["), s.rfind("]")
            if a == -1 or b <= a:
                raise PatchFormatError("找不到 JSON 数组") from None
            try:
                raw = json.loads(s[a : b + 1])
            except json.JSONDecodeError as e:
                # 模型偶尔输出多余的 }、漏逗号或尾逗号：逐个抢救出完整的补丁对象，
                # 而不是用正则改写原文（正则会误伤 replace 字符串里本来就有的 “}},”）
                salvaged = _salvage_objects(s[a : b + 1])
                if not salvaged:
                    raise PatchFormatError(f"JSON 解析失败：{e}") from None
                raw = salvaged
    if isinstance(raw, dict):
        if "patches" in raw:
            raw = raw["patches"]
        elif "find" in raw:
            raw = [raw]  # 单条补丁没包数组，宽容处理
        else:
            raise PatchFormatError("补丁对象缺少 patches 或 find 字段")
    if not isinstance(raw, list):
        raise PatchFormatError("补丁必须是 JSON 数组")
    out: list[Patch] = []
    for item in raw:
        if not isinstance(item, dict):
            raise PatchFormatError(f"补丁项必须是对象：{item!r}")
        find, replace = item.get("find"), item.get("replace")
        if not isinstance(find, str) or not isinstance(replace, str):
            raise PatchFormatError(f"补丁项缺少字符串 find/replace：{item!r}")
        out.append(Patch(find, replace, str(item.get("reason", "")), str(item.get("type", "") or "")))
    return out


# ---------------------------------------------------------------------------
# 补丁合并
# ---------------------------------------------------------------------------


@dataclass
class _Span:
    u_start: int
    u_end: int
    m_start: int
    m_end: int
    pid: int


def _expand(masked: str, placeholders: list[str]) -> tuple[str, list[_Span]]:
    """解掩码，同时记录每个冻结片段在明文/掩码文本中的位置。"""
    out: list[str] = []
    spans: list[_Span] = []
    pos = 0
    upos = 0
    for m in PLACEHOLDER_RE.finditer(masked):
        chunk = masked[pos : m.start()]
        out.append(chunk)
        upos += len(chunk)
        val = placeholders[int(m.group(1))]
        spans.append(_Span(upos, upos + len(val), m.start(), m.end(), int(m.group(1))))
        out.append(val)
        upos += len(val)
        pos = m.end()
    out.append(masked[pos:])
    return "".join(out), spans


def _u2m(p: int, spans: list[_Span]) -> int:
    """明文位置 -> 掩码位置（p 不得落在冻结片段内部）。"""
    shift = 0
    for sp in spans:
        if sp.u_end <= p:
            shift += (sp.m_end - sp.m_start) - (sp.u_end - sp.u_start)
    return p + shift


@dataclass
class PatchResult:
    patch: Patch
    applied: bool
    reason: str = ""
    slot: int | None = None


def _find_all(hay: str, needle: str) -> list[int]:
    res, i = [], hay.find(needle)
    while i != -1:
        res.append(i)
        i = hay.find(needle, i + 1)
    return res


def _find_outside_placeholders(hay: str, needle: str) -> int:
    taken = [m.span() for m in PLACEHOLDER_RE.finditer(hay)]
    for k in _find_all(hay, needle):
        if all(k + len(needle) <= a or k >= b for a, b in taken):
            return k
    return -1


def apply_patches(doc: Document, slot_texts: list[str], patches: list[Patch]) -> tuple[list[str], list[PatchResult]]:
    """把补丁逐条合并进掩码槽位文本。返回 (新槽位文本, 每条补丁的处理结果)。"""
    texts = list(slot_texts)
    results: list[PatchResult] = []
    for p in patches:
        r = _apply_one(doc, texts, p)
        results.append(r)
    return texts, results


def _strip_structure_prefix(p: Patch) -> tuple[Patch | None, str]:
    """宿主模型常把行首的 Markdown 标记（## 、1. 、- [x] 、> ）一起摘进 find。

    这些标记属于文档骨架，不在可改写的正文槽位里。find 与 replace 带同样的标记时去掉后再匹配；
    replace 改动了标记本身（如改标题级别、改列表编号）则拒收，结构只能由原文决定。
    返回 (去掉标记后的补丁或 None, 拒收原因)。
    """
    m = _LINE_PREFIX_RE.match(p.find)
    prefix = m.group(1) if m else ""
    if not prefix.strip():
        return None, ""
    if not p.replace.startswith(prefix):
        return None, f"补丁改动了行首结构标记「{prefix.strip()}」"
    return Patch(p.find[len(prefix):], p.replace[len(prefix):], p.reason, p.type), ""


_CELL_SPLIT_RE = re.compile(r"(?<!\\)\|")


def _table_cells(row: str) -> list[str] | None:
    row = row.strip()
    if not (row.startswith("|") and row.endswith("|") and row.count("|") >= 3):
        return None
    return [c.strip() for c in _CELL_SPLIT_RE.split(row)[1:-1]]


def _apply_table_row(doc: Document, texts: list[str], p: Patch) -> PatchResult | None:
    """宿主按整行改写 Markdown 表格时，拆成逐个单元格的补丁；任一格失败则整条拒收（不留半改状态）。"""
    fc, rc = _table_cells(p.find), _table_cells(p.replace)
    if fc is None or rc is None:
        return None
    if len(fc) != len(rc):
        return PatchResult(p, False, "补丁改变了表格列数")
    trial = list(texts)
    for f, r in zip(fc, rc):
        if f == r:
            continue
        if not f:
            return PatchResult(p, False, "补丁向空单元格填写内容")
        sub = _apply_core(doc, trial, Patch(f, r, p.reason, p.type))
        if not sub.applied:
            return PatchResult(p, False, f"表格单元格「{f[:20]}」：{sub.reason}")
    texts[:] = trial
    return PatchResult(p, True, "")


def _apply_one(doc: Document, texts: list[str], p: Patch) -> PatchResult:
    r = _apply_core(doc, texts, p)
    if r.applied or not r.reason.startswith("find 在初稿中不存在"):
        return r
    t = _apply_table_row(doc, texts, p)
    if t is not None:
        return t
    stripped, why = _strip_structure_prefix(p)
    if why:
        return PatchResult(p, False, why)
    if stripped is None:
        return r
    r2 = _apply_core(doc, texts, stripped)
    return PatchResult(p, r2.applied, r2.reason, r2.slot)


def _apply_core(doc: Document, texts: list[str], p: Patch) -> PatchResult:
    if not p.find:
        return PatchResult(p, False, "find 为空")
    if p.find == p.replace:
        return PatchResult(p, False, "find 与 replace 相同")
    if "\n" in p.find or "\n" in p.replace:
        return PatchResult(p, False, "补丁不得跨行")
    if len(p.find) > MAX_FIND_CHARS:
        return PatchResult(p, False, f"find 超过 {MAX_FIND_CHARS} 字，疑似整段重写")
    if PLACEHOLDER_RE.search(p.replace) or PLACEHOLDER_RE.search(p.find):
        return PatchResult(p, False, "补丁中不得出现占位符 {{Pn}}")

    views = [_expand(t, doc.placeholders) for t in texts]
    hits = [(i, pos) for i, (u, _) in enumerate(views) for pos in _find_all(u, p.find)]
    if not hits:
        return PatchResult(p, False, "find 在初稿中不存在（可能跨越了结构边界或不是逐字摘录）")
    if len(hits) > 1:
        return PatchResult(p, False, f"find 在初稿中出现 {len(hits)} 次，有歧义")
    i, s = hits[0]
    e = s + len(p.find)
    _, spans = views[i]

    inner: list[_Span] = []
    for sp in spans:
        if sp.u_end <= s or sp.u_start >= e:
            continue
        if s <= sp.u_start and sp.u_end <= e:
            inner.append(sp)
        else:
            return PatchResult(p, False, f"补丁切断了受保护内容「{doc.placeholders[sp.pid]}」", i)

    # 把 replace 中的受保护原文重新掩码回原占位符；每个受保护片段必须恰好保留一次
    rep = p.replace
    for sp in sorted(inner, key=lambda x: -len(doc.placeholders[x.pid])):
        val = doc.placeholders[sp.pid]
        k = _find_outside_placeholders(rep, val)
        if k == -1:
            return PatchResult(p, False, f"补丁丢失了受保护内容「{val}」", i)
        rep = rep[:k] + PLACEHOLDER.format(sp.pid) + rep[k + len(val):]
    for sp in inner:
        val = doc.placeholders[sp.pid]
        if _find_outside_placeholders(rep, val) != -1:
            return PatchResult(p, False, f"补丁复制了受保护内容「{val}」", i)

    ms, me = _u2m(s, spans), _u2m(e, spans)
    new = texts[i][:ms] + rep + texts[i][me:]
    if not check_placeholders(new, doc.slots[i].ids).ok:
        return PatchResult(p, False, "合并后占位符校验失败", i)
    texts[i] = new
    return PatchResult(p, True, "", i)


def results_to_dicts(results: list[PatchResult]) -> list[dict]:
    return [{**asdict(r.patch), "applied": r.applied, "reject_reason": r.reason or None} for r in results]
