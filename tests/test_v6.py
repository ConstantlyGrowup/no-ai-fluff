"""v6：往返翻译带上下文、整句英文冻结、补丁打在原文上。"""

import json

from no_ai_fluff.masker import parse
from no_ai_fluff.patcher import AUDIT_RULES, build_audit_prompt
from no_ai_fluff.pipeline import Pipeline, PipelineConfig, rtt_contexts
from no_ai_fluff.rtt import RoundTripTranslator, build_prompt

CTX_MARK = "参考上面的信息，把下面的文本翻译成"


def test_build_prompt_uses_official_context_template():
    p = build_prompt("运维成本低", "ru", context="20 人团队选单体还是微服务")
    assert p == "20 人团队选单体还是微服务\n参考上面的信息，把下面的文本翻译成俄语，注意不需要翻译上文，也不要额外解释：\n运维成本低"
    assert build_prompt("x", "ru", context="  ").startswith("将以下文本翻译为俄语")


def test_round_trip_sends_context_both_ways(fake_client):
    def handler(prompt):
        return prompt.split(CTX_MARK, 1)[1].split("\n", 1)[1] if CTX_MARK in prompt else prompt.split("\n\n", 1)[1]

    client = fake_client(handler)
    res = RoundTripTranslator(client).round_trip(["第一段内容在这里", "第二段内容在这里"], [[], []],
                                                 contexts=["上文：机房运维", None])
    assert res.texts == ["第一段内容在这里", "第二段内容在这里"]
    assert len(client.prompts) == 2 and all(p.startswith("上文：机房运维\n" + CTX_MARK) for p in client.prompts)


def test_rtt_contexts_only_use_preceding_text():
    doc = parse("# 单体还是微服务\n\n运维成本低，部署简单。\n\n第三段说别的事情。\n")
    tr = [i for i, s in enumerate(doc.slots) if "运维" in s.original or "第三段" in s.original]
    ctx = rtt_contexts(doc, tr, 300, question="20 人团队怎么选？")
    assert ctx[0].startswith("20 人团队怎么选？") and "单体还是微服务" in ctx[0] and "运维成本低" not in ctx[0]
    assert "运维成本低" in ctx[1] and "第三段" not in ctx[1]
    assert rtt_contexts(doc, tr, 0) == [None, None]


def test_whole_english_sentence_is_frozen():
    doc = parse("对于 20 人的团队：**Build a solid Monolith, don't build a Microservices platform.** 先把业务做起来。")
    assert "Build a solid Monolith, don't build a Microservices platform." in doc.placeholders
    doc2 = parse("这里用 Modular Monolith 起步，再看 fallback 方案。")
    assert not any(" " in p for p in doc2.placeholders)  # 两三个词的术语不受影响


class Plain:
    def __init__(self):
        self.ctx = None

    def round_trip(self, segs, ids, timeout_s=None, contexts=None):
        from no_ai_fluff.rtt import RTTResult

        self.ctx = contexts
        return RTTResult([s.replace("运维", "运营") for s in segs], [""] * len(segs), [], {}, 2, 0.01)


ORIG = "## 结论\n\n运维成本低，团队可以专注业务。\n\n本方案旨在对系统进行全面赋能。\n"


def v6_pipe(tmp_path, tr):
    return Pipeline(PipelineConfig(store_dir=str(tmp_path), telemetry=False, audit_rules="v6"),
                    translator_factory=lambda c: tr)


def test_v6_prompt_puts_original_first_and_patches_original(tmp_path):
    tr = Plain()
    pipe = v6_pipe(tmp_path, tr)
    prep = pipe.prepare(ORIG, question="怎么选架构")
    assert tr.ctx is not None and tr.ctx[0].startswith("怎么选架构")
    ap = prep.audit_prompt
    assert ap.index("【原文】") < ap.index("【往返参考稿") and "运营成本" in ap  # 参考稿照给，错词由宿主判断
    patches = [
        {"find": "本方案旨在对系统进行全面赋能。", "replace": "这个方案要把系统整体改好。", "type": "空洞大词", "reason": "黑话"},
        {"find": "团队可以专注业务", "replace": "团队能专心做业务", "reason": "没写类型"},
    ]
    fin = pipe.finalize(prep.draft_id, json.dumps(patches, ensure_ascii=False))
    assert "运维成本低" in fin.final_text and "运营" not in fin.final_text  # 底本是原文，往返错词进不来
    assert "这个方案要把系统整体改好。" in fin.final_text and "专心" not in fin.final_text
    assert fin.applied == 1 and fin.rejected == 1
    assert "哪类问题" in fin.patches[1]["reject_reason"]


