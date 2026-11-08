"""词对微筛：拦截“把初稿里已经说直白的短词，改回原文行话”的补丁（免词表、跨语言）。

整句回抄由 patcher 的 25 字守卫拦截；不到 25 字的短词回抄，单靠长提示约束拦不住——
宿主浸在几千字公文语境里，会把“人员分析”改回“人力盘点”，还能给出看似合理的理由。

做法分两步：
1. 代码确定性初筛：补丁新引入的片段（与 find 相比新增或替换的部分）逐字出现在原文里、
   却不在 RTT 初稿里，就是“疑似改回原文”的嫌疑补丁。宿主自己新写的表达直接放行。
2. 脱离语境微筛：只把嫌疑词对（左 = 初稿说法，右 = 补丁说法）拼成一个很小的请求，
   不给原文和上下文，让模型挑出“同一所指、右边退化成行话 / 公文腔 / 套话”的编号。
   修正错词、专名、生硬机翻词的词对必须保留。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher

_WS = re.compile(r"\s+")
_WORDISH = re.compile(r"[一-鿿A-Za-z0-9]")
_PAIR_SIDE_MAX = 48  # 词对每一侧展示的最大字数


@dataclass
class Suspect:
    patch_index: int  # 在补丁列表中的下标
    left: str  # 初稿里的说法（find）
    right: str  # 补丁改成的说法（replace）
    reverted: str  # 引入的、与原文逐字相同的片段


def _norm(s: str) -> str:
    return _WS.sub("", s)


def _clip(s: str) -> str:
    s = s.strip()
    return s if len(s) <= _PAIR_SIDE_MAX else s[: _PAIR_SIDE_MAX - 1] + "…"


def find_suspects(patches, original_text: str, rtt_text: str, min_chars: int = 2) -> list[Suspect]:
    """返回嫌疑补丁：补丁新引入的片段逐字出现在原文中、但不在初稿中。"""
    orig, rtt = _norm(original_text), _norm(rtt_text)
    out: list[Suspect] = []
    for k, p in enumerate(patches):
        sm = SequenceMatcher(None, p.find, p.replace, autojunk=False)
        # 候选片段：逐段新增 / 替换的部分，再加整条 replace。
        # 像“降低成本、提高效率 -> 降本增效”“自动吸尘 -> 自动集尘”这类改写，逐字对齐后新增的只剩一个字，
        # 只看差异片段会漏掉，所以整条 replace 也要检查。
        cands = [p.replace[j1:j2] for op, _i1, _i2, j1, j2 in sm.get_opcodes() if op in ("replace", "insert")]
        cands.append(p.replace)
        for c in cands:
            seg = _norm(c)
            if len(_WORDISH.findall(seg)) >= min_chars and seg in orig and seg not in rtt:
                out.append(Suspect(k, _clip(p.find), _clip(p.replace), seg))
                break
    return out


def build_term_review_prompt(suspects: list[Suspect]) -> str:
    lines = [f"{i}. {s.left} -> {s.right}" for i, s in enumerate(suspects, 1)]
    return (
        "以下每一行都是一处修改：把左边的说法换成了右边的说法。\n"
        + "\n".join(lines)
        + "\n\n逐行判断：把右边换回左边，普通读者理解到的意思会不会变？\n"
        "只有两个条件同时成立才选这一行：①意思完全不变（同一个动作、同一个对象、同样的数量和程度）；"
        "②左边是大白话，右边却换成了职场行话、公文腔、缩略黑话或空洞套话（例如“人员分析 -> 人力盘点”"
        "“降低成本、提高效率 -> 降本增效”）。\n"
        "下面这些都不要选：右边换了动作或对象（例如“删除 -> 归档”）、改了数量或程度（例如“大幅 -> 略微”）、"
        "修正了错词、专有名词或生硬机翻、右边本身更自然好懂。拿不准就不选。\n"
        "只输出要拒收的编号组成的 JSON 数组，例如 [1, 3]；没有就输出 []。"
    )


def parse_term_review(raw, n: int) -> set[int]:
    """解析微筛结果，返回 1 起始的编号集合；越界或无法解析的部分忽略。"""
    if isinstance(raw, (list, tuple, set)):
        items = list(raw)
    else:
        s = re.sub(r"<think>.*?</think>", "", str(raw or ""), flags=re.S)
        m = re.search(r"\[[^\[\]]*\]", s)
        try:
            items = json.loads(m.group(0)) if m else []
        except json.JSONDecodeError:
            items = re.findall(r"\d+", m.group(0)) if m else []
    out: set[int] = set()
    for x in items:
        try:
            v = int(x)
        except (TypeError, ValueError):
            continue
        if 1 <= v <= n:
            out.add(v)
    return out
