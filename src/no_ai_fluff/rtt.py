"""Stage 1：OpenRouter 原生小模型往返解压层（RTT）。

- 只用标准库 urllib 调 OpenRouter 的 OpenAI 兼容接口；温度 0.1，纯翻译提示词，不注入文风指令。
- 多个槽位用 ⟦0⟧、⟦1⟧ 段落分界符合并成一批，保留跨句指代上下文；
  分界符数量对不上时自动降级为逐段单独翻译。
- 每段回译结果都做占位符与行内标记校验，失败的段落单段重试一次，仍失败则退回原文（段级熔断）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from pathlib import Path

from .masker import _CJK_RE, PLACEHOLDER_RE, check_placeholders, markup_signature, normalize_placeholders

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
TENCENT_URL = "https://tokenhub.tencentmaas.com/v1/chat/completions"
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"

# 各服务商的接入参数。chunk_chars / batch_segments 按“输入 + 输出 ≤ 上下文”留足余量：
# 腾讯云 hy-mt2-pro 上下文 8K、输出上限 4K，实测约 1.9 个汉字 ≈ 1 token，
# 超长输入不会报错而是静默截断（finish_reason=length），所以单次输入控制在约 1200 字。
PROVIDERS: dict[str, dict] = {
    "openrouter": {
        "url": OPENROUTER_URL, "key_env": ("OPENROUTER_API_KEY",), "default_model": "tencent/hy-mt2-30b-a3b",
        "extra": {"usage": {"include": True}}, "batch_chars": 400, "batch_segments": 6, "max_workers": 8,
        "max_segment_chars": 1500, "max_output_tokens": 4000,
    },
    "tencent": {
        "url": TENCENT_URL, "key_env": ("TENCENTCLOUD_API_KEY", "TENCENT_API_KEY"), "default_model": "hy-mt2-pro",
        "extra": None, "batch_chars": 1200, "batch_segments": 8, "max_workers": 4,
        "max_segment_chars": 1200, "max_output_tokens": 4000,
    },
    "deepseek": {
        "url": DEEPSEEK_URL, "key_env": ("DEEPSEEK_API_KEY", "Deepseek_OFFICIAL_API_KEY", "DEEPSEEK_OFFICIAL_API_KEY"),
        "default_model": "deepseek-flash", "extra": None, "batch_chars": 400, "batch_segments": 6,
        "max_workers": 4, "max_segment_chars": 1500, "max_output_tokens": 8000,
    },
}

LANG_NAMES_ZH = {"zh": "中文", "fr": "法语", "ja": "日语", "ru": "俄语", "en": "英语", "de": "德语", "es": "西班牙语"}

DEFAULT_MODEL = "tencent/hy-mt2-30b-a3b"
DEFAULT_BRIDGE = "ru"  # M3 锁定：人工盲测俄语 5/8 胜出（p≈0.027），LLM 复核保真度与法语持平
SUPPORTED_MODELS = ("tencent/hy-mt2-30b-a3b", "google/gemma-3-4b-it")
SUPPORTED_BRIDGES = ("fr", "ja", "ru")


class OpenRouterError(RuntimeError):
    pass


class ContentFilteredError(OpenRouterError):
    """上游服务商对这段输入触发了内容过滤：确定性行为，重试无意义。"""


class TruncatedOutputError(OpenRouterError):
    """输出因长度上限被截断（finish_reason=length）：同样的输入重试无意义，应拆小。"""


@dataclass
class ChatResult:
    text: str
    latency_s: float
    usage: dict = field(default_factory=dict)
    cached: bool = False


def provider_key(provider: str) -> str:
    for name in PROVIDERS[provider]["key_env"]:
        if os.environ.get(name, "").strip():
            return os.environ[name].strip()  # .strip()：Windows 编辑的 .env 可能带 \r
    return ""


class ChatClient:
    """极简 OpenAI 兼容客户端（零依赖），带重试与可选的磁盘缓存（评测复现用）。

    provider 决定默认地址、Key 环境变量和请求附加字段；url / api_key 可显式覆盖（如本地模型）。
    """

    def __init__(
        self,
        api_key: str | None = None,
        url: str | None = None,
        timeout: float = 30.0,
        max_retries: int = 2,
        cache_dir: str | os.PathLike | None = None,
        provider: str = "openrouter",
    ):
        if provider not in PROVIDERS:
            raise ValueError(f"未知服务商 {provider}，可选：{', '.join(PROVIDERS)}")
        self.provider = provider
        spec = PROVIDERS[provider]
        self.api_key = (api_key or provider_key(provider)).strip()
        self.url = url or spec["url"]
        self.default_extra = spec["extra"] if url in (None, spec["url"]) else None
        self.timeout = timeout
        self.max_retries = max_retries
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, payload: dict) -> Path | None:
        if not self.cache_dir:
            return None
        key = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        return self.cache_dir / f"{key[:2]}" / f"{key}.json"

    def chat(
        self,
        model: str,
        messages: list[dict],
        temperature: float = 0.1,
        max_tokens: int | None = None,
        timeout: float | None = None,
        extra: dict | None = None,
        max_retries: int | None = None,
    ) -> ChatResult:
        if not self.api_key:
            raise OpenRouterError(f"{' / '.join(PROVIDERS[self.provider]['key_env'])} 未设置")
        payload: dict = {"model": model, "messages": messages, "temperature": temperature}
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if extra:
            payload.update(extra)
        cpath = self._cache_path(payload)
        if cpath and cpath.exists():
            data = json.loads(cpath.read_text("utf-8"))
            return ChatResult(data["text"], data.get("latency_s", 0.0), data.get("usage", {}), cached=True)

        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/ConstantlyGrowup/no-ai-fluff",
            "X-Title": "no-ai-fluff",
        }
        last_err: Exception | None = None
        retries = self.max_retries if max_retries is None else max_retries
        for attempt in range(retries + 1):
            t0 = time.monotonic()
            try:
                req = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                latency = time.monotonic() - t0
                if "error" in data:
                    raise OpenRouterError(str(data["error"]))
                choice = (data.get("choices") or [{}])[0]
                content = choice.get("message", {}).get("content")
                if choice.get("finish_reason") == "content_filter" or choice.get("native_finish_reason") == "content_filter":
                    raise ContentFilteredError("上游内容过滤（content_filter）")
                if choice.get("finish_reason") == "length":
                    raise TruncatedOutputError("输出被长度上限截断（finish_reason=length）")
                if not content:
                    raise OpenRouterError("空响应")
                result = ChatResult(content, latency, data.get("usage") or {})
                if cpath:
                    cpath.parent.mkdir(parents=True, exist_ok=True)
                    cpath.write_text(
                        json.dumps({"text": content, "latency_s": latency, "usage": result.usage}, ensure_ascii=False),
                        "utf-8",
                    )
                return result
            except (ContentFilteredError, TruncatedOutputError):
                raise
            except urllib.error.HTTPError as e:
                err_body = e.read()
                last_err = OpenRouterError(f"HTTP {e.code}: {err_body[:300]!r}")
                # OpenRouter 在账户并发请求的预扣额度超出余额时返回 402 + in_flight，
                # 属于暂时性限制，等在途请求结算后重试即可；其余 402（余额真的不足）不重试
                transient_402 = e.code == 402 and b"in_flight" in err_body
                if e.code not in (408, 409, 425, 429, 500, 502, 503, 504) and not transient_402:
                    break
            except (urllib.error.URLError, TimeoutError, OSError, OpenRouterError, json.JSONDecodeError) as e:
                last_err = e
            if attempt < retries:
                time.sleep(0.6 * (2**attempt))
        raise OpenRouterError(f"{self.provider} 请求失败：{last_err}")


class OpenRouterClient(ChatClient):
    """向后兼容：默认服务商为 OpenRouter 的 ChatClient。"""

    def __init__(self, api_key: str | None = None, url: str = OPENROUTER_URL, **kw):
        kw.setdefault("provider", "openrouter")
        super().__init__(api_key=api_key, url=url, **kw)


def make_client(provider: str = "openrouter", **kw) -> ChatClient:
    return ChatClient(provider=provider, **kw)


# ---------------------------------------------------------------------------
# 超长段落切分
# ---------------------------------------------------------------------------

_SENT_END_RE = re.compile(r"(?<=[。！？!?；;\n])")
_SOFT_END_RE = re.compile(r"(?<=[，,、：:])")


def split_long(text: str, max_chars: int) -> list[str]:
    """把超长段落按句末标点切成不超过 max_chars 的若干块（尽量不切断句子，绝不切断 {{Pn}}）。

    拼接各块（"".join）严格等于原文。
    """
    if len(text) <= max_chars:
        return [text]

    def pack(units: list[str]) -> list[str]:
        out, cur = [], ""
        for u in units:
            if cur and len(cur) + len(u) > max_chars:
                out.append(cur)
                cur = ""
            cur += u
        if cur:
            out.append(cur)
        return out

    pieces: list[str] = []
    for sent in [x for x in _SENT_END_RE.split(text) if x]:
        if len(sent) <= max_chars:
            pieces.append(sent)
            continue
        for clause in [x for x in _SOFT_END_RE.split(sent) if x]:
            if len(clause) <= max_chars:
                pieces.append(clause)
                continue
            # 没有标点可断：按长度硬切，但不切进占位符内部
            i = 0
            while i < len(clause):
                j = min(len(clause), i + max_chars)
                for m in PLACEHOLDER_RE.finditer(clause):
                    if m.start() < j < m.end():
                        j = m.start() if m.start() > i else m.end()
                pieces.append(clause[i:j])
                i = j
    return pack(pieces)


# ---------------------------------------------------------------------------
# 往返翻译
# ---------------------------------------------------------------------------

_MARK = "⟦{}⟧"
_MARK_RE = re.compile(r"⟦\s*(\d+)\s*⟧")


def build_prompt(text: str, target: str, batched: bool = False) -> str:
    """纯翻译提示词：逐字沿用混元 MT 官方中外互译模板，不含任何文风要求。

    实测（2026-11，hy-mt2-30b-a3b）：在模板里追加“保留 ⟦数字⟧ 标记、逐行对应”之类的规则说明，
    约 30% 的批次会返回“生成了 token 但 content 为空”；只用官方模板时空响应为 0，
    分界符与 {{Pn}} 占位符的保留率反而更高。分界符是否对齐、占位符是否齐全统一由代码校验兜底。
    batched 参数保留只为兼容调用方，不影响提示词。
    """
    return f"将以下文本翻译为{LANG_NAMES_ZH.get(target, target)}，注意只需要输出翻译后的结果，不要额外解释：\n\n{text}"


def _cjk_len(s: str) -> int:
    return len(PLACEHOLDER_RE.sub("", s))


def split_batched(output: str, n: int) -> list[str] | None:
    """解析带 ⟦i⟧ 分界符的批量输出；数量不一致返回 None。容忍第 0 个分界符被模型吞掉。"""
    parts = _MARK_RE.split(output)
    head = parts[0].strip()
    found: dict[int, str] = {}
    for k in range(1, len(parts) - 1, 2):
        idx = int(parts[k])
        if idx in found:
            return None
        found[idx] = parts[k + 1].strip()
    if head:
        if 0 in found:
            return None
        found[0] = head
    if sorted(found) != list(range(n)):
        return None
    return [found[i] for i in range(n)]


def _strip_wrapping(s: str) -> str:
    s = s.strip()
    if s.startswith("```") and s.endswith("```"):
        s = re.sub(r"^```[^\n]*\n?|```$", "", s).strip()
    return s


@dataclass
class SegmentOutcome:
    text: str  # 回译后的掩码文本（失败时为原文）
    bridge: str  # 桥接语中间稿
    ok: bool
    reason: str = ""


@dataclass
class RTTResult:
    texts: list[str]
    bridges: list[str]
    fallbacks: list[int]  # 退回原文的段落下标
    reasons: dict[int, str]
    calls: int
    latency_s: float
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0


class _Failed:
    __slots__ = ("reason",)

    def __init__(self, reason: str):
        self.reason = reason


def _parallel(fns: list) -> list:
    """并行执行若干无参函数，按顺序返回结果；任一抛异常则向上抛出。"""
    if not fns:
        return []
    if len(fns) == 1:
        return [fns[0]()]
    pool = ThreadPoolExecutor(max_workers=min(8, len(fns)))
    try:
        futs = [pool.submit(f) for f in fns]
        return [f.result() for f in futs]
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


class Deadline:
    def __init__(self, seconds: float | None):
        self.end = time.monotonic() + seconds if seconds else None

    def remaining(self) -> float | None:
        if self.end is None:
            return None
        r = self.end - time.monotonic()
        if r <= 0:
            raise TimeoutError("RTT 超出整体时限")
        return r


class RoundTripTranslator:
    def __init__(
        self,
        client: ChatClient,
        model: str = DEFAULT_MODEL,
        bridge: str = DEFAULT_BRIDGE,
        source: str = "zh",
        batch_chars: int = 400,
        batch_segments: int = 6,
        max_workers: int = 8,
        temperature: float = 0.1,
        max_segment_chars: int = 1500,
        max_output_tokens: int = 4000,
    ):
        self.client = client
        self.model = model
        self.bridge = bridge
        self.source = source
        self.batch_chars = batch_chars
        self.batch_segments = batch_segments  # 0 表示不限段数
        self.max_segment_chars = max_segment_chars
        self.max_output_tokens = max_output_tokens
        self.max_workers = max_workers
        self.temperature = temperature
        self._calls = 0
        self._ptok = 0
        self._ctok = 0
        self._cost = 0.0
        self._lock = threading.Lock()

    # -- 底层单次调用 ---------------------------------------------------------
    def _call(self, text: str, target: str, batched: bool, deadline: Deadline) -> str:
        rem = deadline.remaining()
        timeout = min(self.client.timeout, rem) if rem else self.client.timeout
        kwargs = {}
        if batched:
            kwargs["max_retries"] = 0  # 合批失败直接二分拆小，比原地重试快得多
        res = self.client.chat(
            self.model,
            [{"role": "user", "content": build_prompt(text, target, batched)}],
            temperature=self.temperature,
            max_tokens=min(self.max_output_tokens, 200 + len(text) * 4),
            timeout=timeout,
            extra=getattr(self.client, "default_extra", {"usage": {"include": True}}),
            **kwargs,
        )
        u = res.usage or {}
        with self._lock:
            self._calls += 1
            self._ptok += int(u.get("prompt_tokens") or 0)
            self._ctok += int(u.get("completion_tokens") or 0)
            self._cost += float(u.get("cost") or 0.0)
        return normalize_placeholders(_strip_wrapping(res.text))

    def _call_safe(self, text: str, target: str, deadline: Deadline) -> "str | _Failed":
        """单段调用：失败（内容过滤 / 接口错误）只影响这一段，超时照常向上抛。"""
        try:
            return self._call(text, target, False, deadline)
        except TimeoutError:
            raise
        except Exception as e:  # noqa: BLE001
            return _Failed(f"{type(e).__name__}: {e}")

    def _translate_batch(self, segs: list[str], target: str, deadline: Deadline) -> list:
        """返回与 segs 等长的列表，元素为译文或 _Failed。"""
        if len(segs) == 1:
            return [self._call_safe(segs[0], target, deadline)]
        joined = "\n".join(f"{_MARK.format(i)} {s}" for i, s in enumerate(segs))
        try:
            out = split_batched(self._call(joined, target, True, deadline), len(segs))
        except TimeoutError:
            raise
        except Exception:  # noqa: BLE001 —— 整批被过滤或出错：拆小重试，把问题收敛到具体段落
            out = None
        if out is not None:
            return out
        # 分界符对不上（常见于模型把相邻短行合并）：少量段落直接并行逐段翻译，
        # 否则二分成两半并行重试合批，调用次数随失败深度对数增长，而不是逐段串行
        if len(segs) <= 3:
            return _parallel([lambda s=s: self._call_safe(s, target, deadline) for s in segs])
        mid = len(segs) // 2
        left, right = _parallel([
            lambda: self._translate_batch(segs[:mid], target, deadline),
            lambda: self._translate_batch(segs[mid:], target, deadline),
        ])
        return left + right

    # -- 段校验 ---------------------------------------------------------------
    @staticmethod
    def validate(src: str, back: str, ids: list[int]) -> str:
        """返回失败原因；空串表示通过。"""
        chk = check_placeholders(back, ids)
        if not chk.ok:
            return f"placeholder missing={chk.missing} extra={chk.extra} dup={chk.duplicated}"
        if markup_signature(src) != markup_signature(back):
            return "inline markup changed"
        if not _CJK_RE.search(back):
            return "back-translation not Chinese"
        ls, lb = _cjk_len(src), _cjk_len(back)
        if ls >= 8 and not (0.35 <= lb / ls <= 2.8):
            return f"length ratio {lb / ls:.2f} out of range"
        return ""

    def _round_trip_batch(self, segs: list[str], ids: list[list[int]], deadline: Deadline) -> list[SegmentOutcome]:
        fwd = self._translate_batch(segs, self.bridge, deadline)
        ok_idx = [k for k, f in enumerate(fwd) if not isinstance(f, _Failed)]
        back: list = [None] * len(segs)
        if ok_idx:
            for k, b in zip(ok_idx, self._translate_batch([fwd[k] for k in ok_idx], self.source, deadline)):
                back[k] = b
        reasons: list[str] = []
        for k, (s, i) in enumerate(zip(segs, ids)):
            failed = fwd[k] if isinstance(fwd[k], _Failed) else back[k] if isinstance(back[k], _Failed) else None
            reasons.append(f"translate failed: {failed.reason}" if failed else self.validate(s, back[k], i))
        fwd = [f if isinstance(f, str) else "" for f in fwd]
        back = [b if isinstance(b, str) else "" for b in back]

        def retry(k: int) -> SegmentOutcome:
            s, i = segs[k], ids[k]
            try:
                f2 = self._call(s, self.bridge, False, deadline)
                b2 = self._call(f2, self.source, False, deadline)
                r2 = self.validate(s, b2, i)
                if not r2:
                    return SegmentOutcome(b2, f2, True)
                return SegmentOutcome(s, f2, False, f"{reasons[k]}; retry {r2}")
            except TimeoutError:
                raise
            except Exception as e:  # noqa: BLE001
                return SegmentOutcome(s, fwd[k], False, f"{reasons[k]}; retry error {e}")

        # 合批结果里校验失败的段落：并行单段重试一次，仍失败则退回原文
        # 内容过滤是确定性的，不重试；其余校验失败的段落单段重试一次
        failed = [k for k, r in enumerate(reasons) if r and len(segs) > 1 and "ContentFiltered" not in r]
        retried = dict(zip(failed, _parallel([lambda k=k: retry(k) for k in failed])))
        outcomes: list[SegmentOutcome] = []
        for k, (s, f, b) in enumerate(zip(segs, fwd, back)):
            if k in retried:
                outcomes.append(retried[k])
            else:
                outcomes.append(SegmentOutcome(b if not reasons[k] else s, f, not reasons[k], reasons[k]))
        return outcomes

    def _batches(self, segs: list[str]) -> list[list[int]]:
        batches: list[list[int]] = []
        cur: list[int] = []
        size = 0
        for i, s in enumerate(segs):
            if cur and (size + len(s) > self.batch_chars or (self.batch_segments and len(cur) >= self.batch_segments)):
                batches.append(cur)
                cur, size = [], 0
            cur.append(i)
            size += len(s)
        if cur:
            batches.append(cur)
        return batches

    def round_trip(self, segs: list[str], ids: list[list[int]], timeout_s: float | None = None) -> RTTResult:
        """对掩码后的槽位文本做 中 -> 桥接语 -> 中 往返翻译。

        超过 max_segment_chars 的段落先按句切块分别翻译，再拼回原段；任一块失败只回退该块。
        """
        pieces: list[str] = []
        owner: list[int] = []
        for i, s in enumerate(segs):
            for piece in split_long(re.sub(r"\s*\n\s*", " ", s), self.max_segment_chars):
                pieces.append(piece)
                owner.append(i)
        if len(pieces) == len(segs):
            return self._round_trip(segs, ids, timeout_s)
        piece_ids = [[int(x) for x in PLACEHOLDER_RE.findall(p)] for p in pieces]
        res = self._round_trip(pieces, piece_ids, timeout_s)
        texts = ["" for _ in segs]
        bridges = ["" for _ in segs]
        reasons: dict[int, str] = {}
        for k, i in enumerate(owner):
            texts[i] += res.texts[k]
            bridges[i] = (bridges[i] + " " + res.bridges[k]).strip()
            if k in res.reasons:
                reasons[i] = (reasons.get(i, "") + f"; piece: {res.reasons[k]}").lstrip("; ")
        # 有任一块回退的段落记为段级回退（其余块已正常解压）
        res.texts, res.bridges, res.reasons = texts, bridges, reasons
        res.fallbacks = sorted(reasons)
        return res

    def _round_trip(self, segs: list[str], ids: list[list[int]], timeout_s: float | None = None) -> RTTResult:
        t0 = time.monotonic()
        deadline = Deadline(timeout_s)
        self._calls = self._ptok = self._ctok = 0
        self._cost = 0.0
        # HTML 文本节点内部可能有软换行，翻译前折叠成空格，避免和分界符的逐行对应冲突
        clean = [re.sub(r"\s*\n\s*", " ", s) for s in segs]
        texts = list(segs)
        bridges = [""] * len(segs)
        fallbacks: list[int] = []
        reasons: dict[int, str] = {}
        batches = self._batches(clean)
        # 不用 with：上下文管理器退出时会等待所有线程结束，整体时限就失效了
        pool = ThreadPoolExecutor(max_workers=max(1, self.max_workers))
        try:
            futs = [
                (b, pool.submit(self._round_trip_batch, [clean[i] for i in b], [ids[i] for i in b], deadline))
                for b in batches
            ]
            for b, fut in futs:
                rem = deadline.remaining()
                try:
                    outcomes = fut.result(timeout=rem)
                except FutureTimeout:
                    raise TimeoutError("RTT 超出整体时限") from None
                except TimeoutError:
                    raise
                except Exception as e:  # noqa: BLE001 —— 单批意外错误只让这一批退回原文
                    outcomes = [SegmentOutcome(clean[i], "", False, f"batch error {type(e).__name__}: {e}") for i in b]
                for i, oc in zip(b, outcomes):
                    bridges[i] = oc.bridge
                    if oc.ok:
                        texts[i] = oc.text
                    else:
                        fallbacks.append(i)
                        reasons[i] = oc.reason
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        return RTTResult(
            texts, bridges, sorted(fallbacks), reasons, self._calls, time.monotonic() - t0,
            self._ptok, self._ctok, self._cost,
        )
