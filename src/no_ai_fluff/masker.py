"""Stage 0：结构感知拆分与确定性掩码层（Masker）。

职责：
1. 结构零触碰剥离 —— 把 Markdown / HTML / 纯文本拆成「模板 + 正文槽位」。
   围栏代码块、数学块、分割线、空行、表格分隔行、HTML 的 <script>/<style>/<code>/<pre>/<svg>
   及所有标签属性都留在模板里原样直通，不消耗任何 Token；只有可见的自然语言正文进入槽位。
2. 行内冻结 —— 把正文里的行内代码、公式、URL、路径、提交号、标识符、复杂度符号、
   工程缩写等替换成 {{P0}}、{{P1}} …… 占位符（全文档全局编号，每个编号只出现一次）。
   日常散装英语（edge case、overhead、trade-off……）故意不冻结，交给往返翻译转成中文。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

SLOT_TOKEN = "\x00{}\x00"
_SLOT_RE = re.compile(r"\x00(\d+)\x00")

PLACEHOLDER = "{{{{P{}}}}}"  # -> {{P3}}
PLACEHOLDER_RE = re.compile(r"\{\{P(\d+)\}\}")
# 翻译模型偶尔会把占位符写成 {{ P0 }}、｛｛P0｝｝、{P0} 等变体，统一规整回规范形式
_LOOSE_PLACEHOLDER_RE = re.compile(r"[{｛]{1,2}\s*[PpＰ]\s*(\d+)\s*[}｝]{1,2}")

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


@dataclass
class Slot:
    """一个可翻译的正文槽位。"""

    original: str  # 原始正文（未掩码）
    masked: str  # 掩码后的正文，含 {{Pn}}
    ids: list[int] = field(default_factory=list)  # 本槽位持有的占位符编号（按出现顺序）

    @property
    def translatable(self) -> bool:
        """只有含中文的槽位才需要往返翻译；纯英文/纯符号槽位原样直通。"""
        return bool(_CJK_RE.search(PLACEHOLDER_RE.sub("", self.masked)))


@dataclass
class Document:
    fmt: str  # "markdown" | "html" | "text"
    template: str  # 结构骨架，正文位置用 \x00n\x00 标记
    slots: list[Slot]
    placeholders: list[str]  # 全局占位符表：编号 -> 冻结的原文
    # “软”占位符：整句英文、首字母大写的英文词。冻结它们只为防止往返翻译弄坏，
    # 宿主以“中英混杂”为由把它们改成中文时允许去掉（见 patcher._apply_core）
    soft: list[int] = field(default_factory=list)
    dropped: set[int] = field(default_factory=set, repr=False, compare=False)  # 本次定稿中被译掉的软占位符

    def render(self, slot_texts: list[str] | None = None, unmask_text: bool = True) -> str:
        """把槽位文本回填进模板。slot_texts 为掩码文本（默认取各槽位的 masked）。"""
        texts = slot_texts if slot_texts is not None else [s.masked for s in self.slots]
        if unmask_text:
            ph = [("" if i in self.dropped else v) for i, v in enumerate(self.placeholders)]
            texts = [unmask(t, ph) for t in texts]
        return _SLOT_RE.sub(lambda m: texts[int(m.group(1))], self.template)


# ---------------------------------------------------------------------------
# 行内冻结规则
# ---------------------------------------------------------------------------

# 用 ASCII 自定义边界：Python 的 \b 把汉字也当作 \w，"用Redis缓存" 中 R 前面没有 \b。
_L = r"(?<![A-Za-z0-9_])"
_R = r"(?![A-Za-z0-9_])"

# 无通行中文译法、应原样保留的工程术语（大小写不敏感）。可通过 extra_terms 扩展。
DEFAULT_TERMS = [
    "API", "SDK", "SQL", "SSE", "Mutex", "Token", "Tokens", "Commit", "Commits", "Embedding",
    "Embeddings", "Prompt", "Prompts", "Goroutine", "Goroutines", "Rebase", "Webhook",
    "Schema", "JSON", "YAML", "Regex", "Lambda", "Tensor", "Transformer", "Docker",
    "Kubernetes", "Redis", "Kafka", "Python", "Go", "Golang", "Rust", "Java", "TypeScript",
    "JavaScript", "React", "Vue", "Linux", "Git", "GitHub", "Nginx", "MySQL", "PostgreSQL",
    "Markdown", "MCP", "Agent", "Subagent", "Hook", "Hooks", "Claude", "Codex", "DeepSeek",
    "OpenRouter", "Gemini", "GPT",
]

# 首字母大写但其实是日常散装英语的词：不当作专有名词冻结，让它们暴露给往返翻译。
LOOSE_CAPITALIZED = {
    "edge", "fallback", "overhead", "trade", "critical", "sync", "issue", "case", "note",
    "todo", "fix", "bug", "feature", "update", "refactor", "summary", "test", "tests",
    "breaking", "changes", "change", "root", "cause", "impact", "context", "background",
    "pros", "cons", "insight", "takeaway", "key", "overview", "highlights", "baseline",
    "tradeoff", "tradeoffs", "workaround", "best", "practice", "practices", "deep", "dive",
}

_FILE_EXT = (
    "py|pyi|js|mjs|cjs|ts|tsx|jsx|go|rs|java|kt|swift|c|cc|cpp|h|hpp|cs|rb|php|vue|svelte|"
    "md|mdx|rst|txt|json|jsonl|yaml|yml|toml|ini|cfg|conf|env|lock|xml|html|htm|css|scss|"
    "sh|bash|zsh|ps1|bat|sql|csv|tsv|ipynb|proto|gradle|dockerfile|tf|lua|dart|r|scala"
)

_PATTERNS: list[tuple[str, str]] = [
    # 已有的模板语法 / 疑似占位符：整体冻结，避免与我们的 {{Pn}} 混淆
    ("tpl", r"\{\{[^{}\n]*\}\}"),
    # 行内代码（支持多反引号；组合正则中必须用命名反向引用）
    ("code", r"(?P<bt>`+)(?!`).+?(?<!`)(?P=bt)(?!`)"),
    # 公式
    ("math", r"\$\$.+?\$\$|\\\(.+?\\\)|\\\[.+?\\\]|\$(?=[^\s$\d])[^$\n]+?(?<=\S)\$(?![\dA-Za-z])"),
    # Markdown 链接/图片的目标部分 (...)，方括号留给正文
    ("linktarget", r"(?<=\])\([^)\s]+(?:\s+\"[^\"]*\")?\)"),
    # URL 与尖括号自动链接
    ("url", r"<(?:https?|ftp)://[^>\s]+>|(?:https?|ftp)://[^\s<>()\[\]{}\"'，。；：！？、）》」』]+"),
    ("email", _L + r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}" + _R),
    # HTML 实体
    ("entity", r"&(?:[A-Za-z]+|#\d+|#x[0-9A-Fa-f]+);"),
    # Windows / 类 Unix 路径与带扩展名的文件名
    ("winpath", _L + r"[A-Za-z]:\\[^\s，。；：！？、）》」』\"'<>|]+"),
    ("path", r"(?<![A-Za-z0-9_/])(?:~|\.{1,2})?/?(?:[A-Za-z0-9_.@-]+/)+[A-Za-z0-9_.@-]*" + _R + r"/?"),
    ("file", _L + r"[A-Za-z0-9_.-]+\.(?:" + _FILE_EXT + r")" + _R),
    # 复杂度符号 O(n log n)、Θ(n)、Ω(1)
    ("bigo", r"(?<![A-Za-z0-9_])[OΘΩo]\((?:[^()\n]|\([^()\n]*\))*\)"),
    # 整句英文（至少 4 个连续英文单词，如引用的口号、英文原句）：整体冻结。
    # M4 发现逐词处理会把 “Build a solid Monolith, don't build …” 译成半中半英的怪句。
    ("ensent", _L + r"[A-Za-z][A-Za-z'’]*(?:-[A-Za-z]+)*(?:,?[ \t]+[A-Za-z][A-Za-z'’]*(?:-[A-Za-z]+)*){3,}[.!?]?" + _R),
    # PR / Issue 编号
    ("issue", r"(?:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)?#\d+" + _R + r"|(?<![A-Za-z0-9_])!\d+" + _R),
    # 函数调用 / 点号成员访问：foo()、obj.method()、os.path.join
    ("call", _L + r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\(\)"),
    ("dotted", _L + r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+" + _R),
    # 版本号
    ("version", _L + r"v\d+(?:\.\d+)*(?:-[A-Za-z0-9.]+)?" + _R + r"|" + _L + r"\d+\.\d+\.\d+(?:-[A-Za-z0-9.]+)?" + _R),
    # 提交哈希（7~40 位十六进制，需同时含字母和数字，回调里二次校验）
    ("hash", _L + r"[0-9a-f]{7,40}" + _R),
    # 标识符：snake_case / camelCase / PascalCase（内含大写）/ 多段 kebab-case
    ("snake", _L + r"[A-Za-z_][A-Za-z0-9]*_[A-Za-z0-9_]+" + _R),
    ("camel", _L + r"[a-z]+[A-Z][A-Za-z0-9]*" + _R),
    ("pascal", _L + r"[A-Z][a-z0-9]+[A-Z][A-Za-z0-9]*" + _R),
    ("kebab", _L + r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+){2,}" + _R),
    # 全大写缩写 / 带数字的代号：API、HTTP/2、CI/CD、P99、K8s 的 K8
    ("acronym", _L + r"[A-Z][A-Z0-9]+s?(?:[-/][A-Z0-9]+)*" + _R),
]

_COMBINED = re.compile("|".join(f"(?P<{name}>{pat})" for name, pat in _PATTERNS), re.DOTALL)

_CAPWORD_RE = re.compile(_L + r"[A-Z][a-z]+(?:'[a-z]+)?" + _R)


def _terms_regex(terms: list[str]) -> re.Pattern[str] | None:
    terms = sorted({t for t in terms if t}, key=len, reverse=True)
    if not terms:
        return None
    return re.compile(_L + "(?:" + "|".join(re.escape(t) for t in terms) + ")" + _R, re.IGNORECASE)


class Masker:
    """有状态的掩码器：同一文档内的占位符全局唯一编号。"""

    def __init__(self, extra_terms: list[str] | None = None, freeze_proper_nouns: bool = True):
        self.placeholders: list[str] = []
        self._terms_re = _terms_regex(DEFAULT_TERMS + list(extra_terms or []))
        self.freeze_proper_nouns = freeze_proper_nouns
        self.soft: list[int] = []
        self._soft_spans: set[tuple[int, int]] = set()

    def _spans(self, text: str) -> list[tuple[int, int]]:
        spans: list[tuple[int, int]] = []
        self._soft_spans = set()
        for m in _COMBINED.finditer(text):
            if m.lastgroup == "ensent":
                self._soft_spans.add(m.span())
            if m.lastgroup == "hash":
                tok = m.group(0)
                if not (re.search(r"\d", tok) and re.search(r"[a-f]", tok)):
                    continue
            spans.append(m.span())
        extra: list[tuple[int, int]] = []
        if self._terms_re is not None:
            extra += [m.span() for m in self._terms_re.finditer(text)]
        if self.freeze_proper_nouns:
            for m in _CAPWORD_RE.finditer(text):
                if m.group(0).lower() not in LOOSE_CAPITALIZED:
                    extra.append(m.span())
                    self._soft_spans.add(m.span())
        # 合并：主规则优先，补充规则只填补未被覆盖的区间
        for s, e in sorted(extra):
            if all(e <= a or s >= b for a, b in spans):
                spans.append((s, e))
        spans.sort()
        return spans

    def mask(self, text: str) -> tuple[str, list[int]]:
        out: list[str] = []
        ids: list[int] = []
        pos = 0
        for s, e in self._spans(text):
            if s < pos:
                continue
            out.append(text[pos:s])
            pid = len(self.placeholders)
            if (s, e) in self._soft_spans:
                self.soft.append(pid)
            self.placeholders.append(text[s:e])
            ids.append(pid)
            out.append(PLACEHOLDER.format(pid))
            pos = e
        out.append(text[pos:])
        return "".join(out), ids

    def slot(self, text: str) -> Slot:
        masked, ids = self.mask(text)
        return Slot(original=text, masked=masked, ids=ids)


def mask_text(text: str, extra_terms: list[str] | None = None) -> tuple[str, list[str]]:
    """便捷函数：对一段纯文本做行内冻结，返回 (掩码文本, 占位符表)。"""
    m = Masker(extra_terms)
    masked, _ = m.mask(text)
    return masked, m.placeholders


# ---------------------------------------------------------------------------
# 还原与校验
# ---------------------------------------------------------------------------


def normalize_placeholders(text: str) -> str:
    return _LOOSE_PLACEHOLDER_RE.sub(lambda m: PLACEHOLDER.format(int(m.group(1))), text)


def unmask(text: str, placeholders: list[str]) -> str:
    def repl(m: re.Match[str]) -> str:
        i = int(m.group(1))
        return placeholders[i] if 0 <= i < len(placeholders) else m.group(0)

    return PLACEHOLDER_RE.sub(repl, text)


@dataclass
class PlaceholderCheck:
    ok: bool
    missing: list[int]
    extra: list[int]
    duplicated: list[int]


def check_placeholders(text: str, expected: list[int]) -> PlaceholderCheck:
    """校验 text 中的占位符集合与 expected 完全一致（每个恰好出现一次，不多不少）。"""
    found = [int(x) for x in PLACEHOLDER_RE.findall(text)]
    exp = set(expected)
    missing = sorted(exp - set(found))
    extra = sorted(set(found) - exp)
    duplicated = sorted({i for i in found if found.count(i) > 1})
    return PlaceholderCheck(not (missing or extra or duplicated), missing, extra, duplicated)


_MARKUP_TOKENS = ("**", "~~", "[", "]")


def markup_signature(text: str) -> tuple[int, ...]:
    """行内 Markdown 标记计数，用于检测翻译是否吞掉了加粗/链接括号。"""
    stripped = PLACEHOLDER_RE.sub("", text)
    return tuple(stripped.count(t) for t in _MARKUP_TOKENS)


# ---------------------------------------------------------------------------
# 结构拆分：Markdown
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_HR_RE = re.compile(r"^\s{0,3}([-*_])(?:\s*\1){2,}\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?\s*$")
_HTML_LINE_RE = re.compile(r"^\s*</?[A-Za-z][^>]*>\s*$")
_LINE_PREFIX_RE = re.compile(
    r"^(\s*(?:>\s?)*\s*(?:#{1,6}\s+|(?:[-*+]|\d{1,3}[.)])\s+(?:\[[ xX]\]\s+)?)?)"
)
_DEF_REF_RE = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*\S+")


def _split_ws(s: str) -> tuple[str, str, str]:
    """把字符串拆成 (前导空白, 正文, 尾随空白)。"""
    core = s.strip()
    if not core:
        return s, "", ""
    start = s.index(core)
    return s[:start], core, s[start + len(core):]


def parse_markdown(text: str, extra_terms: list[str] | None = None) -> Document:
    masker = Masker(extra_terms)
    slots: list[Slot] = []
    tpl: list[str] = []

    def add_slot(body: str) -> str:
        lead, core, trail = _split_ws(body)
        if not core:
            return body
        slots.append(masker.slot(core))
        return lead + SLOT_TOKEN.format(len(slots) - 1) + trail

    lines = text.split("\n")
    i = 0
    fence: str | None = None
    in_math = False
    # YAML front matter
    if lines and lines[0].strip() == "---":
        for j in range(1, len(lines)):
            if lines[j].strip() in ("---", "..."):
                tpl.extend(lines[: j + 1])
                i = j + 1
                break

    while i < len(lines):
        line = lines[i]
        i += 1
        if fence is not None:
            tpl.append(line)
            stripped = line.strip()
            if stripped.startswith(fence[0] * len(fence)) and set(stripped) <= {fence[0]}:
                fence = None
            continue
        if in_math:
            tpl.append(line)
            if line.strip().endswith("$$"):
                in_math = False
            continue
        m = _FENCE_RE.match(line)
        if m:
            fence = m.group(1)
            tpl.append(line)
            continue
        stripped = line.strip()
        if stripped.startswith("$$"):
            tpl.append(line)
            if not (len(stripped) > 2 and stripped.endswith("$$")):
                in_math = True
            continue
        if (
            not stripped
            or _HR_RE.match(line)
            or _TABLE_SEP_RE.match(line)
            or _HTML_LINE_RE.match(line)
            or _DEF_REF_RE.match(line)
        ):
            tpl.append(line)
            continue
        if stripped.startswith("|") and stripped.count("|") >= 2:
            # 表格行：逐单元格抽取（转义的 \| 不作为分隔符）
            parts = re.split(r"(?<!\\)\|", line)
            tpl.append("|".join(add_slot(p) if k not in (0, len(parts) - 1) else p
                                for k, p in enumerate(parts)))
            continue
        pm = _LINE_PREFIX_RE.match(line)
        prefix = pm.group(1) if pm else ""
        tpl.append(prefix + add_slot(line[len(prefix):]))

    return Document("markdown", "\n".join(tpl), slots, masker.placeholders, masker.soft)


# ---------------------------------------------------------------------------
# 结构拆分：HTML
# ---------------------------------------------------------------------------

_HTML_TOKEN_RE = re.compile(
    r"(<!--.*?-->|<!\[CDATA\[.*?\]\]>|<![^>]*>|<\?.*?\?>|<[A-Za-z/][^>]*>)", re.DOTALL
)
_TAG_NAME_RE = re.compile(r"<\s*(/?)\s*([A-Za-z][A-Za-z0-9:-]*)")
HTML_SKIP_TAGS = {"script", "style", "code", "pre", "svg", "math", "textarea", "kbd", "samp", "var", "noscript", "template"}
_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


def parse_html(text: str, extra_terms: list[str] | None = None) -> Document:
    masker = Masker(extra_terms)
    slots: list[Slot] = []
    tpl: list[str] = []
    skip_stack: list[str] = []
    for tok in _HTML_TOKEN_RE.split(text):
        if not tok:
            continue
        if _HTML_TOKEN_RE.fullmatch(tok):
            tpl.append(tok)
            m = _TAG_NAME_RE.match(tok)
            if m:
                closing, name = m.group(1) == "/", m.group(2).lower()
                if name in HTML_SKIP_TAGS:
                    if closing:
                        if name in skip_stack:
                            while skip_stack and skip_stack.pop() != name:
                                pass
                    elif not tok.rstrip(">").rstrip().endswith("/") and name not in _VOID_TAGS:
                        skip_stack.append(name)
            continue
        if skip_stack:
            tpl.append(tok)
            continue
        lead, core, trail = _split_ws(tok)
        if not core:
            tpl.append(tok)
            continue
        slots.append(masker.slot(core))
        tpl.append(lead + SLOT_TOKEN.format(len(slots) - 1) + trail)
    return Document("html", "".join(tpl), slots, masker.placeholders, masker.soft)


# ---------------------------------------------------------------------------
# 结构拆分：纯文本（按段落/行）
# ---------------------------------------------------------------------------


def parse_text(text: str, extra_terms: list[str] | None = None) -> Document:
    masker = Masker(extra_terms)
    slots: list[Slot] = []
    tpl: list[str] = []
    for line in text.split("\n"):
        lead, core, trail = _split_ws(line)
        if not core:
            tpl.append(line)
            continue
        slots.append(masker.slot(core))
        tpl.append(lead + SLOT_TOKEN.format(len(slots) - 1) + trail)
    return Document("text", "\n".join(tpl), slots, masker.placeholders, masker.soft)


_HTML_SNIFF_RE = re.compile(r"^\s*(?:<!DOCTYPE html|<html|<body|<div|<section|<article|<p[\s>])", re.IGNORECASE)
_MD_SNIFF_RE = re.compile(r"(^|\n)\s{0,3}(#{1,6}\s|[-*+]\s|\d+\.\s|```|>\s|\|.*\|)")


def detect_format(text: str) -> str:
    if _HTML_SNIFF_RE.match(text):
        return "html"
    if _MD_SNIFF_RE.search(text) or "`" in text or "**" in text:
        return "markdown"
    return "text"


def parse(text: str, fmt: str = "auto", extra_terms: list[str] | None = None) -> Document:
    """拆分入口。fmt: auto | markdown | html | text"""
    text = text.replace("\x00", "").replace("\r\n", "\n")
    if fmt == "auto":
        fmt = detect_format(text)
    if fmt == "html":
        return parse_html(text, extra_terms)
    if fmt == "text":
        return parse_text(text, extra_terms)
    return parse_markdown(text, extra_terms)
