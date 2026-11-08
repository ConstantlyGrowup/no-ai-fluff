import pytest

from no_ai_fluff.masker import parse
from no_ai_fluff.patcher import Patch, PatchFormatError, apply_patches, build_audit_prompt, parse_patches

TEXT = "调用 `fetchUser()` 时会读 src/a.ts，这个开销很大。\n\n第二段也说这个开销很大。\n\n| 列一 | 列二 |\n|---|---|\n| 甲 | 乙 |"


def setup():
    doc = parse(TEXT)
    return doc, [s.masked for s in doc.slots]


def apply(*patches):
    doc, texts = setup()
    new, res = apply_patches(doc, texts, [Patch(*p) for p in patches])
    return doc, new, res


def test_parse_patches_variants():
    assert parse_patches("[]") == []
    assert parse_patches("") == []
    assert parse_patches('```json\n[{"find":"a","replace":"b"}]\n```')[0].replace == "b"
    assert parse_patches('好的，补丁如下：[{"find":"a","replace":"b","reason":"r"}] 完毕')[0].reason == "r"
    assert parse_patches({"patches": [{"find": "a", "replace": "b"}]})[0].find == "a"
    assert parse_patches([{"find": "a", "replace": ""}])[0].replace == ""


@pytest.mark.parametrize("bad", ["不是 JSON", '{"find": "a"}', '[1, 2]', '[{"find": "a"}]', '[{"find": 1, "replace": "b"}]'])
def test_parse_patches_rejects_bad_format(bad):
    with pytest.raises(PatchFormatError):
        parse_patches(bad)


def test_normal_patch_applies():
    doc, new, res = apply(("时会读", "时会读取", "措辞"))
    assert res[0].applied
    assert "时会读取 src/a.ts" in doc.render(new)


def test_patch_may_include_protected_content_verbatim():
    doc, new, res = apply(("调用 `fetchUser()` 时", "每次调用 `fetchUser()` 函数时", "补主语"))
    assert res[0].applied, res[0].reason
    assert "每次调用 `fetchUser()` 函数时" in doc.render(new)
    assert "{{P0}}" in new[0]  # 受保护内容仍以占位符形式留在槽位里


def test_patch_dropping_protected_content_is_rejected():
    _, _, res = apply(("会读 src/a.ts，", "会读文件，", "删路径"))
    assert not res[0].applied and "丢失" in res[0].reason


def test_patch_cutting_protected_content_is_rejected():
    _, _, res = apply(("src/a", "src/b", "改路径"))
    assert not res[0].applied and "切断" in res[0].reason


def test_patch_modifying_protected_content_is_rejected():
    _, _, res = apply(("`fetchUser()`", "`fetchUsers()`", "改函数名"))
    assert not res[0].applied


def test_patch_duplicating_protected_content_is_rejected():
    _, _, res = apply(("`fetchUser()` 时", "`fetchUser()` 和 `fetchUser()` 时", "复制"))
    assert not res[0].applied


def test_ambiguous_find_is_rejected():
    _, _, res = apply(("这个开销很大", "开销较大", "x"))
    assert not res[0].applied and "2 次" in res[0].reason


def test_missing_find_is_rejected():
    _, _, res = apply(("不存在的片段", "x", "x"))
    assert not res[0].applied


def test_cross_line_and_cross_cell_rejected():
    _, _, res = apply(("很大。\n\n第二段", "x", "跨行"), ("甲 | 乙", "甲乙", "跨单元格"))
    assert not res[0].applied and "跨行" in res[0].reason
    assert not res[1].applied


def test_placeholder_in_patch_rejected():
    _, _, res = apply(("时会读", "{{P0}}", "注入占位符"))
    assert not res[0].applied


def test_noop_and_empty_find_rejected():
    _, _, res = apply(("时会读", "时会读", "相同"), ("", "x", "空"))
    assert not res[0].applied and not res[1].applied


def test_bad_patch_does_not_block_good_ones():
    doc, new, res = apply(("src/a", "src/b", "坏"), ("第二段也说", "第二段同样指出", "好"))
    assert [r.applied for r in res] == [False, True]
    assert "第二段同样指出" in doc.render(new)


def test_sequential_patches_see_previous_results():
    doc, new, res = apply(("时会读", "时会读取", "1"), ("会读取 src/a.ts", "会读取文件 src/a.ts", "2"))
    assert all(r.applied for r in res)
    assert "时会读取文件 src/a.ts" in doc.render(new)


def test_audit_prompt_contains_all_parts():
    p = build_audit_prompt("原文X", "初稿Y", "雷达Z")
    assert "原文X" in p and "初稿Y" in p and "雷达Z" in p and "JSON" in p and "[]" in p
    assert len(p) < 2200  # v5 规则含外延判据与保真粒度约束，仍控制在约 2k 字以内


