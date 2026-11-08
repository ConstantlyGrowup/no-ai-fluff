import json
import time

import pytest

from no_ai_fluff import telemetry
from no_ai_fluff.pipeline import DraftNotFound, DraftStore, Pipeline, PipelineConfig, verify_final
from no_ai_fluff.masker import parse

DOC = """## 结论

这个 edge case 的 overhead 是 non-trivial 的，`fetchUser()` 耗时 15秒。

```python
print("代码块不动")
```

值得注意的是，命中率是 82.7%。
"""


def make(fake, tmp_path, **cfg):
    return Pipeline(PipelineConfig(store_dir=str(tmp_path / "drafts"), **cfg), translator_factory=lambda c: fake)


def loosen(s):
    import re

    for en, zh in (("edge case", "极端情况"), ("overhead", "开销"), ("non-trivial", "不可忽视")):
        s = re.sub(rf"\s*{en}\s*", zh, s)
    return s.replace("值得注意的是，", "")


def test_prepare_returns_audit_material(fake_translator, tmp_path):
    fake = fake_translator(loosen)
    pipe = make(fake, tmp_path)
    res = pipe.prepare(DOC)
    assert res.original == DOC
    assert "极端情况的开销是不可忽视的，`fetchUser()` 耗时 15秒" in res.rtt_draft
    assert 'print("代码块不动")' in res.rtt_draft
    assert not res.fallback
    assert res.stats["translated_slots"] == 2 and res.stats["placeholders"] == 1
    assert "【原文】" in res.audit_prompt and "【RTT 初稿】" in res.audit_prompt
    # 只有正文槽位送去翻译，代码块和标题标记都不送
    sent = fake.seen[0]
    assert not any("print" in s or "##" in s for s in sent)
    assert any("{{P0}}" in s for s in sent)


def test_full_handshake_with_patch(fake_translator, tmp_path):
    pipe = make(fake_translator(loosen), tmp_path)
    prep = pipe.prepare(DOC)
    patches = [{"find": "耗时 15秒", "replace": "调用一次耗时 15 秒", "reason": "补主语"}]
    fin = pipe.finalize(prep.draft_id, json.dumps(patches, ensure_ascii=False))
    assert fin.applied == 1 and not fin.fallback
    assert "`fetchUser()` 调用一次耗时 15 秒" in fin.final_text
    assert "```python\nprint(\"代码块不动\")\n```" in fin.final_text
    with pytest.raises(DraftNotFound):  # 定稿后草稿销毁
        pipe.finalize(prep.draft_id, [])


def test_empty_patches_returns_rtt_draft(fake_translator, tmp_path):
    pipe = make(fake_translator(loosen), tmp_path)
    prep = pipe.prepare(DOC)
    fin = pipe.finalize(prep.draft_id, [])
    assert fin.final_text == prep.rtt_draft and fin.applied == 0


def test_bad_patch_json_keeps_rtt_draft(fake_translator, tmp_path):
    pipe = make(fake_translator(loosen), tmp_path)
    prep = pipe.prepare(DOC)
    fin = pipe.finalize(prep.draft_id, "这不是 JSON")
    assert fin.final_text == prep.rtt_draft
    assert "补丁格式错误" in fin.fallback_reason


@pytest.mark.parametrize("exc", [RuntimeError("网络断了"), ValueError("坏响应")])
def test_rtt_exception_falls_back_to_original(fake_translator, tmp_path, exc):
    pipe = make(fake_translator(exc=exc), tmp_path)
    prep = pipe.prepare(DOC)
    assert prep.fallback and prep.rtt_draft == DOC
    fin = pipe.finalize(prep.draft_id, [{"find": "值得注意的是，", "replace": ""}])
    assert fin.applied == 1 and "值得注意的是" not in fin.final_text  # 原文上仍可打补丁


def test_missing_key_falls_back_to_original(tmp_path):
    pipe = Pipeline(PipelineConfig(store_dir=str(tmp_path / "d")))  # 默认工厂，conftest 已移除 Key
    prep = pipe.prepare("这个方案的 overhead 很大。")
    assert prep.fallback and "OPENROUTER_API_KEY" in prep.fallback_reason
    assert prep.rtt_draft == "这个方案的 overhead 很大。"


def test_rtt_timeout_hard_bound(fake_translator, tmp_path):
    pipe = make(fake_translator(delay=3), tmp_path, chat_timeout_s=0.3)
    t0 = time.monotonic()
    prep = pipe.prepare("这个方案的 overhead 很大。")
    assert time.monotonic() - t0 < 2.0
    assert prep.fallback and prep.fallback_reason.startswith("timeout")
    assert prep.rtt_draft == "这个方案的 overhead 很大。"


def test_mode_selection(fake_translator, tmp_path):
    pipe = make(fake_translator(), tmp_path, doc_chars=50)
    assert pipe.prepare("短句。").stats["mode"] == "chat"
    assert pipe.prepare("短句。", exit="file").stats["mode"] == "doc"
    assert pipe.prepare("长" * 60).stats["mode"] == "doc"
    assert pipe.prepare("<p>网页</p>").stats["mode"] == "doc"


def test_rtt_output_with_broken_placeholders_is_ignored(fake_translator, tmp_path):
    pipe = make(fake_translator(lambda s: s.replace("{{P0}}", "")), tmp_path)
    prep = pipe.prepare("调用 `foo()` 读取数据。")
    assert prep.rtt_draft == "调用 `foo()` 读取数据。"


