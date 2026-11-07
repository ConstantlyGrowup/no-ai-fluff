from no_ai_fluff.radar import compare_numbers, extract_numbers, normalize_number, scan, scan_document, scan_slot


def kinds(hints):
    return {h.kind for h in hints}


def test_long_clause_and_de_chain():
    long = "这" * 45
    assert "long_clause" in kinds(scan_slot(long))
    assert "long_clause" in kinds(scan_slot("我的朋友的老师的方案"))
    assert "long_clause" not in kinds(scan_slot("短句，没有问题。"))


def test_number_consistency():
    assert compare_numbers("耗时 15秒，命中率 82.7%", "耗时 15 秒，命中率 82,7%") == []  # 法式小数逗号
    hints = compare_numbers("耗时 15秒，命中率 82.7%", "耗时 15秒，命中率 82%")
    assert hints and "82.7" in hints[0].message and "82" in hints[0].message


def test_number_normalization():
    assert normalize_number("1,000") == "1000"
    assert normalize_number("82,7") == "82.7"
    assert normalize_number("3.50") == "3.5"
    assert extract_numbers("{{P12}} 有 3 个") == extract_numbers("有 3 个")  # 占位符编号不计入


def test_cliches():
    hs = scan_slot("值得注意的是，这个方案深度赋能了业务闭环，综上所述极其关键。")
    words = {h.excerpt for h in hs if h.kind == "cliche"}
    assert {"值得注意的是", "赋能", "闭环", "综上所述", "极其"} <= words


def test_loose_english_ignores_units_and_placeholders():
    hs = scan_slot("延迟 120ms，{{P0}} 的 overhead 较高")
    eng = [h for h in hs if h.kind == "loose_english"]
    assert eng and eng[0].excerpt == "overhead"


def test_pronoun_density():
    assert "pronoun" in kinds(scan_slot("当它调用它时，它会通知其重试。"))
    assert "pronoun" not in kinds(scan_slot("网关调用支付服务，其他模块不受影响。"))


def test_scan_respects_only_and_reports_counts():
    rep = scan(["原文 1 个", "原文 2 个"], ["初稿 1 个", "初稿 3 个，值得注意的是"], only=[1])
    assert rep.counts["number"] == 1 and rep.counts["cliche"] == 1
    assert all(h.slot == 1 for h in rep.hints)
    assert "数字不一致" in rep.to_prompt()


def test_empty_report_prompt_still_demands_audit():
    assert "全量语义审校" in scan([], []).to_prompt()


def test_scan_document_skips_code():
    rep = scan_document("正文没问题。\n\n```\n值得注意的是 赋能\n```\n")
    assert rep.counts["cliche"] == 0