def test_salvage_extra_brace_between_objects():
    raw = '[{"find":"a","replace":"b","reason":"r"}},\n{"find":"c","replace":"d","reason":"r2"}]'
    ps = parse_patches(raw)
    assert [(p.find, p.replace) for p in ps] == [("a", "b"), ("c", "d")]


def test_salvage_missing_comma_and_trailing_comma():
    raw = '[{"find":"a","replace":"b"} {"find":"c","replace":"d"},]'
    assert [p.find for p in parse_patches(raw)] == ["a", "c"]


def test_salvage_does_not_corrupt_braces_inside_strings():
    # replace 里本来就有 “}},”，坏格式抢救时必须原样保留
    raw = '[{"find":"x","replace":"map{{k}},v"}}, {"find":"y","replace":"z"}]'
    ps = parse_patches(raw)
    assert ps[0].replace == "map{{k}},v" and ps[1].find == "y"


def test_salvage_gives_up_when_nothing_usable():
    with pytest.raises(PatchFormatError):
        parse_patches('[{"find": "a", "replace": ')


MD = "## 订单取消规则（已提交6条）\n\n1. **确认提交内容时的要点**：看第 3 个提交\n- [x] 已修复缓存\n> 引用里的话"


def apply_md(*patches):
    doc = parse(MD)
    new, res = apply_patches(doc, [s.masked for s in doc.slots], [Patch(*p) for p in patches])
    return doc.render(new), res


@pytest.mark.parametrize("find,replace,expect", [
    ("## 订单取消规则（已提交6条）", "## 订单取消规则（第 6 个提交）", "## 订单取消规则（第 6 个提交）"),
    ("1. **确认提交内容时的要点**", "1. **审阅要点**", "1. **审阅要点**：看第 3 个提交"),
    ("- [x] 已修复缓存", "- [x] 已修复缓存失效", "- [x] 已修复缓存失效"),
    ("> 引用里的话", "> 引用中的话", "> 引用中的话"),
])
def test_patch_with_same_markdown_prefix_applies(find, replace, expect):
    out, res = apply_md((find, replace, "r"))
    assert res[0].applied, res[0].reason
    assert expect in out


def test_patch_changing_structure_prefix_is_rejected():
    out, res = apply_md(("## 订单取消规则", "### 订单取消规则", "改标题级别"))
    assert not res[0].applied and "结构标记" in res[0].reason
    assert out == MD


def test_audit_rules_versions():
    from no_ai_fluff.patcher import AUDIT_RULES, DEFAULT_AUDIT_RULES

    assert DEFAULT_AUDIT_RULES == "v5"
    v1 = build_audit_prompt("原文X", "初稿Y", "雷达Z", rules="v1")
    v2 = build_audit_prompt("原文X", "初稿Y", "雷达Z", rules="v2")
    assert "严禁重写全文或润色本来正确的句子" in v1 and "翻译腔" not in AUDIT_RULES["v1"]
    assert "翻译腔" in v2 and '"type"' in v2 and "原文X" in v2
    assert len(v2) < 1800
    with pytest.raises(ValueError):
        build_audit_prompt("a", "b", "c", rules="v9")


def test_patch_type_parsed_and_optional():
    ps = parse_patches('[{"find":"a","replace":"b","type":"翻译腔"},{"find":"c","replace":"d"}]')
    assert ps[0].type == "翻译腔" and ps[1].type == ""


def test_overlong_find_rejected_as_rewrite():
    from no_ai_fluff.patcher import MAX_FIND_CHARS

    long = "长" * (MAX_FIND_CHARS + 1)
    doc = parse(long)
    _, res = apply_patches(doc, [s.masked for s in doc.slots], [Patch(long, "短", "重写")])
    assert not res[0].applied and "超出本规则允许的改写范围" in res[0].reason


def test_pipeline_uses_configured_rules(tmp_path):
    from no_ai_fluff.pipeline import Pipeline, PipelineConfig

    class Echo:
        def round_trip(self, segs, ids, timeout_s=None):
            from no_ai_fluff.rtt import RTTResult

            return RTTResult(list(segs), [""] * len(segs), [], {}, 2, 0.01)

    for rules, marker in (("v1", "严禁重写全文"), ("v2", "翻译腔")):
        pipe = Pipeline(PipelineConfig(store_dir=str(tmp_path), audit_rules=rules), translator_factory=lambda c: Echo())
        assert marker in pipe.prepare("这是一段需要审校的中文。").audit_prompt


