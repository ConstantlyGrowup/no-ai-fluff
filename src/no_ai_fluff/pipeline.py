"""核心编排：两次握手（prepare / finalize）+ 超时与异常的无损回退。

    prepare_rtt_draft(text)                -> draft_id + 原文 + 往返参考稿 + 雷达提示 + 审校提示词
    finalize_patched_delivery(id, patches) -> 定稿

回退原则（任何一步出问题，交付物都不能比原文更差）：
- RTT 整体失败 / 超时 / 未配置 Key：初稿直接用原文，宿主模型仍可对原文开补丁；
- 单段 RTT 校验失败：该段退回原文（rtt.py 段级熔断）；
- 单条补丁破坏受保护内容：拒收该条（patcher.py）；
- 定稿最终校验不通过：整体退回原文。
"""

from __future__ import annotations

import inspect
import json
import os
import re
import secrets
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import telemetry
from .config import env_float, load_dotenv
from .masker import PLACEHOLDER_RE, Document, Masker, Slot, check_placeholders, parse
from .cleanup import clean_punctuation
from .patcher import (
    DEFAULT_AUDIT_RULES,
    MAX_FIND_BY_RULES,
    MAX_FIND_CHARS,
    PROBLEM_TYPES,
    Patch,
    PatchFormatError,
    apply_patches,
    build_audit_prompt,
    DENSE_RADAR_RULES,
    NO_CLICHE_RADAR_RULES,
    CJK_SPACE_RULES,
    ROUTED_RULES,
    resolve_rules,
    tidy_cjk_spaces,
    parse_patches,
    results_to_dicts,
)
from .radar import scan_single
from .telegraph_router import ROUTER_SPECS, TelegraphRouter, host_router, parse_host_triage, route_report
from .rtt import DEFAULT_BRIDGE, PROVIDERS, ChatClient, OpenRouterError, RoundTripTranslator, RTTResult, default_provider

EXITS = ("terminal", "file")
MODES = ("auto", "chat", "doc")


@dataclass
class PipelineConfig:
    provider: str = "openrouter"  # openrouter | tencent（翻译小模型所在服务商）
    model: str | None = None  # 为空时取服务商默认模型
    bridge: str = DEFAULT_BRIDGE
    chat_timeout_s: float = 10.0  # 对话类交付的 RTT 整体时限
    doc_timeout_s: float = 60.0  # 文档类交付的 RTT 整体时限
    doc_chars: int = 1500  # auto 模式下超过该长度按文档处理
    extra_terms: list[str] = field(default_factory=list)
    batch_chars: int | None = None  # 为空时取服务商默认值（小批次 + 高并行：延迟低，分界符不易丢）
    batch_segments: int | None = None
    max_workers: int | None = None
    max_segment_chars: int | None = None  # 超过该长度的段落按句切块翻译
    temperature: float = 0.1
    request_timeout_s: float = 30.0
    cache_dir: str | None = None  # OpenRouter 响应磁盘缓存（评测复现用）
    store_dir: str | None = None  # 草稿存放目录，默认 ~/.no-ai-fluff/drafts
    draft_ttl_s: float = 6 * 3600
    audit_rules: str = DEFAULT_AUDIT_RULES  # 宿主审校规则版本，见 patcher.AUDIT_RULES
    rtt_context_chars: int = 300  # 往返翻译时附带的中文上文长度（官方上下文模板）；0 关闭
    min_rtt_chars: int = 8  # 短于该长度且不含英文的槽位（表头、短标题）不送往返翻译
    source_lang: str = "zh"  # 待改文本的语言：往返翻译回程译回这种语言
    telemetry: bool = True

    @classmethod
    def from_env(cls, **overrides: Any) -> "PipelineConfig":
        load_dotenv()
        values: dict[str, Any] = dict(
            provider=os.environ.get("NO_AI_FLUFF_PROVIDER") or default_provider(),
            model=os.environ.get("NO_AI_FLUFF_MODEL") or None,
            bridge=os.environ.get("NO_AI_FLUFF_BRIDGE") or DEFAULT_BRIDGE,
            chat_timeout_s=env_float("NO_AI_FLUFF_CHAT_TIMEOUT", 10.0),
            doc_timeout_s=env_float("NO_AI_FLUFF_DOC_TIMEOUT", 60.0),
            extra_terms=[t.strip() for t in os.environ.get("NO_AI_FLUFF_TERMS", "").split(",") if t.strip()],
            cache_dir=os.environ.get("NO_AI_FLUFF_CACHE_DIR") or None,
            audit_rules=os.environ.get("NO_AI_FLUFF_AUDIT_RULES") or DEFAULT_AUDIT_RULES,
        )
        # 覆盖项必须在构造前合并：__post_init__ 会按最终的 provider 补全默认模型与批次参数
        values.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**values)

    def __post_init__(self) -> None:
        if self.provider not in PROVIDERS:
            raise ValueError(f"未知服务商 {self.provider}，可选：{', '.join(PROVIDERS)}")
        spec = PROVIDERS[self.provider]
        self.model = self.model or spec["default_model"]
        for k in ("batch_chars", "batch_segments", "max_workers", "max_segment_chars"):
            if getattr(self, k) is None:
                setattr(self, k, spec[k])


