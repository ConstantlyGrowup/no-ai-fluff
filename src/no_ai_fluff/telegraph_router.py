"""领域分诊子 Agent（第二代双通道门控）。

雷达的“电报式短句群”只看句法统计量（片段数、片段均长、虚词率），旅游行程、注意事项、字段清单这类
“短而具体”的句子形状一样会命中。命中的句子交给一个上下文干净的轻量判断：这些句子是否出自技术/工程语境？

    任一句为 true —— 整篇走强化通道，电报提示全部保留；
    全部为 false —— 去掉电报提示，审校提示词与第一代（基础通道）逐字相同。

判断结果按“分诊版本 + 模型 + 句子”的 sha256 落盘缓存，重跑零调用、可复现、可审计。
分诊只看到这几条句子本身，看不到全文、往返稿和审校规则。

生产路径（MCP / CLI / Python SDK）不调用任何外部分诊 API：分诊由宿主模型握手完成（host_router + parse_host_triage，
见 Pipeline.prepare / complete_prepare）。评测脚本才显式注入 default_router（DeepSeek 官方 API）。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .masker import unmask
from .radar import RadarReport, telegraph_sentences

ROUTER_MODEL = "deepseek-flash"
ROUTER_EFFORT = "low"
ROUTER_MAX_TOKENS = 4000

DOMAIN_ROUTER_VERSION = "domain-router-v1"
DOMAIN_ROUTER_PROMPT = """下面几句中文摘自同一篇文档，都被一个统计雷达标成了“电报式短句群”（一句话被逗号、顿号、分号切成很多短片段）。
请逐句判断：这句话是不是出自技术或工程语境——讲代码、软件系统、数据、算法、研发流程、技术方案或技术岗位工作，读者需要一定专业背景？

true：技术/工程语境。例：“删除 `foo.ts` 与三处调用，无数据迁移”；“典型结果约 1 ns、0 allocs”；“完善支付链路自动化测试，覆盖补偿和对账场景”。
false：日常生活、旅行、消费、情感、通用事务。例：“苏州拙政园、平江路、山塘街游船”；“注意：五一园林人多，门票提前约，早上早点进”。

只看句子讲的是什么领域，不评价写得好不好。

{items}

只输出 JSON 数组，每句一项，顺序与编号一致：
[{{"id": 1, "technical": true 或 false, "why": "不超过 20 字"}}]"""


@dataclass(frozen=True)
class RouterSpec:
    version: str
    prompt: str
    field: str  # 模型输出里的布尔字段
    doc_level: bool  # True：任一句为 true 则保留全部电报提示，否则全部去掉；False：逐句过滤


DOMAIN_SPEC = RouterSpec(DOMAIN_ROUTER_VERSION, DOMAIN_ROUTER_PROMPT, "technical", True)  # 中文第二代
DOMAIN_ROUTER_PROMPT_EN = """Each sentence below comes from the same document. A statistical radar flagged each one as a "telegraphic sentence chain": a sentence cut into several short phrases with very few function words.
For each sentence, decide only whether it comes from a technical or engineering context: code, software systems, data, algorithms, development process, technical design, or technical work, where the reader needs some technical background.

true: technical or engineering context. Examples: "Remove `foo.ts` and its three call sites; no data migration"; "Typical results: about 1 ns, 0 allocs"; "Spawning a process is expensive (new page tables, file descriptors)".
false: everyday life, travel, shopping, feelings, general advice. Examples: "Morning sunlight, 10 minutes, then breakfast"; "Silence notifications, use website blockers, and keep your phone in another room".

Judge only the domain, not the writing quality.

{items}