def test_no_translatable_text_skips_rtt(fake_translator, tmp_path):
    fake = fake_translator()
    pipe = make(fake, tmp_path)
    prep = pipe.prepare("```\nonly code\n```\n")
    assert fake.seen == [] and not prep.fallback
    assert pipe.finalize(prep.draft_id, []).final_text == "```\nonly code\n```\n"


def test_html_roundtrip(fake_translator, tmp_path):
    html = '<p class="x">这个 overhead 很大</p><pre>保持</pre>'
    pipe = make(fake_translator(loosen), tmp_path)
    prep = pipe.prepare(html)
    fin = pipe.finalize(prep.draft_id, [])
    assert fin.final_text == '<p class="x">这个开销很大</p><pre>保持</pre>'


def test_draft_survives_process_boundary(fake_translator, tmp_path):
    p1 = make(fake_translator(loosen), tmp_path)
    prep = p1.prepare(DOC)
    p2 = make(fake_translator(), tmp_path)  # 新实例，内存为空，只能从磁盘读
    fin = p2.finalize(prep.draft_id, [])
    assert fin.final_text == prep.rtt_draft


def test_draft_store_rejects_path_traversal(tmp_path):
    with pytest.raises(DraftNotFound):
        DraftStore(tmp_path).get("../../etc/passwd")


def test_draft_store_gc(tmp_path, fake_translator):
    pipe = make(fake_translator(), tmp_path, draft_ttl_s=0.0)
    prep = pipe.prepare("一句话。")
    time.sleep(0.05)
    pipe.store.gc()
    with pytest.raises(DraftNotFound):
        pipe.finalize(prep.draft_id, [])


def test_verify_final_catches_missing_placeholder():
    doc = parse("调用 `a` 和 `b`。")
    assert verify_final(doc, [s.masked for s in doc.slots]) == ""
    assert "占位符" in verify_final(doc, ["调用 {{P0}}。"])


def test_finalize_falls_back_when_verify_fails(fake_translator, tmp_path):
    pipe = make(fake_translator(), tmp_path)
    prep = pipe.prepare("调用 `a` 读取。")
    draft = pipe.store.get(prep.draft_id)
    draft.rtt_slots = ["调用读取。"]  # 模拟初稿被破坏
    pipe.store.put(draft)
    fin = pipe.finalize(prep.draft_id, [])
    assert fin.fallback and fin.final_text == "调用 `a` 读取。"


def test_run_with_reviewer(fake_translator, tmp_path):
    pipe = make(fake_translator(loosen), tmp_path)
    seen = {}

    def reviewer(prompt):
        seen["prompt"] = prompt
        return '[{"find": "耗时 15秒", "replace": "耗时 15 秒", "reason": "空格"}]'

    prep, fin = pipe.run(DOC, reviewer)
    assert "【RTT 初稿】" in seen["prompt"] and fin.applied == 1


def test_run_reviewer_failure_degrades_to_rtt(fake_translator, tmp_path):
    def reviewer(prompt):
        raise RuntimeError("宿主模型挂了")

    prep, fin = make(fake_translator(loosen), tmp_path).run(DOC, reviewer)
    assert fin.final_text == prep.rtt_draft
    assert "reviewer error: RuntimeError: 宿主模型挂了" in fin.fallback_reason  # 不能静默吞掉


def test_telemetry_events_written_without_text(fake_translator, tmp_path):
    pipe = make(fake_translator(loosen), tmp_path)
    prep = pipe.prepare(DOC)
    pipe.finalize(prep.draft_id, [{"find": "不存在", "replace": "x"}])
    events = telemetry.read_events()
    assert [e["event"] for e in events] == ["prepare", "finalize"]
    assert events[1]["patches_received"] == 1 and events[1]["patches_rejected"] == 1
    raw = telemetry.events_path().read_text("utf-8")
    assert "fetchUser" not in raw and "极端情况" not in raw  # 只记统计量
    s = telemetry.summarize(events)
    assert s["prepare_count"] == 1 and s["radar_original"]["loose_english"] >= 1
    assert "雷达类别" in telemetry.render_table(s)


def test_telemetry_can_be_disabled(fake_translator, tmp_path, monkeypatch):
    monkeypatch.setenv("NO_AI_FLUFF_TELEMETRY", "0")
    pipe = make(fake_translator(), tmp_path)
    pipe.finalize(pipe.prepare("一句话。").draft_id, [])
    assert telemetry.read_events() == []


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("NO_AI_FLUFF_MODEL", "google/gemma-3-4b-it")
    monkeypatch.setenv("NO_AI_FLUFF_BRIDGE", "ja")
    monkeypatch.setenv("NO_AI_FLUFF_TERMS", "Foo, BarBaz")
    cfg = PipelineConfig.from_env(bridge="ru")
    assert cfg.model == "google/gemma-3-4b-it" and cfg.bridge == "ru" and cfg.extra_terms == ["Foo", "BarBaz"]


def test_short_cjk_slots_skip_rtt(fake_translator, tmp_path):
    from no_ai_fluff.pipeline import needs_rtt
    from no_ai_fluff.masker import Masker

    m = Masker()
    assert not needs_rtt(m.slot("含义"))
    assert not needs_rtt(m.slot("**结论**"))
    assert needs_rtt(m.slot("fallback 方案"))  # 含散装英文，仍要洗
    assert needs_rtt(m.slot("两个维度需要区分清楚"))
    fake = fake_translator()
    pipe = make(fake, tmp_path)
    pipe.prepare("| 含义 | 例子 |\n|---|---|\n| 这个方案的开销比较大 | 甲 |")
    assert fake.seen == [["这个方案的开销比较大"]]