# ---------------------------------------------------------------------------
# 草稿：两次握手之间需要保留的全部状态
# ---------------------------------------------------------------------------


@dataclass
class Draft:
    draft_id: str
    created: float
    doc: Document
    rtt_slots: list[str]  # 每个槽位的掩码初稿（未翻译的槽位即原文）
    translated: list[int]  # 实际参与往返翻译的槽位
    meta: dict

    @property
    def original_text(self) -> str:
        return self.meta["original_text"]

    def rtt_text(self) -> str:
        return self.doc.render(self.rtt_slots)

    def to_json(self) -> dict:
        return {
            "draft_id": self.draft_id,
            "created": self.created,
            "doc": {
                "fmt": self.doc.fmt,
                "template": self.doc.template,
                "slots": [asdict(s) for s in self.doc.slots],
                "placeholders": self.doc.placeholders,
                "soft": self.doc.soft,
            },
            "rtt_slots": self.rtt_slots,
            "translated": self.translated,
            "meta": self.meta,
        }

    @classmethod
    def from_json(cls, d: dict) -> "Draft":
        doc = Document(
            d["doc"]["fmt"], d["doc"]["template"], [Slot(**s) for s in d["doc"]["slots"]], d["doc"]["placeholders"],
            d["doc"].get("soft", []),
        )
        return cls(d["draft_id"], d["created"], doc, d["rtt_slots"], d["translated"], d["meta"])


class DraftNotFound(KeyError):
    pass


class DraftStore:
    """内存 + 磁盘双写：MCP 常驻进程走内存，CLI 两步调用跨进程走磁盘。"""

    def __init__(self, directory: str | os.PathLike | None = None, ttl_s: float = 6 * 3600):
        self.dir = Path(directory) if directory else telemetry.home() / "drafts"
        self.ttl_s = ttl_s
        self._mem: dict[str, Draft] = {}
        self._lock = threading.Lock()

    def _path(self, draft_id: str) -> Path:
        if not draft_id.replace("-", "").isalnum():
            raise DraftNotFound(draft_id)
        return self.dir / f"{draft_id}.json"

    def put(self, draft: Draft) -> None:
        with self._lock:
            self._mem[draft.draft_id] = draft
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._path(draft.draft_id).write_text(json.dumps(draft.to_json(), ensure_ascii=False), "utf-8")
        except OSError:
            pass  # 磁盘不可写时仍可在同一进程内使用
        self.gc()

    def get(self, draft_id: str) -> Draft:
        with self._lock:
            if draft_id in self._mem:
                return self._mem[draft_id]
        p = self._path(draft_id)
        try:
            return Draft.from_json(json.loads(p.read_text("utf-8")))
        except (OSError, ValueError, KeyError, TypeError):
            raise DraftNotFound(draft_id) from None

    def delete(self, draft_id: str) -> None:
        with self._lock:
            self._mem.pop(draft_id, None)
        try:
            self._path(draft_id).unlink()
        except (OSError, DraftNotFound):
            pass

    def gc(self) -> None:
        cutoff = time.time() - self.ttl_s
        with self._lock:
            for k in [k for k, v in self._mem.items() if v.created < cutoff]:
                del self._mem[k]
        try:
            for p in self.dir.glob("*.json"):
                if p.stat().st_mtime < cutoff:
                    p.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# 结果