TABLE = "| 阶段 | 时间 | 说明 |\n|---|---|---|\n| API v1 下线 | 发布后六个月。 | API v1 停止服务 |\n| 迁移期 | 半年 | 两版并存 |"


def apply_table(*patches):
    doc = parse(TABLE)
    new, res = apply_patches(doc, [s.masked for s in doc.slots], [Patch(*p) for p in patches])
    return doc.render(new), res


def test_whole_table_row_patch_split_into_cells():
    out, res = apply_table(("| API v1 下线 | 发布后六个月。 | API v1 停止服务 |",
                            "| API v1 下线 | 发布 6 个月后 | API v1 停止服务 |", "r"))
    assert res[0].applied, res[0].reason
    assert "| API v1 下线 | 发布 6 个月后 | API v1 停止服务 |" in out


def test_table_row_patch_changing_columns_rejected():
    out, res = apply_table(("| 迁移期 | 半年 | 两版并存 |", "| 迁移期 | 半年 |", "删列"))
    assert not res[0].applied and "列数" in res[0].reason and out == TABLE


def test_table_row_patch_is_all_or_nothing():
    out, res = apply_table(("| 迁移期 | 半年 | 两版并存 |", "| 迁移阶段 | 半年 | 两版并存 |", "r"),
                           ("| 迁移阶段 | 不存在的格 | 两版并存 |", "| 迁移阶段 | 六个月 | 两个版本同时可用 |", "r"))
    assert res[0].applied and not res[1].applied
    assert "| 迁移阶段 | 半年 | 两版并存 |" in out  # 第二条整条未生效，没有半改


def test_v3_rules_cover_skill_principles_and_allow_paragraph_rewrites():
    from no_ai_fluff.patcher import AUDIT_RULES, MAX_FIND_BY_RULES

    v3 = AUDIT_RULES["v3"]
    for kw in ("保真", "说人话", "尽量、通常、可能、建议、很", "原文本身就有的 AI 腔", "主谓完整", "翻译腔",
               "口号式排比", "不扩写", "严禁把整句改回原文", "仍然是局部补丁", "每个只出现一次"):
        assert kw in v3, kw
    assert MAX_FIND_BY_RULES["v3"] > MAX_FIND_BY_RULES["v2"]
    para = "这个方案打通了数据链路，" + "形成了完整的业务闭环，" * 20 + "极其关键。"
    assert MAX_FIND_BY_RULES["v2"] < len(para) <= MAX_FIND_BY_RULES["v3"]
    doc = parse(para)
    texts = [s.masked for s in doc.slots]
    _, res2 = apply_patches(doc, texts, [Patch(para, "这个方案统一了上报格式。", "整段重写")], MAX_FIND_BY_RULES["v2"])
    _, res3 = apply_patches(doc, texts, [Patch(para, "这个方案统一了上报格式。", "整段重写")], MAX_FIND_BY_RULES["v3"])
    assert not res2[0].applied and res3[0].applied


def test_v3_paragraph_rewrite_may_move_protected_content_but_must_keep_it():
    from no_ai_fluff.patcher import MAX_FIND_BY_RULES

    text = "通过调用 `fetchUser()` 对 src/a.ts 中的数据进行读取处理操作。"
    doc = parse(text)
    texts = [s.masked for s in doc.slots]
    ok_patch = Patch(text, "src/a.ts 里的数据由 `fetchUser()` 读取。", "说人话")
    bad_patch = Patch(text, "由 `fetchUser()` 读取数据。", "丢了路径")
    new, res = apply_patches(doc, texts, [ok_patch], MAX_FIND_BY_RULES["v3"])
    assert res[0].applied and doc.render(new) == "src/a.ts 里的数据由 `fetchUser()` 读取。"
    _, res = apply_patches(doc, texts, [bad_patch], MAX_FIND_BY_RULES["v3"])
    assert not res[0].applied and "丢失" in res[0].reason


def test_v3_rejects_reverting_sentence_to_original_but_allows_term_restore():
    from no_ai_fluff.patcher import REVERT_MIN_CHARS

    original = "过去六个月，我们重点攻坚了三大核心战役，为下半年的业务爆发奠定了坚实基础。"
    doc = parse(original)
    # 往返翻译后的初稿（槽位已被拆松）
    rtt = ["在过去六个月里，我们重点推进了三个关键方向，为下半年的业务增长做了准备。"]
    revert = Patch(rtt[0], original, "保真")
    term = Patch("三个关键方向", "三个方向", "术语")
    new, res = apply_patches(doc, rtt, [revert, term], 300, REVERT_MIN_CHARS["v3"])
    assert not res[0].applied and "改回了原文" in res[0].reason
    assert res[1].applied and "三个方向" in new[0]


