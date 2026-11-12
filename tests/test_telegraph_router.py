import json
import re

from no_ai_fluff.masker import parse
from no_ai_fluff.patcher import (
    AUDIT_INSTRUCTIONS_V8,
    AUDIT_INSTRUCTIONS_V16_DENSE,
    build_audit_prompt,
    effective_rules,
    tidy_cjk_spaces,
)
from no_ai_fluff.radar import scan_single
from no_ai_fluff.telegraph_router import TelegraphRouter, parse_verdicts, route_report

JARGON = "入口收紧准入门槛，挂载契约门禁和安全边界；中间层编排多个分支，控制预算、并发水位与背压，避免上下文污染。"
TRIP = "注意：五一园林人多，门票提前约，早上早点进，别追网红店排队，晚上逛平江路。"


def _fake(verdicts):
    calls = []

    def chat(prompt):
        calls.append(prompt)
        n = len(re.findall(r"(?m)^\d+\. ", prompt))
        key = "technical" if '"technical"' in prompt else "telegraph"
        items = [{"id": i + 1, key: verdicts[i], "why": "测试"} for i in range(n)]
        return json.dumps(items, ensure_ascii=False), {"prompt_tokens": 10, "completion_tokens": 5}

    return chat, calls


def test_router_filters_hints_and_caches(tmp_path):
    doc = parse(JARGON + "\n\n" + TRIP)
    slots = [s.masked for s in doc.slots]
    rep = scan_single(slots, dense=True)
    assert sum(h.kind == "telegraph" for h in rep.hints) == 2
    chat, calls = _fake([True, False])
    r = TelegraphRouter(chat, tmp_path)
    res = route_report(rep, slots, doc.placeholders, r)
    assert res.kept == 1 and res.calls == 1 and len(calls) == 1 and '"technical"' in calls[0]
    kept = [h.excerpt for h in res.report.hints if h.kind == "telegraph"]
    assert len(kept) == 2 and res.dense  # 文档级分诊：任一句是技术语境，电报提示全部保留
    # 第二次全部命中缓存，不再调用
    res2 = route_report(rep, slots, doc.placeholders, r)
    assert res2.calls == 0 and len(calls) == 1 and all(v.cached for v in res2.verdicts)


def test_router_all_false_removes_marker_and_none_router_keeps_all(tmp_path):
    doc = parse(TRIP)
    slots = [s.masked for s in doc.slots]
    rep = scan_single(slots, dense=True)
    chat, _ = _fake([False])
    res = route_report(rep, slots, doc.placeholders, TelegraphRouter(chat, tmp_path))
    assert "[电报式短句群]" not in res.report.to_prompt()
    assert res.report.to_prompt() == scan_single(slots).to_prompt()
    assert route_report(rep, slots, doc.placeholders, None).report is rep
    # 只读缓存、没有模型时，未分诊的句子保守保留
    res3 = route_report(rep, slots, doc.placeholders, TelegraphRouter(None, tmp_path / "empty"))
    assert res3.kept == 1


def test_parse_verdicts_tolerates_noise():
    raw = '说明 [{"id": 2, "telegraph": false}, {"id": 1, "telegraph": true, "why": "x"}]'
    assert parse_verdicts(raw, 2) == [(True, "x"), (False, "")]
    assert parse_verdicts("not json", 1) == [None]
    twice = '[{"id": 1, "technical": true, "why": "a"}]\n\n自言自语 [x]。[{"id": 1, "technical": false}]'
    assert parse_verdicts(twice, 1, "technical") == [(True, "a")]


def test_second_generation_gate_and_aliases():
    plain, dense = "- [长句] 一句很长", "- [电报式短句群] 一句切成 6 个短片段"
    for gen2, gen1 in (("v16", "v8"), ("v2", "v1")):
        assert effective_rules(gen2, plain) == gen1 and effective_rules(gen2, dense) == gen2
        assert build_audit_prompt("原文", "参考", plain, gen2).startswith(AUDIT_INSTRUCTIONS_V8)
        assert build_audit_prompt("原文", "参考", dense, gen2).startswith(AUDIT_INSTRUCTIONS_V16_DENSE)
    assert build_audit_prompt("原文", "参考", dense, "v1") == build_audit_prompt("原文", "参考", dense, "v8")
    assert "电报堆叠" not in AUDIT_INSTRUCTIONS_V8 and "11. [电报堆叠]" in AUDIT_INSTRUCTIONS_V16_DENSE


def test_tidy_cjk_spaces_only_removes_new_gaps():
    assert tidy_cjk_spaces("出口执行 冒烟测试", "出口执行 smoke 冒烟测试") == ("出口执行冒烟测试", 1)
    assert tidy_cjk_spaces("用 Harness 编排", "用 Harness 编排")[1] == 0
    assert tidy_cjk_spaces("第一 第二", "第一 第二") == ("第一 第二", 0)


def test_domain_router_is_doc_level(tmp_path):
    from no_ai_fluff.telegraph_router import DOMAIN_SPEC

    doc = parse(JARGON + "\n\n" + TRIP)
    slots = [s.masked for s in doc.slots]
    rep = scan_single(slots, dense=True)
    chat, _ = _fake([True, False])
    res = route_report(rep, slots, doc.placeholders, TelegraphRouter(chat, tmp_path, spec=DOMAIN_SPEC))
    assert res.dense and res.report.to_prompt() == rep.to_prompt()  # 任一句是技术语境：电报提示全部保留
    chat2, _ = _fake([False, False])
    res2 = route_report(rep, slots, doc.placeholders, TelegraphRouter(chat2, tmp_path / "b", spec=DOMAIN_SPEC))
    assert not res2.dense and res2.report.to_prompt() == scan_single(slots).to_prompt()
    assert effective_rules("v16", res2.report.to_prompt()) == "v8"
    assert build_audit_prompt("原文", "参考", res2.report.to_prompt(), "v16") == build_audit_prompt("原文", "参考", res2.report.to_prompt(), "v8")
    assert build_audit_prompt("原文", "参考", res.report.to_prompt(), "v16").startswith(AUDIT_INSTRUCTIONS_V16_DENSE)