# ---------------------------------------------------------------------------


@dataclass
class PrepareResult:
    draft_id: str
    original: str
    rtt_draft: str
    radar_hints: str
    radar: dict
    audit_prompt: str
    fallback: bool
    fallback_reason: str | None
    stats: dict
    # 第二代宿主分诊握手：雷达扫出疑似电报句、本地又没有分诊缓存时，先请宿主判断这些句子是否出自技术/工程语境，
    # 再调用 Pipeline.complete_prepare(draft_id, domain_triage) 取回审校规则（往返翻译不重跑）。此时 audit_prompt 为空。
    needs_domain_triage: bool = False
    triage_prompt: str = ""
    triage_sentences: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FinalizeResult:
    draft_id: str
    final_text: str
    patches: list[dict]
    applied: int
    rejected: int
    fallback: bool
    fallback_reason: str | None
    cleanups: int = 0  # 定稿前确定性标点清理的处数

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# 编排
# ---------------------------------------------------------------------------

TranslatorFactory = Callable[["PipelineConfig"], Any]


def default_translator_factory(cfg: PipelineConfig) -> RoundTripTranslator:
    client = ChatClient(provider=cfg.provider, timeout=cfg.request_timeout_s, cache_dir=cfg.cache_dir)
    if not client.api_key:  # 整体回退，不必逐段失败
        raise OpenRouterError(f"{' / '.join(PROVIDERS[cfg.provider]['key_env'])} 未设置")
    return RoundTripTranslator(
        client,
        model=cfg.model,
        bridge=cfg.bridge,
        batch_chars=cfg.batch_chars,
        batch_segments=cfg.batch_segments,
        max_workers=cfg.max_workers,
        temperature=cfg.temperature,
        max_segment_chars=cfg.max_segment_chars,
        max_output_tokens=PROVIDERS[cfg.provider]["max_output_tokens"],
        source=cfg.source_lang,
    )


_MARKUP_STRIP_RE = re.compile(r"\*\*|~~|[*_`\[\]]")
_ASCII_WORD_RE = re.compile(r"[A-Za-z]{2,}")


def rtt_contexts(doc, translated: list[int], max_chars: int, question: str | None = None) -> list[str | None]:
    """每个待翻译槽位的中文上文：用户问题（截短）+ 紧邻的前文，供翻译模型按语境选词。

    只取本槽位之前的内容，不含本槽位自身，回程翻译不会因此照抄原句。
    """
    if max_chars <= 0:
        return [None] * len(translated)
    q = (question or "").strip()[:150]
    out: list[str | None] = []
    for i in translated:
        prev = "\n".join(s.original.strip() for s in doc.slots[:i] if s.original.strip())
        if len(prev) > max_chars:
            prev = prev[-max_chars:]
            cuts = [k for k in (prev.find(c) for c in "\n。！？；") if k >= 0]
            if cuts and min(cuts) < max_chars // 2:  # 去掉被截断的半句
                prev = prev[min(cuts) + 1:]
        ctx = "\n".join(x for x in (q, prev.strip()) if x)
        out.append(ctx or None)
    return out


def needs_rtt(slot: Slot, min_chars: int = 8) -> bool:
    """是否值得送去往返翻译：必须含中文；过短的纯中文槽位没有可解压的句法，送进去只会增加合批失败风险。"""
    if not slot.translatable:
        return False
    plain = _MARKUP_STRIP_RE.sub("", PLACEHOLDER_RE.sub("", slot.masked)).strip()
    return len(plain) >= min_chars or bool(_ASCII_WORD_RE.search(plain))