def _v4_apply(text, rtt, *patches):
    from no_ai_fluff.patcher import MAX_FIND_BY_RULES, REVERT_MIN_CHARS

    doc = parse(text)
    return apply_patches(doc, rtt or [s.masked for s in doc.slots], list(patches),
                         MAX_FIND_BY_RULES["v4"], REVERT_MIN_CHARS["v4"], require_misread=True)


@pytest.mark.parametrize("misread,ok", [
    ("", False),
    ("不一致", False),
    ("与原文不一致", False),
    ("原文是“尽量不超过”，所以要改", False),
    ("和原文表述有差异", False),
    ("读者会以为周末起床时间必须严格控制在 1 小时以内", True),
])
def test_v4_fidelity_patch_requires_concrete_misread(misread, ok):
    _, res = _v4_apply("周末起床时间尽量不超过平时 1 小时。", ["周末起床时间不超过平时 1 小时。"],
                       Patch("不超过平时", "尽量不超过平时", "语气", "保真", misread))
    assert res[0].applied is ok, res[0].reason


def test_v4_plain_language_patch_needs_no_misread():
    _, res = _v4_apply("我们重点攻坚了三大核心战役。", ["我们重点推进了三个关键方向。"],
                       Patch("重点推进了三个关键方向", "主要做了三件事", "去口号", "说人话"))
    assert res[0].applied


def test_v4_prompt_defines_reader_centric_fidelity_and_includes_question():
    from no_ai_fluff.patcher import AUDIT_RULES

    v4 = AUDIT_RULES["v4"]
    for kw in ("会不会得出与原文不同的错误认识", "不是**保真问题", "misread", "严禁把整句改回原文", "仍然是局部补丁"):
        assert kw in v4, kw
    p = build_audit_prompt("原文X", "初稿Y", "雷达Z", rules="v4", question="怎么调作息？")
    assert "【用户问题】\n怎么调作息？" in p and p.index("【用户问题】") < p.rindex("【原文】")
    assert "\n\n【用户问题】\n" not in build_audit_prompt("原文X", "初稿Y", "雷达Z", rules="v3", question="怎么调作息？")
    assert "\n\n【用户问题】\n" not in build_audit_prompt("原文X", "初稿Y", "雷达Z", rules="v4", question="  ")


def test_patch_misread_roundtrips_in_results():
    from no_ai_fluff.patcher import results_to_dicts

    ps = parse_patches('[{"find":"a","replace":"b","type":"保真","misread":"读者会以为 a 是 b"}]')
    doc = parse("这里有 a 字。")
    _, res = apply_patches(doc, [s.masked for s in doc.slots], ps)
    assert results_to_dicts(res)[0]["misread"] == "读者会以为 a 是 b"


def test_v5_removes_copy_inducing_rule_and_puts_draft_first():
    from no_ai_fluff.patcher import AUDIT_RULES

    v5 = AUDIT_RULES["v5"]
    assert "原文作者明确使用的说法" not in v5
    for kw in ("外延判据", "指的不是同一个东西", "人员分析", "不超过 20 字", "不要从【AI 原始底稿】里搬词"):
        assert kw in v5, kw
    p = build_audit_prompt("底稿X", "初稿Y", "雷达Z", rules="v5", question="问题Q")
    assert p.rindex("【用户问题】") < p.rindex("【RTT 初稿】") < p.rindex("【AI 原始底稿（含行话套话")
    assert p.index("初稿Y") < p.index("底稿X")


def test_v5_fidelity_patch_limited_to_minimal_phrase():
    from no_ai_fluff.patcher import FIDELITY_MAX_FIND

    doc = parse("预算三千左右，家里有猫，地板是木地板，最好选带自动集尘的型号。")
    rtt = ["预算大约三千，家中养猫，地面为木地板，最好选择带有自动吸尘功能的型号。"]
    short = Patch("自动吸尘", "自动集尘", "术语", "保真", "读者会以为是吸尘功能而不是集尘座")
    long_ = Patch("地面为木地板，最好选择带有自动吸尘功能的型号", "地面为木地板，最好选带自动集尘的型号", "术语", "保真",
                  "读者会以为是吸尘功能")
    assert len(long_.find) > FIDELITY_MAX_FIND["v5"]
    _, res = apply_patches(doc, rtt, [short], 300, 25, True, FIDELITY_MAX_FIND["v5"])
    assert res[0].applied
    _, res = apply_patches(doc, rtt, [long_], 300, 25, True, FIDELITY_MAX_FIND["v5"])
    assert not res[0].applied and "最小词组" in res[0].reason
