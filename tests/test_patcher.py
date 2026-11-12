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
    from no_ai_fluff.patcher import AUDIT_RULES, DEFAULT_AUDIT_RULES, RULE_ALIASES, resolve_rules

    assert DEFAULT_AUDIT_RULES == "v16"  # 第二代双通道门控（= v2）
    assert set(AUDIT_RULES) == {"v1", "v2", "v8", "v16"} and RULE_ALIASES == {"v1": "v8", "v2": "v16"}
    assert AUDIT_RULES["v1"] == AUDIT_RULES["v8"] and AUDIT_RULES["v2"] == AUDIT_RULES["v16"]
    assert resolve_rules("v2") == "v16" and resolve_rules("v16") == "v16"
    p = build_audit_prompt("原文X", "参考Y", "雷达Z", rules="v1")
    assert "【原文】\n原文X" in p and "参考Y" in p and "雷达Z" in p and "上面十类之一" in p
    for retired in ("v3", "v5", "v13", "v15"):
        with pytest.raises(ValueError):
            build_audit_prompt("a", "b", "c", rules=retired)


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

    plain, dense = "这是一段需要审校的中文。", "入口收紧准入门槛，挂载契约门禁和安全边界，控制并发水位与背压，避免上下文污染。"
    for rules in ("v1", "v8"):
        pipe = Pipeline(PipelineConfig(store_dir=str(tmp_path), audit_rules=rules), translator_factory=lambda c: Echo())
        prep = pipe.prepare(dense)
        assert not prep.needs_domain_triage and "11. [电报堆叠]" not in prep.audit_prompt
    pipe = Pipeline(PipelineConfig(store_dir=str(tmp_path), audit_rules="v2"), translator_factory=lambda c: Echo())
    assert "11. [电报堆叠]" not in pipe.prepare(plain).audit_prompt  # 没有电报句：基础通道
    assert pipe.prepare(dense).needs_domain_triage  # 有电报句：先请宿主做领域分诊


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


def test_paragraph_rewrite_may_move_protected_content_but_must_keep_it():
    from no_ai_fluff.patcher import MAX_FIND_BY_RULES

    src = "调用 `fetch()` 之后，系统会打通数据链路并形成闭环，再把结果写回。"
    doc = parse(src)
    texts = [s.masked for s in doc.slots]
    ok_patch = Patch(src, "系统先把结果写回，再调用 `fetch()`。", "整句改写", "隐喻黑话")
    bad_patch = Patch(src, "系统先把结果写回。", "丢了代码", "隐喻黑话")
    new, res = apply_patches(doc, texts, [ok_patch], MAX_FIND_BY_RULES["v2"])
    assert res[0].applied and "`fetch()`" in doc.render(new)
    _, res = apply_patches(doc, texts, [bad_patch], MAX_FIND_BY_RULES["v2"])
    assert not res[0].applied


def test_second_generation_tables_and_gate():
    from no_ai_fluff import patcher

    assert patcher.DEFAULT_AUDIT_RULES == "v16"
    for table in (patcher.GATED_RULES, patcher.ROUTED_RULES, patcher.DENSE_RADAR_RULES,
                  patcher.NO_CLICHE_RADAR_RULES, patcher.CJK_SPACE_RULES):
        assert set(table) == {"v2", "v16"}
    assert "电报堆叠" not in patcher.PROBLEM_TYPES["v1"] and patcher.PROBLEM_TYPES["v2"][-1] == "电报堆叠"
    plain = "- [长句/定语堆积] 一句 72 字"
    dense = plain + "\n- [电报式短句群] 一句切成 6 个短片段"
    for q in (None, "用户问题"):
        assert build_audit_prompt("原文", "参考", plain, "v16", q) == build_audit_prompt("原文", "参考", plain, "v8", q)
        assert build_audit_prompt("原文", "参考", dense, "v16", q).startswith(patcher.AUDIT_INSTRUCTIONS_V16_DENSE)
        assert (q or "") in build_audit_prompt("原文", "参考", dense, "v2", q)
    d = patcher.AUDIT_INSTRUCTIONS_V16_DENSE
    assert "拦下不合规" not in d and "通过和不通过" not in d and "一律不补" in d and "即使你觉得原文有误也不改" in d
    assert patcher.effective_rules("v8", dense) == "v8"
