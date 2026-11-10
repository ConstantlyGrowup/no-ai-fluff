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


def test_chinese_numeral_forms():
    from no_ai_fluff.radar import to_chinese_numeral

    assert to_chinese_numeral(3) == {"三"}
    assert to_chinese_numeral(2) == {"二", "两"}
    assert to_chinese_numeral(10) == {"十"}
    assert to_chinese_numeral(12) == {"十二"}
    assert to_chinese_numeral(20) == {"二十"}
    assert to_chinese_numeral(105) == {"一百零五"}
    assert "两千" in to_chinese_numeral(2000)
    assert to_chinese_numeral(100000) == set()


def test_digit_to_chinese_numeral_is_not_a_mismatch():
    assert compare_numbers("需要跨 3 个服务查日志，发布 6 个月后下线", "需要查看三个服务的日志，发布后六个月下线") == []
    assert compare_numbers("第 3–8 周", "第三至第八周") == []
    assert compare_numbers("二十个实例", "20 个实例") == []
    # 真正的改动仍会报
    hs = compare_numbers("延迟 120ms，3 个服务", "延迟 150ms，三个服务")
    assert hs and "120" in hs[0].message and "150" in hs[0].message


# ---- 优化闭环第 2 轮：高密度句、句首连列 ----


def test_dense_sentence_and_list_first_flagged_in_prose():
    from no_ai_fluff.radar import scan_slot
    dense = ("读回比对一致后写完成标记，原键与归档都不删除，也不自动归属任何账号，"
             "迁移失败时保留旧数据并提示用户手动处理，同时记录日志，便于之后排查和回滚操作。")
    hs = [h for h in scan_slot(dense) if h.kind == "long_clause"]
    assert any("信息太密" in h.message for h in hs)
    lst = "看房时要问清楚：中介费、服务费、物业费、供暖费、网费分别由谁出，这些都要写进合同里，免得以后扯皮。"
    assert any("连列 5 项" in h.message for h in scan_slot(lst))


def test_short_cells_and_headings_not_flagged():
    from no_ai_fluff.radar import scan_slot
    cell = "需求开发、Bug 修复、测试编写、代码评审"
    assert not [h for h in scan_slot(cell) if "连列" in h.message]


def test_prompt_hint_limit_raised():
    from no_ai_fluff.radar import Hint, RadarReport
    r = RadarReport([Hint("cliche", 0, f"x{i}", "套话") for i in range(40)])
    lines = r.to_prompt().splitlines()
    assert len(lines) == 31 and "另有 10 条" in lines[-1]
