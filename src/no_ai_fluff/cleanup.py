"""定稿前的确定性标点清理（不依赖模型）。

往返翻译与局部补丁拼接时，偶尔会留下重复或冲突的标点，例如“重点是：：”“完成了，。”。
这里逐槽位做最小清理，并且**尊重原文**：某种写法如果原文同一槽位里本来就有（作者有意为之），就不动它。
只处理中文标点；代码、路径等受保护内容已被冻结为 {{Pn}}，不会被触及。
"""

from __future__ import annotations

import re

# (说明, 匹配模式, 替换) —— 按顺序执行
_RULES: list[tuple[str, re.Pattern[str], str]] = [
    ("中文标点前多余空格", re.compile(r"[ \t]+(?=[，。；：、！？）》」』])"), ""),
    ("半角逗号与全角逗号叠用", re.compile(r"(?:,，|，,)"), "，"),
    ("逗号接句末标点", re.compile(r"[，、]+(?=[。！？；])"), ""),
    ("句号后再接逗号", re.compile(r"(?<=[。！？])[，、]+"), ""),
    ("冒号接句号", re.compile(r"：+(?=。)"), ""),
    ("分号接句号", re.compile(r"；+(?=。)"), ""),
    ("重复的同一标点", re.compile(r"([，。；：、])\1+"), r"\1"),
]


def clean_punctuation(text: str, reference: str = "") -> tuple[str, int]:
    """返回 (清理后的文本, 修改处数)。reference 为原文同一槽位，其中已有的写法不会被改。"""
    total = 0
    for _name, pat, rep in _RULES:
        if reference and pat.search(reference):
            continue  # 原文本来就这样写，尊重作者
        text, n = pat.subn(rep, text)
        total += n
    return text, total