def _run_with_hard_timeout(fn: Callable[[], Any], timeout_s: float) -> Any:
    """在守护线程里执行 fn，超过 timeout_s 直接放弃等待（网络库重试也拖不住主流程）。"""
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["value"] = fn()
        except BaseException as e:  # noqa: BLE001
            box["error"] = e

    th = threading.Thread(target=target, daemon=True)
    th.start()
    th.join(timeout_s)
    if th.is_alive():
        raise TimeoutError(f"RTT 超出整体时限 {timeout_s:g}s")
    if "error" in box:
        raise box["error"]
    return box["value"]


def verify_final(doc: Document, slot_texts: list[str]) -> str:
    """定稿最终校验：每个槽位占位符恰好齐全，且所有冻结原文都出现在渲染结果里。返回失败原因或空串。"""
    if len(slot_texts) != len(doc.slots):
        return "槽位数量不一致"
    for i, (slot, text) in enumerate(zip(doc.slots, slot_texts)):
        chk = check_placeholders(text, slot.ids)
        if not chk.ok:
            return f"槽位 {i} 占位符校验失败 missing={chk.missing} extra={chk.extra} dup={chk.duplicated}"
    rendered = doc.render(slot_texts)
    for pid, val in enumerate(doc.placeholders):
        if pid not in doc.dropped and val not in rendered:
            return f"受保护内容丢失：{val[:40]}"
    if "\x00" in rendered:
        return "模板标记残留"
    return ""


_NO_TRIAGE = object()  # complete_prepare 之外调用 _finish_prepare 时的“未提供分诊结果”标记


