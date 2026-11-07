import re

import pytest

from no_ai_fluff.rtt import OpenRouterClient, OpenRouterError, RoundTripTranslator, build_prompt, split_batched


def body_of(prompt):
    return prompt.split("\n\n", 1)[1]


def echo_handler(prompt):
    """往返恒等：法语方向原样返回，中文方向也原样返回。"""
    return body_of(prompt)


def test_build_prompt_is_official_template_only():
    p = build_prompt("你好 {{P0}}", "fr", batched=True)
    assert p == "将以下文本翻译为法语，注意只需要输出翻译后的结果，不要额外解释：\n\n你好 {{P0}}"


def test_split_batched():
    assert split_batched("⟦0⟧ a\n⟦1⟧ b", 2) == ["a", "b"]
    assert split_batched("a\n⟦1⟧ b", 2) == ["a", "b"]  # 首个分界符被吞
    assert split_batched("⟦ 0 ⟧ a ⟦1⟧ b", 2) == ["a", "b"]
    assert split_batched("⟦0⟧ a", 2) is None
    assert split_batched("⟦0⟧ a ⟦0⟧ b", 2) is None


def test_round_trip_batches_segments(fake_client):
    client = fake_client(echo_handler)
    tr = RoundTripTranslator(client)
    res = tr.round_trip(["第一段 {{P0}} 内容", "第二段内容比较长"], [[0], []])
    assert res.texts == ["第一段 {{P0}} 内容", "第二段内容比较长"]
    assert res.calls == 2  # 一去一回，两段合批
    assert "⟦0⟧" in client.prompts[0] and "⟦1⟧" in client.prompts[0]
    assert res.fallbacks == []
    assert res.prompt_tokens == 20 and res.cost_usd == pytest.approx(0.0002)


def test_marker_mismatch_degrades_to_single_segments(fake_client):
    def handler(prompt):
        body = body_of(prompt)
        return re.sub(r"⟦\d+⟧\s*", "", body) if "⟦" in body else body  # 吞掉全部分界符

    client = fake_client(handler)
    res = RoundTripTranslator(client).round_trip(["第一段内容在这里", "第二段内容在这里"], [[], []])
    assert res.texts == ["第一段内容在这里", "第二段内容在这里"]
    assert res.calls == 2 + 4  # 去程合批失败后逐段 2 次；回程同理


def test_segment_losing_placeholder_falls_back_to_original(fake_client):
    def handler(prompt):
        return body_of(prompt).replace("{{P0}}", "")

    res = RoundTripTranslator(fake_client(handler)).round_trip(["调用 {{P0}} 读取数据"], [[0]])
    assert res.texts == ["调用 {{P0}} 读取数据"]
    assert res.fallbacks == [0] and "placeholder" in res.reasons[0]


def test_loose_placeholder_variants_are_normalized(fake_client):
    res = RoundTripTranslator(fake_client(lambda p: body_of(p).replace("{{P0}}", "{{ P0 }}"))).round_trip(
        ["调用 {{P0}} 读取数据"], [[0]]
    )
    assert res.texts == ["调用 {{P0}} 读取数据"] and res.fallbacks == []


def test_non_chinese_back_translation_rejected(fake_client):
    res = RoundTripTranslator(fake_client(lambda p: "Bonjour tout le monde")).round_trip(["你好世界，今天天气不错"], [[]])
    assert res.fallbacks == [0]


def test_length_ratio_guard(fake_client):
    res = RoundTripTranslator(fake_client(lambda p: "短")).round_trip(["这是一段足够长的中文句子用于测试"], [[]])
    assert res.fallbacks == [0] and "length" in res.reasons[0]


def test_markup_change_rejected(fake_client):
    res = RoundTripTranslator(fake_client(lambda p: body_of(p).replace("**", ""))).round_trip(
        ["这里有 **加粗** 的内容"], [[]]
    )
    assert res.fallbacks == [0]


def test_deadline_raises_timeout(fake_client):
    import time

    def slow(prompt):
        time.sleep(0.5)
        return body_of(prompt)

    t0 = time.monotonic()
    with pytest.raises(TimeoutError):
        RoundTripTranslator(fake_client(slow)).round_trip(["一段中文内容"], [[]], timeout_s=0.2)
    assert time.monotonic() - t0 < 0.9  # 不会等后台线程跑完去程+回程


def test_client_requires_key():
    with pytest.raises(OpenRouterError, match="OPENROUTER_API_KEY"):
        OpenRouterClient(api_key="").chat("m", [{"role": "user", "content": "x"}])


