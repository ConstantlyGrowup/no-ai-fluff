"""多服务商客户端、腾讯云 hy-mt2-pro 适配（8K 上下文）与超长段落切分。全部 mock 网络。"""

import json
import re

import pytest

from no_ai_fluff.masker import PLACEHOLDER_RE
from no_ai_fluff.pipeline import Pipeline, PipelineConfig
from no_ai_fluff.rtt import (
    PROVIDERS,
    ChatClient,
    OpenRouterClient,
    RoundTripTranslator,
    TruncatedOutputError,
    split_long,
)


class Resp:
    def __init__(self, data):
        self.data = data

    def read(self):
        return json.dumps(self.data).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def ok(content, finish="stop"):
    return Resp({"choices": [{"message": {"content": content}, "finish_reason": finish}],
                 "usage": {"prompt_tokens": 10, "completion_tokens": 5}})


# ---------------------------------------------------------------- 客户端


def test_tencent_client_defaults_and_no_openrouter_fields(monkeypatch):
    monkeypatch.setenv("TENCENTCLOUD_API_KEY", "tk-123\r")  # Windows .env 的 \r 必须被去掉
    sent = {}

    def fake(req, timeout=None):
        sent["url"] = req.full_url
        sent["auth"] = req.headers["Authorization"]
        sent["body"] = json.loads(req.data)
        return ok("Bonjour")

    monkeypatch.setattr("urllib.request.urlopen", fake)
    c = ChatClient(provider="tencent")
    c.chat("hy-mt2-pro", [{"role": "user", "content": "你好"}], extra=c.default_extra)
    assert sent["url"] == "https://tokenhub.tencentmaas.com/v1/chat/completions"
    assert sent["auth"] == "Bearer tk-123"
    assert "usage" not in sent["body"] and sent["body"]["model"] == "hy-mt2-pro"