class Pipeline:
    def __init__(
        self,
        config: PipelineConfig | None = None,
        translator_factory: TranslatorFactory | None = None,
        store: DraftStore | None = None,
        router: TelegraphRouter | None = None,
    ):
        self.config = config or PipelineConfig.from_env()
        self.translator_factory = translator_factory or default_translator_factory
        self.store = store or DraftStore(self.config.store_dir, self.config.draft_ttl_s)
        # 电报句分诊：评测脚本可显式注入同步分诊器（如 DeepSeek）；生产路径不注入，改由宿主模型握手分诊，
        # 绝不在 prepare() 里隐式连接外部 API
        self.router = router

    # -- 语言相关的钩子：中文版即默认实现；其他语言版本（no_ai_fluff.en）在子类里替换 --------------
    masker_cls: type = Masker

    def _parse(self, text: str, fmt: str) -> Document:
        return parse(text, fmt, self.config.extra_terms, self.masker_cls)

    def _needs_rtt(self, slot: Slot) -> bool:
        return needs_rtt(slot, self.config.min_rtt_chars)

    def _contexts(self, doc: Document, translated: list[int], question: str | None) -> list[str | None]:
        return rtt_contexts(doc, translated, self.config.rtt_context_chars, question)

    def _radar(self, orig_masked: list[str], rtt_slots: list[str], translated: list[int], rules: str):
        # 补丁打在原文上：雷达报原文自己的疑点；第二代加扫电报式短句群、不跑静态黑话词表
        return scan_single(orig_masked, dense=rules in DENSE_RADAR_RULES, cliche=rules not in NO_CLICHE_RADAR_RULES)

    def _radar_counts(self, slots: list[str]) -> dict:
        return scan_single(slots).counts

    def _audit_prompt(self, original: str, rtt_text: str, radar_text: str, rules: str, question: str | None) -> str:
        return build_audit_prompt(original, rtt_text, radar_text, rules, question)

    def _clean(self, text: str, reference: str) -> tuple[str, int]:
        return clean_punctuation(text, reference)

    # -- 握手 1 ---------------------------------------------------------------
    def prepare(
        self, text: str, fmt: str = "auto", exit: str = "terminal", mode: str = "auto", question: str | None = None
    ) -> PrepareResult:
        """question：可选，用户的原始问题，会附在审校提示里，帮宿主判断轻重和是否跑题。"""
        cfg = self.config
        t0 = time.monotonic()
        doc = self._parse(text, fmt)
        original_text = doc.render()  # 规整后的原文（CRLF -> LF、去掉 \x00）
        if mode == "auto":
            mode = "doc" if exit == "file" or doc.fmt == "html" or len(original_text) > cfg.doc_chars else "chat"
        timeout_s = cfg.doc_timeout_s if mode == "doc" else cfg.chat_timeout_s

        translated = [i for i, s in enumerate(doc.slots) if self._needs_rtt(s)]
        rtt_slots = [s.masked for s in doc.slots]
        fallback_reason: str | None = None
        rtt: RTTResult | None = None
        if translated:
            try:
                translator = self.translator_factory(cfg)
                segs = [doc.slots[i].masked for i in translated]
                ids = [doc.slots[i].ids for i in translated]
                kw = {}
                if "contexts" in inspect.signature(translator.round_trip).parameters:
                    kw["contexts"] = self._contexts(doc, translated, question)
                rtt = _run_with_hard_timeout(
                    lambda: translator.round_trip(segs, ids, timeout_s=timeout_s, **kw), timeout_s + 1.0
                )
                for i, t in zip(translated, rtt.texts):
                    if check_placeholders(t, doc.slots[i].ids).ok:
                        rtt_slots[i] = t
            except TimeoutError as e:
                fallback_reason = f"timeout: {e}"
            except Exception as e:  # noqa: BLE001 —— 任何异常都无损回退到原文
                fallback_reason = f"{type(e).__name__}: {e}"

        draft_id = time.strftime("%Y%m%d%H%M%S") + "-" + secrets.token_hex(4)
        meta = {
            "original_text": original_text,
            "fmt": doc.fmt,
            "exit": exit,
            "mode": mode,
            "provider": cfg.provider,
            "model": cfg.model,
            "bridge": cfg.bridge,
            "audit_rules": self._resolve_rules(cfg.audit_rules),
            "fallback": fallback_reason is not None,
            "fallback_reason": fallback_reason,
            "question": question,
        }
        stats = {
            "fmt": doc.fmt,
            "mode": mode,
            "provider": cfg.provider,
            "model": cfg.model,
            "bridge": cfg.bridge,
            "slots": len(doc.slots),
            "translated_slots": len(translated),
            "placeholders": len(doc.placeholders),
            "segment_fallbacks": len(rtt.fallbacks) if rtt else 0,
            "rtt_calls": rtt.calls if rtt else 0,
            "rtt_latency_s": round(rtt.latency_s, 3) if rtt else None,
            "cost_usd": rtt.cost_usd if rtt else 0.0,
            "elapsed_s": round(time.monotonic() - t0, 3),
        }
        meta["stats"] = stats
        draft = Draft(draft_id, time.time(), doc, rtt_slots, translated, meta)
        self.store.put(draft)  # 先落盘：需要宿主分诊时，第二步从这里接着走，不重跑往返翻译
        return self._finish_prepare(draft)

    def complete_prepare(self, draft_id: str, domain_triage: Any = None) -> PrepareResult:
        """宿主分诊握手第二步：带上宿主对候选电报句的判断，取回审校规则。往返翻译不重跑。

        domain_triage 可以是单个布尔、布尔数组、[{"id": 1, "technical": true}] 或其 JSON 字符串；
        解析不了的句子按 true（技术语境，走强化通道）保守处理。
        """
        return self._finish_prepare(self.store.get(draft_id), domain_triage)

    def _is_routed(self, rules: str) -> bool:
        return rules in ROUTED_RULES

    def _route_report(self, report, masked_slots: list[str], placeholders: list[str], router: TelegraphRouter):
        return route_report(report, masked_slots, placeholders, router)

    def _route(self, rules: str) -> TelegraphRouter:
        want = ROUTER_SPECS.get(rules)
        if self.router is not None and (want is None or self.router.spec is want):
            return self.router  # 评测脚本显式注入的同步分诊器
        return host_router(rules)  # 生产路径：只读写本地缓存，缺的判定交给宿主模型

    def _finish_prepare(self, draft: "Draft", domain_triage: Any = _NO_TRIAGE) -> PrepareResult:
        cfg = self.config
        meta = draft.meta
        rules = self._resolve_rules(meta.get("audit_rules") or cfg.audit_rules)
        question = meta.get("question")
        stats = dict(meta.get("stats") or {})
        orig_masked = [s.masked for s in draft.doc.slots]
        report = self._radar(orig_masked, draft.rtt_slots, draft.translated, rules)
        rtt_text = draft.rtt_text()
        fallback_reason = meta.get("fallback_reason")
        if self._is_routed(rules) and any(h.kind == "telegraph" for h in report.hints):
            router = self._route(rules)
            rr = self._route_report(report, orig_masked, draft.doc.placeholders, router)
            if rr.needs_host and domain_triage is not _NO_TRIAGE:
                flags = parse_host_triage(domain_triage, len(rr.pending), router.spec.field)
                known = [(x, f) for x, f in zip(rr.pending, flags) if f is not None]
                router.record([x for x, _ in known], [f for _, f in known])
                rr = self._route_report(report, orig_masked, draft.doc.placeholders, router)
                stats["domain_triage"] = "host"
            elif rr.needs_host:
                return PrepareResult(
                    draft_id=draft.draft_id, original=draft.original_text, rtt_draft=rtt_text,
                    radar_hints=report.to_prompt(), radar=report.to_dict(), audit_prompt="",
                    fallback=fallback_reason is not None, fallback_reason=fallback_reason, stats=stats,
                    needs_domain_triage=True, triage_prompt=router.prompt_for(rr.pending),
                    triage_sentences=rr.pending,
                )
            else:
                stats["domain_triage"] = "cache" if not rr.calls else "router"
            # 宿主给不出可解析的判定时，待定句按 true 处理（保守：整篇走强化通道）
            report = rr.report
            stats["domain_dense"] = rr.dense
        if cfg.telemetry:
            telemetry.log_event({
                "event": "prepare",
                "draft_id": draft.draft_id,
                "exit": meta.get("exit"),
                "fallback": fallback_reason is not None,
                "fallback_reason": (fallback_reason or "").split(":", 1)[0] or None,
                "radar_counts": report.counts,
                "chars": len(draft.original_text),
                **stats,
            })
        return PrepareResult(
            draft_id=draft.draft_id,
            original=draft.original_text,
            rtt_draft=rtt_text,
            radar_hints=report.to_prompt(),
            radar=report.to_dict(),
            audit_prompt=self._audit_prompt(draft.original_text, rtt_text, report.to_prompt(), rules, question),
            fallback=fallback_reason is not None,
            fallback_reason=fallback_reason,
            stats=stats,
        )

    # -- 握手 2 ---------------------------------------------------------------
    def _resolve_rules(self, rules: str) -> str:
        return resolve_rules(rules)

    def _apply(self, draft: "Draft", plist: list, rules: str, rejected: dict[int, str] | None = None):
        base = [s.masked for s in draft.doc.slots]  # 补丁打在原文上（往返稿只作参考）
        return apply_patches(draft.doc, base, plist, MAX_FIND_BY_RULES.get(rules, MAX_FIND_CHARS), rejected,
                             PROBLEM_TYPES.get(rules))

    def finalize(
        self, draft_id: str, patches: Any = None, keep_draft: bool = False, audit_rules: str | None = None,
    ) -> FinalizeResult:
        """audit_rules 决定补丁允许的改写范围；默认取 prepare 时记录的规则版本。
        """
        draft = self.store.get(draft_id)
        rules = self._resolve_rules(audit_rules or draft.meta.get("audit_rules") or self.config.audit_rules)
        doc = draft.doc
        format_error: str | None = None
        try:
            plist = parse_patches(patches if patches is not None else [])
        except PatchFormatError as e:
            plist, format_error = [], f"补丁格式错误，已忽略全部补丁：{e}"

        new_slots, results = self._apply(draft, plist, rules)
        cleanups = 0
        for i, slot in enumerate(doc.slots):  # 只清理被改写过的槽位（往返翻译或补丁）；原样未动的不碰
            if new_slots[i] != slot.masked:
                new_slots[i], n = self._clean(new_slots[i], slot.masked)
                cleanups += n
                if rules in CJK_SPACE_RULES:  # 第二代：删掉夹杂英文后，两个汉字之间留下的多余空格
                    new_slots[i], n = tidy_cjk_spaces(new_slots[i], slot.masked)
                    cleanups += n
        reason = verify_final(doc, new_slots)
        if reason:
            final_text, fallback_reason = draft.original_text, f"verify: {reason}"
        else:
            final_text, fallback_reason = doc.render(new_slots), None
        applied = sum(1 for r in results if r.applied)
        rejected = len(results) - applied

        if self.config.telemetry:
            only = draft.translated
            telemetry.log_event({
                "event": "finalize",
                "draft_id": draft_id,
                "exit": draft.meta.get("exit"),
                "model": draft.meta.get("model"),
                "bridge": draft.meta.get("bridge"),
                "patches_received": len(plist),
                "patches_applied": applied,
                "patches_rejected": rejected,
                "patch_format_error": format_error is not None,
                "punctuation_cleanups": cleanups,
                "fallback": fallback_reason is not None,
                "radar_original": self._radar_counts([doc.slots[i].masked for i in only]),
                "radar_rtt": self._radar_counts([draft.rtt_slots[i] for i in only]),
                "radar_final": self._radar_counts([new_slots[i] for i in only]),
            })
        if not keep_draft:
            self.store.delete(draft_id)
        return FinalizeResult(
            draft_id=draft_id,
            final_text=final_text,
            patches=results_to_dicts(results),
            applied=applied,
            rejected=rejected,
            fallback=fallback_reason is not None,
            fallback_reason=fallback_reason or format_error,
            cleanups=cleanups,
        )

    # -- 便捷：单进程一次跑完（DeepSeek Harness / 评测台） --------------------
    def run(
        self,
        text: str,
        reviewer: Callable[[str], Any] | None = None,
        fmt: str = "auto",
        exit: str = "terminal",
        mode: str = "auto",
        question: str | None = None,
    ) -> tuple[PrepareResult, FinalizeResult]:
        """reviewer(audit_prompt) -> JSON 补丁（字符串或列表）。不传 reviewer 时等价于纯 RTT。

        需要宿主分诊时，同一个 reviewer 先回答分诊提示（triage_prompt），再回答审校提示。
        """
        prep = self.prepare(text, fmt=fmt, exit=exit, mode=mode, question=question)
        patches: Any = []
        reviewer_error: str | None = None
        triage_error: str | None = None
        if prep.needs_domain_triage:
            triage: Any = None
            if reviewer is not None:
                try:
                    triage = reviewer(prep.triage_prompt)
                except Exception as e:  # noqa: BLE001 —— 分诊失败按技术语境保守处理，照常审校，但如实记录
                    triage_error = f"domain triage error: {type(e).__name__}: {e}"
            prep = self.complete_prepare(prep.draft_id, triage)
        if reviewer is not None and not reviewer_error:
            try:
                patches = reviewer(prep.audit_prompt)
            except Exception as e:  # noqa: BLE001 —— 审校失败按空补丁出初稿，但必须如实告知调用方
                patches, reviewer_error = [], f"reviewer error: {type(e).__name__}: {e}"
        fin = self.finalize(prep.draft_id, patches)
        reviewer_error = "; ".join(x for x in (triage_error, reviewer_error) if x) or None
        if reviewer_error:
            fin.fallback_reason = "; ".join(x for x in (reviewer_error, fin.fallback_reason) if x)
            if self.config.telemetry:
                telemetry.log_event({"event": "reviewer_error", "draft_id": prep.draft_id,
                                     "error": reviewer_error.split(":", 2)[1].strip()})
        return prep, fin
