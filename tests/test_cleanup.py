import pytest

from no_ai_fluff.cleanup import clean_punctuation


@pytest.mark.parametrize("dirty,clean", [
    ("因此，Q3 的技术规划重点是：：", "因此，Q3 的技术规划重点是："),
    ("完成了，。下一步", "完成了。下一步"),
    ("完成了。，下一步", "完成了。下一步"),
    ("先这样，，再那样", "先这样，再那样"),
    ("条件如下：。", "条件如下。"),
    ("第一项；。", "第一项。"),
    ("问题 ，答案", "问题，答案"),
    ("甲,，乙", "甲，乙"),
    ("甲、、乙", "甲、乙"),
])
def test_cleans_obvious_artifacts(dirty, clean):
    out, n = clean_punctuation(dirty)
    assert out == clean and n >= 1


def test_respects_author_style_in_reference():
    # 原文本来就用了连续冒号（比如刻意强调），不动
    out, n = clean_punctuation("重点是：：拆分", reference="重点是：：拆分")
    assert out == "重点是：：拆分" and n == 0


def test_does_not_touch_placeholders_ellipsis_or_dash():
    text = "调用 {{P0}} 之后……然后——结束。"
    assert clean_punctuation(text) == (text, 0)


def test_finalize_applies_cleanup(tmp_path):
    from no_ai_fluff.pipeline import Pipeline, PipelineConfig
    from no_ai_fluff.rtt import RTTResult

    class Echo:
        def round_trip(self, segs, ids, timeout_s=None):
            return RTTResult(list(segs), [""] * len(segs), [], {}, 2, 0.01)

    pipe = Pipeline(PipelineConfig(store_dir=str(tmp_path), telemetry=False, audit_rules="v8"), translator_factory=lambda c: Echo())
    prep = pipe.prepare("规划的重点是下面三点：拆分、观测、治理。")
    fin = pipe.finalize(prep.draft_id, [{"find": "重点是下面三点：", "replace": "重点是：：", "type": "冗余", "reason": "测试"}])
    assert fin.final_text == "规划的重点是：拆分、观测、治理。" and fin.cleanups == 1
