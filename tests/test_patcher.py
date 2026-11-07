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
    assert len(p) < 1600  # 提示词保持在甜点区


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