def test_v6_empty_patches_returns_original(tmp_path):
    pipe = v6_pipe(tmp_path, Plain())
    prep = pipe.prepare(ORIG)
    fin = pipe.finalize(prep.draft_id, "[]")
    assert fin.final_text == prep.original


def test_v6_rules_text():
    v6 = AUDIT_RULES["v6"]
    assert "没有问题的句子一个字都不要动" in v6 and "不设上限" in v6 and "运营成本" in v6
    p = build_audit_prompt("原文X", "参考Y", "雷达Z", rules="v6", question="问题Q")
    assert p.index("问题Q") < p.index("原文X") < p.index("参考Y") < p.index("雷达Z")


def test_soft_english_can_be_translated_only_as_mixed_language_fix(tmp_path):
    text = "## 总结\n\n对于 20 人的团队：**Build a solid Monolith, don't build a Microservices platform.** 每周开一次 Office Hour。\n"
    pipe = v6_pipe(tmp_path, Plain())
    prep = pipe.prepare(text)
    ok = {"find": "**Build a solid Monolith, don't build a Microservices platform.**",
          "replace": "**先把单体做扎实，别急着搭微服务平台。**", "type": "中英混杂", "reason": "整句英文"}
    bad = {"find": "Office Hour", "replace": "答疑时间", "type": "翻译腔", "reason": "类型不对"}
    fin = pipe.finalize(prep.draft_id, json.dumps([ok, bad], ensure_ascii=False))
    assert "**先把单体做扎实，别急着搭微服务平台。**" in fin.final_text and "Build" not in fin.final_text
    assert "Office Hour" in fin.final_text  # 非“中英混杂”类型不准去掉受保护的英文
    assert fin.applied == 1 and "丢失了受保护内容" in fin.patches[1]["reject_reason"]


def test_code_placeholder_never_droppable(tmp_path):
    pipe = v6_pipe(tmp_path, Plain())
    prep = pipe.prepare("这里调用 `get_user()` 读取用户数据，然后继续。\n")
    p = {"find": "调用 `get_user()` 读取", "replace": "调用接口读取", "type": "中英混杂", "reason": "x"}
    fin = pipe.finalize(prep.draft_id, json.dumps([p], ensure_ascii=False))
    assert fin.applied == 0 and "`get_user()`" in fin.final_text


def test_empty_response_with_context_retries_without_context(fake_client):
    def handler(prompt):
        if CTX_MARK in prompt:
            from no_ai_fluff.rtt import OpenRouterError

            raise OpenRouterError("openrouter 请求失败：空响应")
        return prompt.split("\n\n", 1)[1]

    client = fake_client(handler)
    res = RoundTripTranslator(client).round_trip(["第一段内容在这里"], [[]], contexts=["上文"])
    assert res.texts == ["第一段内容在这里"] and res.fallbacks == []


def test_find_with_trailing_space_still_applies(tmp_path):
    pipe = v6_pipe(tmp_path, Plain())
    prep = pipe.prepare("## 总结\n\n本方案旨在对系统进行全面赋能。 \n")
    p = {"find": "本方案旨在对系统进行全面赋能。 ", "replace": "这个方案要把系统整体改好。 ", "type": "空洞大词", "reason": "x"}
    fin = pipe.finalize(prep.draft_id, json.dumps([p], ensure_ascii=False))
    assert fin.applied == 1 and "这个方案要把系统整体改好。" in fin.final_text


def test_acronyms_soft_but_term_list_hard():
    doc = parse("这个 case 我们和 PM 侧同步过，ROI 不错，API 已经上线。")
    soft = {doc.placeholders[i] for i in doc.soft}
    assert {"PM", "ROI"} <= soft and "API" not in soft