def test_deepseek_key_env_variants(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("Deepseek_OFFICIAL_API_KEY", "ds-1")
    c = ChatClient(provider="deepseek")
    assert c.api_key == "ds-1" and c.url.startswith("https://api.deepseek.com")


def test_openrouter_client_backward_compatible():
    c = OpenRouterClient(api_key="k")
    assert c.provider == "openrouter" and c.default_extra == {"usage": {"include": True}}


def test_custom_url_drops_provider_extra():
    assert ChatClient(api_key="k", url="http://127.0.0.1:1/v1/chat/completions").default_extra is None


def test_unknown_provider_rejected():
    with pytest.raises(ValueError):
        ChatClient(provider="nope")
    with pytest.raises(ValueError):
        PipelineConfig(provider="nope")


def test_missing_key_message_names_provider_env(monkeypatch):
    for k in PROVIDERS["tencent"]["key_env"]:
        monkeypatch.delenv(k, raising=False)
    from no_ai_fluff.rtt import OpenRouterError

    with pytest.raises(OpenRouterError, match="TENCENTCLOUD_API_KEY"):
        ChatClient(provider="tencent").chat("m", [{"role": "user", "content": "x"}])


def test_truncated_output_raises_without_retry(monkeypatch):
    n = []

    def fake(req, timeout=None):
        n.append(1)
        return ok("Le début seulement", finish="length")

    monkeypatch.setattr("urllib.request.urlopen", fake)
    with pytest.raises(TruncatedOutputError):
        ChatClient(api_key="k", provider="tencent", max_retries=2).chat("m", [{"role": "user", "content": "x"}])
    assert len(n) == 1


# ---------------------------------------------------------------- 配置


def test_pipeline_config_provider_defaults():
    t = PipelineConfig(provider="tencent")
    assert t.model == "hy-mt2-pro" and t.batch_chars == 1200 and t.max_segment_chars == 1200
    o = PipelineConfig()
    assert o.model == "tencent/hy-mt2-30b-a3b" and o.batch_chars == 400
    assert PipelineConfig(provider="tencent", model="hy-mt2-lite", batch_chars=500).batch_chars == 500


def test_config_from_env_provider(monkeypatch):
    monkeypatch.setenv("NO_AI_FLUFF_PROVIDER", "tencent")
    monkeypatch.delenv("NO_AI_FLUFF_MODEL", raising=False)
    cfg = PipelineConfig.from_env()
    assert cfg.provider == "tencent" and cfg.model == "hy-mt2-pro"


# ---------------------------------------------------------------- 切分


def test_split_long_preserves_text_and_limits_length():
    text = "第一句话比较长一点。" * 30 + "第二部分没有句号但有逗号，" * 20 + "无标点" * 200
    parts = split_long(text, 100)
    assert "".join(parts) == text
    assert all(len(p) <= 100 for p in parts)
    assert parts[0].endswith("。")  # 优先在句末切


def test_split_long_never_cuts_placeholders():
    text = "甲" * 95 + "{{P12}}" + "乙" * 95 + "{{P13}}" + "丙" * 50
    parts = split_long(text, 100)
    assert "".join(parts) == text
    found = [x for p in parts for x in PLACEHOLDER_RE.findall(p)]
    assert found == ["12", "13"]


def test_split_long_short_text_untouched():
    assert split_long("短句。", 100) == ["短句。"]


# ---------------------------------------------------------------- 往返翻译


def body_of(prompt):
    return prompt.split("\n\n", 1)[1]


class EchoClient:
    timeout = 30.0
    default_extra = None

    def __init__(self, max_input=None):
        self.prompts = []
        self.max_input = max_input

    def chat(self, model, messages, **kw):
        from no_ai_fluff.rtt import ChatResult

        p = messages[-1]["content"]
        self.prompts.append(p)
        if self.max_input and len(body_of(p)) > self.max_input:
            raise TruncatedOutputError("输出被长度上限截断（finish_reason=length）")
        return ChatResult(body_of(p), 0.01, {})


def test_long_segment_is_split_translated_and_rejoined():
    seg = "".join(f"这是第{i}句，调用 {{{{P{i}}}}} 读取数据。" for i in range(40))
    ids = list(range(40))
    client = EchoClient()
    res = RoundTripTranslator(client, max_segment_chars=200).round_trip([seg, "短的一段内容"], [ids, []])
    assert res.texts == [seg, "短的一段内容"] and res.fallbacks == []
    assert max(len(body_of(p)) for p in client.prompts) < 200 * 6 + 200  # 每次请求都是小块


def test_truncated_batch_is_bisected_until_it_fits():
    segs = [f"第{i}段中文内容，长度大约二十个字左右。" for i in range(12)]
    client = EchoClient(max_input=150)  # 模拟 8K 上下文：太大的批次会被截断
    res = RoundTripTranslator(client, batch_chars=10_000, batch_segments=0).round_trip(segs, [[] for _ in segs])
    assert res.texts == segs and res.fallbacks == []


def test_piece_failure_only_falls_back_that_piece():
    class Partial(EchoClient):
        def chat(self, model, messages, **kw):
            if "坏句子" in messages[-1]["content"]:
                from no_ai_fluff.rtt import ContentFilteredError

                raise ContentFilteredError("x")
            return super().chat(model, messages, **kw)

    seg = "好句子一号在这里。" * 10 + "坏句子在这里。" + "好句子二号在这里。" * 10
    res = RoundTripTranslator(Partial(), max_segment_chars=40).round_trip([seg], [[]])
    assert res.texts == [seg]  # 回退的块保留原文，整体拼回仍等于原文
    assert res.fallbacks == [0] and "ContentFiltered" in res.reasons[0]


def test_tencent_pipeline_end_to_end_with_fake_server(monkeypatch, tmp_path):
    monkeypatch.setenv("TENCENTCLOUD_API_KEY", "tk")
    seen = []

    def fake(req, timeout=None):
        body = json.loads(req.data)
        seen.append(body)
        text = body_of(body["messages"][0]["content"])
        return ok(text.replace("overhead", "开销"))

    monkeypatch.setattr("urllib.request.urlopen", fake)
    pipe = Pipeline(PipelineConfig(provider="tencent", store_dir=str(tmp_path)))
    prep = pipe.prepare("这个方案的overhead比较大，调用 `foo()` 会读文件。")
    assert not prep.fallback and "开销" in prep.rtt_draft and "`foo()`" in prep.rtt_draft
    assert prep.stats["provider"] == "tencent" and prep.stats["model"] == "hy-mt2-pro"
    assert all(b["model"] == "hy-mt2-pro" and "usage" not in b for b in seen)


def test_from_env_provider_override_picks_provider_defaults(monkeypatch):
    monkeypatch.delenv("NO_AI_FLUFF_PROVIDER", raising=False)
    monkeypatch.delenv("NO_AI_FLUFF_MODEL", raising=False)
    cfg = PipelineConfig.from_env(provider="tencent")
    assert cfg.model == "hy-mt2-pro" and cfg.batch_chars == 1200
