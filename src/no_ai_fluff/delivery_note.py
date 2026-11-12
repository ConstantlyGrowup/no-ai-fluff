"""定稿交付后的“整段重写”提示与护栏规则（中英两版）。

MCP 的 finalize_patched_delivery 会把这段说明附在定稿后面，所以任何接了 MCP 的宿主
（Claude Code、Codex、Qoder、WorkBuddy、ZCode、DeepSeek Harness 等）都能看到，
不依赖宿主是否认识 Skill。各宿主的 SKILL.md 模板里写的是同一句提示和同样的五条规则，
tests/test_delivery_note.py 检查它们没有走样。
"""

from __future__ import annotations

NOTE_ZH = (
    "如果你觉得其中哪一段还是太绕、黑话太重，局部小改不够顺口，可以直接告诉我“整体重写”或者“只重写第 X 段”，"
    "我会只针对那一段彻底换种直白写法，代码、公式和数字仍一个不动。"
)

NOTE_EN = (
    "If any paragraph still feels dense or jargon-heavy and needs a full rewrite instead of light edits, just tell me "
    "which part to rewrite in plain English (or paste those sentences). I will rewrite only that part from scratch and "
    "keep all code, paths, formulas and numbers untouched."
)

RULES_ZH = (
    "1. 只改点名的部分：只重写用户指出的那一段或那几句，其他段落一个字都不动。\n"
    "2. 硬内容零改动，不编造：代码块、行内代码、命令、文件路径、URL、LaTeX 公式、版本号和所有数字一字不差；"
    "不补原文没写的机制、后果或前提条件，正负、前后、方向照原文写。\n"
    "3. 真术语保留，包装词拆开：换个普通说法会让懂行的人误解的（如 P99、幂等、死锁、SHA256）原样保留；"
    "底座、收口、打通、闭环、抓手、赋能这类包装词，改成它实际指的动作。\n"
    "4. 拆开压缩句和长难句：用 →、+、/ 代替连接词的写法和缺主语的短片段，改成有主语、有“先、再、因为、所以”的完整句子。\n"
    "5. 从头把意思说清楚：可以整段重组语序，但忠于原意；不扩写、不加背景、不升华。"
)

RULES_EN = (
    "1. Touch only what was named: rewrite only the paragraph or sentences the user pointed to; leave the rest as is.\n"
    "2. Keep hard content intact and invent nothing: code blocks, inline code, commands, file paths, URLs, LaTeX "
    "formulas, version numbers and every number stay character for character; add no mechanism, consequence or "
    "condition the original did not state, and keep signs, order and direction as written.\n"
    "3. Keep genuine terms, unpack packaging jargon: terms an expert would misread if paraphrased (P99, idempotent, "
    "deadlock, SHA256) stay; words like drive alignment, operationalize, holistic, de-risk become the action they "
    "stand for.\n"
    "4. Break up compressed and long sentences: replace →, + and / used as words and subject-less fragments with "
    "complete sentences; split sentences over about 25 words, put back a dropped \"that\", unpack noun stacks.\n"
    "5. Say it plainly from scratch: reorder freely but stay faithful to the meaning; no padding, background or slogans."
)


def host_note_zh() -> str:
    return (
        "【给宿主的交付说明：从上面的“---”开始，以下内容都只给你看，不要写进交付物】\n"
        "0. 如果你是替主会话审校的子智能体：只把“---”之前的定稿交回主会话（回退到原文时也一样），"
        "不要转述本说明，也不要自己加下面的提示，提示由主会话负责。以下各条写给直接面对用户的会话。\n"
        "1. 把“---”之前的定稿原样交给用户，不要再自己润色。\n"
        "2. 交付时在聊天里附上下面这句提示。终端回复（exit=terminal）：正文后空一行、加一条分割线再附上；"
        "落盘文件（exit=file）：文件里只写定稿，这句提示只在聊天里报告文件路径之后说。\n"
        f"   提示：{NOTE_ZH}\n"
        "3. 之后如果用户点名某一段要整段重写，由你直接重写那一段，不用再调用本工具，并守住五条：\n"
        + "\n".join("   " + line for line in RULES_ZH.splitlines())
        + "\n   重写完只发出改好的那一段并说明改的是哪段；交付物是文件时，只替换文件里的那一段。"
    )


def host_note_en() -> str:
    return (
        "[For the host, about delivery: everything from the \"---\" above onward is for you only; keep it out of the "
        "deliverable]\n"
        "0. If you are a subagent editing for a main session: hand back only the final text before the \"---\" (also "
        "when the text fell back to the original); do not relay these notes or add the note below yourself, the main "
        "session does that. The items below are for the session that talks to the user.\n"
        "1. Deliver the final text before the \"---\" as is; do not polish it again.\n"
        "2. Add the note below in the chat. For a terminal reply (exit=terminal): after the text, a blank line and a "
        "horizontal rule, then the note. For a file (exit=file): the file holds only the final text; say the note in "
        "the chat after reporting the file path.\n"
        f"   Note: {NOTE_EN}\n"
        "3. If the user later names a paragraph to rewrite, rewrite just that part yourself without calling this tool "
        "again, and keep these five rules:\n"
        + "\n".join("   " + line for line in RULES_EN.splitlines())
        + "\n   Send only the rewritten part and say which part it was; if the deliverable is a file, replace only that "
        "part in the file."
    )