def test_client_disk_cache(tmp_path, monkeypatch):
    import json

    calls = []

    class Resp:
        def __init__(self, data):
            self.data = data

        def read(self):
            return json.dumps(self.data).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        calls.append(json.loads(req.data))
        return Resp({"choices": [{"message": {"content": "Bonjour"}}], "usage": {"prompt_tokens": 3}})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    c = OpenRouterClient(api_key="k", cache_dir=tmp_path)
    r1 = c.chat("m", [{"role": "user", "content": "你好"}])
    r2 = c.chat("m", [{"role": "user", "content": "你好"}])
    assert r1.text == r2.text == "Bonjour"
    assert not r1.cached and r2.cached and len(calls) == 1
    assert calls[0]["temperature"] == 0.1


def test_client_retries_then_raises(monkeypatch):
    import urllib.error

    n = []

    def boom(req, timeout=None):
        n.append(1)
        raise urllib.error.URLError("down")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    monkeypatch.setattr("time.sleep", lambda s: None)
    with pytest.raises(OpenRouterError):
        OpenRouterClient(api_key="k", max_retries=2).chat("m", [{"role": "user", "content": "x"}])
    assert len(n) == 3


def test_marker_loss_in_large_batch_uses_bisection(fake_client):
    """模拟模型在整批里吞掉分界符、但小批正常：应二分重试，而不是 2N 次逐段调用。"""

    def handler(prompt):
        body = body_of(prompt)
        n = len(re.findall(r"⟦\d+⟧", body))
        return re.sub(r"\n⟦\d+⟧\s*", " ", body) if n > 4 else body  # 超过 4 段就把行合并

    segs = [f"第{i}段中文内容在这里" for i in range(16)]
    client = fake_client(handler)
    res = RoundTripTranslator(client).round_trip(segs, [[] for _ in segs])
    assert res.texts == segs and res.fallbacks == []
    assert res.calls < 2 * len(segs)  # 逐段串行需要 32 次


def test_failed_segments_retry_in_parallel(fake_client):
    import threading
    import time

    active, peak = [0], [0]
    lock = threading.Lock()

    def handler(prompt):
        body = body_of(prompt)
        if "⟦" in body:  # 合批结果把占位符全弄丢，触发逐段重试
            return body.replace("{{P0}}", "").replace("{{P1}}", "").replace("{{P2}}", "")
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.05)
        with lock:
            active[0] -= 1
        return body

    segs = [f"调用 {{{{P{i}}}}} 读取第{i}段数据" for i in range(3)]
    res = RoundTripTranslator(fake_client(handler)).round_trip(segs, [[0], [1], [2]])
    assert res.texts == segs and res.fallbacks == []
    assert peak[0] >= 2


def test_content_filter_isolated_to_offending_segment(fake_client):
    """某段触发上游内容过滤：只有这一段退回原文，其余段落正常，整篇不回退。"""
    from no_ai_fluff.rtt import ContentFilteredError

    class FilteringClient(fake_client):
        def chat(self, model, messages, **kw):
            if "地球是平的" in messages[-1]["content"]:
                self.prompts.append(messages[-1]["content"])
                raise ContentFilteredError("上游内容过滤（content_filter）")
            return super().chat(model, messages, **kw)

    segs = ["第一段正常的中文内容", "古人觉得地球是平的这很正常", "第三段正常的中文内容", "第四段正常的中文内容"]
    client = FilteringClient(echo_handler)
    res = RoundTripTranslator(client).round_trip(segs, [[] for _ in segs])
    assert res.texts == segs
    assert res.fallbacks == [1] and "ContentFiltered" in res.reasons[1]
    # 被过滤的单段不会被反复重试
    assert sum("地球是平的" in p and "⟦" not in p for p in client.prompts) == 1


def test_client_does_not_retry_content_filter(monkeypatch):
    import json

    from no_ai_fluff.rtt import ContentFilteredError

    n = []

    class Resp:
        def read(self):
            return json.dumps({"choices": [{"finish_reason": "content_filter", "message": {"content": None}}]}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake(req, timeout=None):
        n.append(1)
        return Resp()

    monkeypatch.setattr("urllib.request.urlopen", fake)
    with pytest.raises(ContentFilteredError):
        OpenRouterClient(api_key="k", max_retries=2).chat("m", [{"role": "user", "content": "x"}])
    assert len(n) == 1


def test_small_batches_by_segment_count(fake_client):
    client = fake_client(echo_handler)
    segs = [f"第{i}段中文内容" for i in range(13)]
    res = RoundTripTranslator(client, batch_segments=6).round_trip(segs, [[] for _ in segs])
    assert res.texts == segs and res.calls == 6  # 3 批 × 去回 2 次
