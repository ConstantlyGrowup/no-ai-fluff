import pytest

from no_ai_fluff.rtt import RTTResult


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """所有测试的遥测与草稿都写进临时目录，且不读真实 Key。"""
    monkeypatch.setenv("NO_AI_FLUFF_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    return tmp_path / "home"


class FakeTranslator:
    """按映射函数改写每段掩码文本，模拟往返翻译。"""

    def __init__(self, fn=lambda s: s, exc=None, delay=0.0):
        self.fn, self.exc, self.delay = fn, exc, delay
        self.seen = []

    def round_trip(self, segs, ids, timeout_s=None):
        import time

        self.seen.append(list(segs))
        if self.delay:
            time.sleep(self.delay)
        if self.exc:
            raise self.exc
        return RTTResult([self.fn(s) for s in segs], [""] * len(segs), [], {}, 2, 0.01)


@pytest.fixture
def fake_translator():
    return FakeTranslator


class FakeClient:
    """模拟 OpenRouterClient.chat：handler(prompt_text, model) -> 回复文本。"""

    timeout = 30.0

    def __init__(self, handler):
        self.handler = handler
        self.prompts = []

    def chat(self, model, messages, **kw):
        from no_ai_fluff.rtt import ChatResult

        prompt = messages[-1]["content"]
        self.prompts.append(prompt)
        return ChatResult(self.handler(prompt), 0.01, {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.0001})


@pytest.fixture
def fake_client():
    return FakeClient
