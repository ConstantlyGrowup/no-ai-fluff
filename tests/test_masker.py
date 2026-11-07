import pytest

from no_ai_fluff.masker import (
    Masker,
    check_placeholders,
    detect_format,
    mask_text,
    normalize_placeholders,
    parse,
    unmask,
)

MD = """---
title: 测试
---
## 根因分析

这个 edge case 的 overhead 是 non-trivial 的，`fetchUser()` 在 src/api/user.ts 里调用 Redis。

```python
x = 1  # 代码块里的中文不翻译
```

$$
E = mc^2
$$

- [x] 修复了 **缓存** 失效，见 [文档](https://example.com/doc) 和 #123
> 引用里的正文

| 指标 | 值 |
|---|---|
| P99 延迟 | 120ms |
"""


def test_markdown_render_roundtrip_is_lossless():
    doc = parse(MD)
    assert doc.fmt == "markdown"
    assert doc.render() == MD


def test_markdown_structure_not_in_slots():
    doc = parse(MD)
    joined = "\n".join(s.original for s in doc.slots)
    assert "x = 1" not in joined  # 围栏代码块
    assert "mc^2" not in joined  # 数学块
    assert "title: 测试" not in joined  # front matter
    assert "|---|" not in joined
    assert not any(s.original.startswith(("## ", "- [x]", "> ")) for s in doc.slots)
    assert "根因分析" in [s.original for s in doc.slots]


@pytest.mark.parametrize(
    "frozen",
    ["`fetchUser()`", "src/api/user.ts", "Redis", "https://example.com/a?b=1", "#123", "O(n log n)",
     "user_id", "getUserName", "HttpClient", "v1.2.3", "API", "$x^2$", "C:\\Users\\a\\b.txt", "a1b2c3d4e5"],
)
def test_inline_freezing(frozen):
    masked, ph = mask_text(f"这里提到 {frozen} 的情况。")
    assert frozen in ph
    assert frozen not in masked
    assert unmask(masked, ph) == f"这里提到 {frozen} 的情况。"


@pytest.mark.parametrize("loose", ["edge case", "overhead", "non-trivial", "trade-off", "fallback", "sync"])
def test_loose_english_is_exposed(loose):
    masked, ph = mask_text(f"这个 {loose} 需要处理。")
    assert loose in masked
    assert ph == []


def test_freezing_works_next_to_cjk_without_spaces():
    masked, ph = mask_text("用Redis缓存，调用getUser()后返回")
    assert "Redis" in ph and "getUser()" in ph


def test_placeholder_ids_are_global_and_unique():
    doc = parse("第一段用 `a` 和 `b`。\n\n第二段用 `c`。")
    ids = [i for s in doc.slots for i in s.ids]
    assert ids == [0, 1, 2]
    assert doc.placeholders == ["`a`", "`b`", "`c`"]


def test_existing_double_brace_template_is_frozen():
    masked, ph = mask_text("模板变量 {{user}} 不能动")
    assert "{{user}}" in ph


def test_html_skips_code_script_and_attributes():
    html = '<div class="说明"><p>这个方案的 overhead 很大</p><pre>不要翻译</pre><script>var a="中文"</script><code>x</code></div>'
    doc = parse(html)
    assert doc.fmt == "html"
    texts = [s.original for s in doc.slots]
    assert texts == ["这个方案的 overhead 很大"]
    assert doc.render() == html


def test_plain_text_format():
    doc = parse("第一行\n\n第二行", fmt="text")
    assert [s.original for s in doc.slots] == ["第一行", "第二行"]
    assert doc.render() == "第一行\n\n第二行"


def test_crlf_and_nul_are_normalized():
    doc = parse("第一行\r\n第二\x00行", fmt="text")
    assert doc.render() == "第一行\n第二行"


def test_translatable_only_when_cjk_remains():
    m = Masker()
    assert m.slot("纯中文").translatable
    assert not m.slot("`code` 120ms").translatable


def test_check_placeholders_detects_missing_extra_dup():
    assert check_placeholders("{{P0}}{{P1}}", [0, 1]).ok
    c = check_placeholders("{{P0}}{{P0}}{{P2}}", [0, 1])
    assert not c.ok and c.missing == [1] and c.extra == [2] and c.duplicated == [0]


def test_normalize_loose_placeholders():
    assert normalize_placeholders("a {{ P0 }} b ｛｛P1｝｝ c {p2}") == "a {{P0}} b {{P1}} c {{P2}}"


def test_detect_format():
    assert detect_format("<p>你好</p>") == "html"
    assert detect_format("## 标题\n正文") == "markdown"
    assert detect_format("普通一句话") == "text"
