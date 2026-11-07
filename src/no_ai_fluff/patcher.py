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

from .masker import PLACEHOLDER, PLACEHOLDER_RE, Document, check_placeholders

AUDIT_INSTRUCTIONS = """你是中文技术文本审校员。【RTT 初稿】由【原文】经机器往返翻译得到，句式已变松弛。请对照原文全量审校初稿，只修下列问题：
1. 术语翻偏：专业术语被误译或直译（如“正定矩阵→积极确定矩阵”“逃逸分析→逃跑分析”），改回中文标准名；首次出现的硬核学科概念写成“中文标准名（English Term）”。
2. 逻辑失真：语气被弱化（“仅在…时”变“通常在…时”）、因果倒置、充要条件改变、推理步骤丢失、数字或量级与原文不符。
3. 隐喻与指代：空间动作隐喻改回直白因果；指代不明的“它/其”换成明确的名词。
4. 雷达提示里确实成立的问题（套话、散装英文、过长分句）可顺手修；误报不必理会。
输出规则：
- 只输出 JSON 数组：[{"find": "初稿原片段", "replace": "修改后片段", "reason": "原因"}]；初稿准确通顺就输出 []。
- find 必须逐字摘自【RTT 初稿】，在全文中唯一，不跨行，尽量短。
- 严禁重写全文或润色本来正确的句子；保持初稿松弛的短句，不要压缩回长句。
- 代码、路径、公式、URL、数字原样保留。"""


def build_audit_prompt(original: str, rtt_draft: str, radar_text: str) -> str:
    return (
        f"{AUDIT_INSTRUCTIONS}\n\n【原文】\n{original}\n\n【RTT 初稿】\n{rtt_draft}\n\n"
        f"【雷达提示】\n{radar_text}\n\n只输出 JSON 数组："
    )


# ---------------------------------------------------------------------------
# 补丁解析
# ---------------------------------------------------------------------------


class PatchFormatError(ValueError):
    pass


@dataclass
class Patch:
    find: str
    replace: str
    reason: str = ""


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
                raise PatchFormatError(f"JSON 解析失败：{e}") from None
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
        out.append(Patch(find, replace, str(item.get("reason", ""))))
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


def _apply_one(doc: Document, texts: list[str], p: Patch) -> PatchResult:
    if not p.find:
        return PatchResult(p, False, "find 为空")
    if p.find == p.replace:
        return PatchResult(p, False, "find 与 replace 相同")
    if "\n" in p.find or "\n" in p.replace:
        return PatchResult(p, False, "补丁不得跨行")
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
