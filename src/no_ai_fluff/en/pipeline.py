"""English pipeline: the shared two-handshake engine with English hooks.

Configuration (environment or .env), all optional:
    NO_AI_FLUFF_EN_PROVIDER   openrouter (default) | tencent
    NO_AI_FLUFF_EN_MODEL      default: provider's default (OpenRouter: tencent/hy-mt2-30b-a3b; Tencent Cloud: hy-mt2-pro)
    NO_AI_FLUFF_EN_BRIDGE     ja (default)
    NO_AI_FLUFF_EN_RULES      en5 = v2 (default: structure-only radar + domain-triage handshake); en2 = v1 (single channel)
If NO_AI_FLUFF_EN_PROVIDER is unset, OpenRouter is used when OPENROUTER_API_KEY is set, otherwise Tencent Cloud when
its key is set. The Chinese edition's NO_AI_FLUFF_* settings are not read, so both editions can be installed side by side.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from .. import telemetry
from ..config import env_float, load_dotenv
from ..masker import PLACEHOLDER_RE, Document, Slot
from ..patcher import apply_patches
from ..pipeline import DraftStore, Pipeline, PipelineConfig
from ..telegraph_router import TelegraphRouter, route_report
from ..rtt import PROVIDERS, ChatClient, OpenRouterError, default_provider
from .cleanup import clean_punctuation_en
from .masker import EnMasker
from .radar import scan, telegraph_sentences
from .rtt import EnRoundTripTranslator
from .rules import (DEFAULT_RULES, DENSE_RADAR_RULES, MAX_FIND, NO_JARGON_RADAR_RULES, PROBLEM_TYPES, ROUTED_RULES,
                    build_audit_prompt, resolve_rules)

_WORDS = re.compile(r"[A-Za-z]{2,}")


@dataclass
class EnPipelineConfig(PipelineConfig):
    provider: str = "openrouter"
    bridge: str = "ja"
    audit_rules: str = DEFAULT_RULES
    source_lang: str = "en"
    min_rtt_chars: int = 4  # minimum number of words for a slot to be worth a round trip

    @classmethod
    def from_env(cls, **overrides: Any) -> "EnPipelineConfig":
        load_dotenv()
        values: dict[str, Any] = dict(
            provider=os.environ.get("NO_AI_FLUFF_EN_PROVIDER") or default_provider(),
            model=os.environ.get("NO_AI_FLUFF_EN_MODEL") or None,
            bridge=os.environ.get("NO_AI_FLUFF_EN_BRIDGE") or "ja",
            chat_timeout_s=env_float("NO_AI_FLUFF_EN_CHAT_TIMEOUT", 10.0),
            doc_timeout_s=env_float("NO_AI_FLUFF_EN_DOC_TIMEOUT", 60.0),
            cache_dir=os.environ.get("NO_AI_FLUFF_EN_CACHE_DIR") or None,
            audit_rules=os.environ.get("NO_AI_FLUFF_EN_RULES") or DEFAULT_RULES,
            store_dir=str(telemetry.home() / "drafts-en"),
        )
        values.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**values)


def en_translator_factory(cfg: PipelineConfig) -> EnRoundTripTranslator:
    client = ChatClient(provider=cfg.provider, timeout=cfg.request_timeout_s, cache_dir=cfg.cache_dir)
    if not client.api_key:
        raise OpenRouterError(f"{' / '.join(PROVIDERS[cfg.provider]['key_env'])} is not set")
    return EnRoundTripTranslator(
        client, model=cfg.model, bridge=cfg.bridge, source="en", batch_chars=cfg.batch_chars,
        batch_segments=cfg.batch_segments, max_workers=cfg.max_workers, temperature=cfg.temperature,
        max_segment_chars=cfg.max_segment_chars, max_output_tokens=PROVIDERS[cfg.provider]["max_output_tokens"],
    )


# English wording for the shared patch guard's rejection reasons
_REASONS = [
    (r"^补丁没有写明修的是哪类问题.*", "patch has no valid type"),
    (r"^find 为空$", "find is empty"),
    (r"^find 与 replace 相同$", "find equals replace"),
    (r"^补丁不得跨行$", "patches must stay on one line"),
    (r"^find 超过 (\d+) 字.*", r"find is longer than \1 characters"),
    (r"^补丁中不得出现占位符.*", "patches must not contain {{Pn}} placeholders"),
    (r"^find 在初稿中不存在.*", "find was not found verbatim in the original"),
    (r"^find 在初稿中出现 (\d+) 次.*", r"find occurs \1 times (ambiguous)"),
    (r"^补丁切断了受保护内容「(.*)」$", r"patch cuts through protected content '\1'"),
    (r"^补丁丢失了受保护内容「(.*)」$", r"patch drops protected content '\1'"),
    (r"^补丁复制了受保护内容「(.*)」$", r"patch duplicates protected content '\1'"),
    (r"^合并后占位符校验失败$", "placeholder check failed after merging"),
    (r"^补丁改变了表格列数$", "patch changes the number of table columns"),
    (r"^补丁向空单元格填写内容$", "patch writes into an empty table cell"),
    (r"^表格单元格「(.*?)」：(.*)$", r"table cell '\1': \2"),
    (r"^补丁格式错误，已忽略全部补丁：(.*)$", r"malformed patches, all ignored: \1"),
    (r"^verify: 受保护内容丢失：(.*)$", r"final check failed, original returned: protected content lost: \1"),
    (r"^verify: (.*)$", r"final check failed, original returned: \1"),
]


def reason_en(reason: str | None) -> str:
    m = re.match("^表格单元格「(.*?)」：(.*)$", reason or "")
    if m:
        return f"table cell '{m.group(1)}': {reason_en(m.group(2))}"
    for pat, rep in _REASONS:
        if reason and re.match(pat, reason):
            return re.sub(pat, rep, reason)
    return reason or ""


class EnPipeline(Pipeline):
    masker_cls = EnMasker

    def __init__(self, config: PipelineConfig | None = None, translator_factory=None, store: DraftStore | None = None,
                 router: TelegraphRouter | None = None):
        config = config or EnPipelineConfig.from_env()
        super().__init__(config, translator_factory or en_translator_factory,
                         store or DraftStore(config.store_dir or telemetry.home() / "drafts-en", config.draft_ttl_s),
                         router)

    def _needs_rtt(self, slot: Slot) -> bool:
        return len(_WORDS.findall(PLACEHOLDER_RE.sub("", slot.masked))) >= self.config.min_rtt_chars

    def _contexts(self, doc: Document, translated: list[int], question: str | None) -> list[str | None]:
        n = self.config.rtt_context_chars
        if n <= 0:
            return [None] * len(translated)
        q = (question or "").strip()[:200]
        out: list[str | None] = []
        for i in translated:
            prev = "\n".join(s.original.strip() for s in doc.slots[:i] if s.original.strip())
            if len(prev) > n:
                prev = prev[-n:]
                m = re.search(r"(?<=[.!?\n])\s+", prev)
                if m and m.end() < n // 2:  # drop the cut-off half sentence
                    prev = prev[m.end():]
            ctx = "\n".join(x for x in (q, prev.strip()) if x)
            out.append(ctx or None)
        return out

    def _radar(self, orig_masked, rtt_slots, translated, rules):
        return scan(orig_masked, dense=rules in DENSE_RADAR_RULES, jargon=rules not in NO_JARGON_RADAR_RULES)

    def _is_routed(self, rules: str) -> bool:
        return rules in ROUTED_RULES

    def _route_report(self, report, masked_slots, placeholders, router):
        return route_report(report, masked_slots, placeholders, router, sentences_fn=telegraph_sentences)

    def _radar_counts(self, slots: list[str]) -> dict:
        return scan(slots).counts

    def _audit_prompt(self, original, rtt_text, radar_text, rules, question):
        return build_audit_prompt(original, rtt_text, radar_text, rules, question)

    def _clean(self, text: str, reference: str) -> tuple[str, int]:
        return clean_punctuation_en(text, reference)

    def _resolve_rules(self, rules: str) -> str:
        return resolve_rules(rules)

    def _apply(self, draft, plist, rules, rejected=None):
        base = [s.masked for s in draft.doc.slots]  # patches land on the original; the round trip is a reference
        return apply_patches(draft.doc, base, plist, MAX_FIND.get(rules, 400), rejected, PROBLEM_TYPES.get(rules))

    def finalize(self, draft_id: str, patches: Any = None, keep_draft: bool = False, audit_rules: str | None = None):
        res = super().finalize(draft_id, patches, keep_draft, audit_rules)
        for p in res.patches:
            if p.get("reject_reason"):
                p["reject_reason"] = reason_en(p["reject_reason"])
        if res.fallback_reason:
            res.fallback_reason = reason_en(res.fallback_reason)
        return res