Output only a JSON array, one item per sentence, in the same order:
[{{"id": 1, "technical": true or false, "why": "at most 12 words"}}]"""
DOMAIN_SPEC_EN = RouterSpec("domain-router-en-v1", DOMAIN_ROUTER_PROMPT_EN, "technical", True)  # English en5
# 键为各语言规则的原名（代际别名由各自的 resolve_rules 先换成原名，避免中英 "v2" 冲突）
ROUTER_SPECS = {"v16": DOMAIN_SPEC, "en5": DOMAIN_SPEC_EN}
DOMAIN_TRIAGE_RULES = set(ROUTER_SPECS)


@dataclass
class Verdict:
    sentence: str
    telegraph: bool
    why: str = ""
    cached: bool = False
    pending: bool = False  # 没有缓存、也没有可调用的模型：等宿主分诊（暂按 true 保守处理）


@dataclass
class RouteResult:
    report: RadarReport
    verdicts: list[Verdict] = field(default_factory=list)
    calls: int = 0
    usage: list[dict] = field(default_factory=list)
    doc_level: bool = False

    @property
    def pending(self) -> list[str]:
        return [v.sentence for v in self.verdicts if v.pending]

    @property
    def needs_host(self) -> bool:
        """还缺宿主分诊才能定通道：有待分诊的句子，且（文档级时）已缓存的判定里还没有任何一句为 true。"""
        if not self.pending:
            return False
        if self.doc_level and any(v.telegraph and not v.pending for v in self.verdicts):
            return False  # 文档级：已有一句是技术语境，整篇已确定走强化通道
        return True

    @property
    def kept(self) -> int:
        """被判为 true（技术语境）的句子数。"""
        return sum(v.telegraph for v in self.verdicts)

    @property
    def dense(self) -> bool:
        return any(h.kind == "telegraph" for h in self.report.hints)


ChatFn = Callable[[str], tuple[str, dict]]  # prompt -> (模型原始输出, usage)


class TelegraphRouter:
    """chat：调用模型的函数（benchmarks 里接 DeepSeek 客户端，effort=low）；为 None 时只读缓存，未命中的句子按 true 保守保留。"""

    def __init__(self, chat: ChatFn | None, cache_dir: str | Path | None, model: str = ROUTER_MODEL,
                 spec: "RouterSpec | None" = None):
        self.chat = chat
        self.model = model
        self.spec = spec or DOMAIN_SPEC
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _key(self, sentence: str) -> str:
        return hashlib.sha256(f"{self.spec.version}\n{self.model}\n{sentence}".encode("utf-8")).hexdigest()

    def _load(self, sentence: str) -> Verdict | None:
        if not self.cache_dir:
            return None
        p = self.cache_dir / f"{self._key(sentence)}.json"
        if not p.exists():
            return None
        d = json.loads(p.read_text("utf-8"))
        return Verdict(sentence, bool(d[self.spec.field]), d.get("why", ""), cached=True)

    def _save(self, v: Verdict, raw: str, usage: dict) -> None:
        if not self.cache_dir:
            return
        rec = {"version": self.spec.version, "model": self.model, "sentence": v.sentence, self.spec.field: v.telegraph,
               "why": v.why, "raw": raw, "usage": usage}
        (self.cache_dir / f"{self._key(v.sentence)}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), "utf-8")

    def triage(self, sentences: list[str]) -> tuple[list[Verdict], int, list[dict]]:
        """逐句分诊。返回 (判定列表, 新发起的调用次数, 每次调用的 usage)。同一批里未缓存的句子合成一次调用。"""
        out: list[Verdict | None] = [self._load(s) for s in sentences]
        todo = [i for i, v in enumerate(out) if v is None]
        calls, usages = 0, []
        if todo and self.chat is not None:
            items = "\n".join(f"{k + 1}. {sentences[i]}" for k, i in enumerate(todo))
            raw, usage = self.chat(self.spec.prompt.format(items=items))
            calls, usages = 1, [usage]
            parsed = parse_verdicts(raw, len(todo), self.spec.field)
            for k, i in enumerate(todo):
                if parsed[k] is None:
                    continue  # 解析失败：不缓存，按保守策略保留
                tele, why = parsed[k]
                v = Verdict(sentences[i], tele, why)
                self._save(v, raw, usage)
                out[i] = v
        if self.chat is None:
            return [v or Verdict(sentences[i], True, "待宿主分诊", pending=True) for i, v in enumerate(out)], calls, usages
        return [v or Verdict(sentences[i], True, "未分诊，保守保留") for i, v in enumerate(out)], calls, usages

    def record(self, sentences: list[str], flags: list[bool], why: str = "宿主分诊") -> None:
        """把宿主模型给出的判定写进缓存（握手第二步）。"""
        for sent, flag in zip(sentences, flags):
            self._save(Verdict(sent, bool(flag), why), raw="", usage={})

    def prompt_for(self, sentences: list[str]) -> str:
        items = "\n".join(f"{k + 1}. {x}" for k, x in enumerate(sentences))
        return self.spec.prompt.format(items=items)


def parse_verdicts(raw: str, n: int, key: str = "telegraph") -> list[tuple[bool, str] | None]:
    res: list[tuple[bool, str] | None] = [None] * n
    # 低推理强度的模型偶尔在数组后面自言自语、再输出一遍数组；取第一个能完整解析的 JSON 数组
    arr = None
    dec = json.JSONDecoder()
    for m in re.finditer(r"\[", raw):
        try:
            cand, _end = dec.raw_decode(raw, m.start())
        except json.JSONDecodeError:
            continue
        if isinstance(cand, list) and cand and all(isinstance(x, dict) for x in cand):
            arr = cand
            break
    if arr is None:
        return res
    for k, item in enumerate(arr):
        if not isinstance(item, dict):
            continue
        idx = item.get("id", k + 1)
        if isinstance(idx, int) and 1 <= idx <= n and isinstance(item.get(key), bool):
            res[idx - 1] = (item[key], str(item.get("why", ""))[:60])
    return res


def route_report(report: RadarReport, masked_slots: list[str], placeholders: list[str],
                 router: TelegraphRouter | None, sentences_fn: Callable = telegraph_sentences) -> RouteResult:
    """把雷达报告里的“电报式短句群”提示交给分诊子 Agent，只留下判为 true 的。router 为 None 时原样返回。"""
    tele = [h for h in report.hints if h.kind == "telegraph"]
    if not tele or router is None:
        return RouteResult(report)
    sents = sentences_fn(masked_slots)  # 中文版 radar.telegraph_sentences；英文版传 en.radar.telegraph_sentences
    if len(sents) != len(tele):  # 理论上不会发生；对不上就不过滤，保守保留
        return RouteResult(report)
    verdicts, calls, usages = router.triage([unmask(s, placeholders).strip() for _, s in sents])
    if router.spec.doc_level:  # 文档级：整篇一个开关
        flags = [any(v.telegraph for v in verdicts)] * len(verdicts)
    else:
        flags = [v.telegraph for v in verdicts]
    keep = iter(flags)
    new = type(report)([h for h in report.hints if h.kind != "telegraph" or next(keep)])
    return RouteResult(new, verdicts, calls, usages, router.spec.doc_level)


def router_cache_dir() -> Path:
    import os

    return Path(os.environ.get("NO_AI_FLUFF_ROUTER_CACHE") or Path.home() / ".cache" / "no-ai-fluff" / "telegraph_router")


HOST_MODEL = "host"


def host_router(rules: str, cache_dir: str | Path | None = None) -> TelegraphRouter:
    """生产路径用的分诊器：不调用任何模型，只读写本地缓存（model="host"）；缺的判定由宿主模型握手补上。"""
    return TelegraphRouter(None, cache_dir or router_cache_dir(), model=HOST_MODEL, spec=ROUTER_SPECS.get(rules, DOMAIN_SPEC))


def parse_host_triage(value, n: int, key: str = "technical") -> list[bool | None]:
    """宽容解析宿主的分诊结果，返回 n 个值；解析不了的位置为 None（调用方按 true 保守处理，且不写缓存）。

    接受：单个布尔（整篇同一判定）、布尔数组、[{"id": 1, "technical": true}, ...]、{"technical": true}、
    以及上述任一形式的 JSON 字符串或 "true"/"false"/"是"/"否" 文本。
    """
    def as_bool(x):
        if isinstance(x, bool):
            return x
        if isinstance(x, (int, float)) and x in (0, 1):
            return bool(x)
        if isinstance(x, str):
            t = x.strip().lower()
            if t in ("true", "yes", "y", "1", "是", "技术", "对"):
                return True
            if t in ("false", "no", "n", "0", "否", "不是", "日常"):
                return False
        return None

    out: list[bool | None] = [None] * n
    v = value
    if isinstance(v, str):
        b = as_bool(v)
        if b is not None:
            return [b] * n
        parsed = parse_verdicts(v, n, key)
        if any(x is not None for x in parsed):
            return [None if x is None else x[0] for x in parsed]
        try:
            v = json.loads(v)
        except (json.JSONDecodeError, ValueError):
            return [None] * n
    if isinstance(v, dict):
        b = as_bool(v.get(key, v.get("telegraph")))
        return [b] * n
    b = as_bool(v)
    if b is not None:
        return [b] * n
    if isinstance(v, list):
        for k, item in enumerate(v):
            if isinstance(item, dict):
                idx = item.get("id", k + 1)
                b = as_bool(item.get(key, item.get("telegraph")))
                if isinstance(idx, int) and 1 <= idx <= n and b is not None:
                    out[idx - 1] = b
            elif k < n:
                out[k] = as_bool(item)
    return out


def default_router(cache_dir: str | Path | None = None, rules: str = "v16") -> TelegraphRouter | None:
    """评测用：DeepSeek 官方 API（effort=low）同步分诊。生产路径不会自动调用它，只有显式注入时才用。
    没有 DeepSeek key 时返回 None。"""
    from .rtt import ChatClient, provider_key

    if not provider_key("deepseek"):
        return None
    cache = Path(cache_dir) if cache_dir else router_cache_dir()
    client = ChatClient(provider="deepseek", timeout=60)

    def chat(prompt: str) -> tuple[str, dict]:
        res = client.chat(ROUTER_MODEL, [{"role": "user", "content": prompt}], temperature=0.0,
                          max_tokens=ROUTER_MAX_TOKENS, extra={"reasoning_effort": ROUTER_EFFORT})
        return re.sub(r"<think>.*?</think>", "", res.text, flags=re.S).strip(), res.usage or {}

    return TelegraphRouter(chat, cache, spec=ROUTER_SPECS.get(rules, DOMAIN_SPEC))
