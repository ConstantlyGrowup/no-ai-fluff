"""词对微筛：代码初筛嫌疑补丁 + 脱离语境的小请求复核。"""

from no_ai_fluff.patcher import Patch
from no_ai_fluff.pipeline import Pipeline, PipelineConfig
from no_ai_fluff.reversion import build_term_review_prompt, find_suspects, parse_term_review
from no_ai_fluff.rtt import RTTResult

ORIGINAL = "## 上半年团队人力盘点情况\n\n团队重点关注降本增效。扫地机最好带自动集尘，边刷要防缠绕。"
RTT = "## 上半年团队人员分析情况\n\n团队重点关注降低成本、提高效率。扫地机最好带自动吸尘，侧刷要防缠绕。"


def test_find_suspects_only_flags_reintroduced_original_wording():
    patches = [
        Patch("人员分析", "人力盘点", "", "说人话"),            # 改回原文行话 -> 嫌疑
        Patch("降低成本、提高效率", "降本增效", "", "说人话"),    # 改回原文行话 -> 嫌疑
        Patch("自动吸尘", "自动集尘", "", "保真", "读者会以为"),  # 改回原文专名 -> 嫌疑（交给微筛保留）
        Patch("团队重点关注", "团队这半年主要在做", "", "说人话"),  # 宿主自己的新说法 -> 放行
    ]
    sus = find_suspects(patches, ORIGINAL, RTT)
    assert [s.patch_index for s in sus] == [0, 1, 2]
    assert sus[0].left == "人员分析" and sus[0].right == "人力盘点"


def test_term_review_prompt_is_small_and_context_free():
    sus = find_suspects([Patch("人员分析", "人力盘点"), Patch("侧刷", "边刷")], ORIGINAL, RTT)
    p = build_term_review_prompt(sus)
    assert "1. 人员分析 -> 人力盘点" in p and "2. 侧刷 -> 边刷" in p
    assert "团队重点关注" not in p and len(p) < 600  # 不带上下文


def test_parse_term_review_tolerates_noise():
    assert parse_term_review("<think>嗯</think>要拒收：[1, 3, 9]", 3) == {1, 3}
    assert parse_term_review("[]", 3) == set()
    assert parse_term_review([2, "3", "x"], 3) == {2, 3}
    assert parse_term_review("没有 JSON", 3) == set()


class Echo:
    def round_trip(self, segs, ids, timeout_s=None):
        rtt = {"上半年团队人力盘点情况": "上半年团队人员分析情况", "团队重点关注降本增效。扫地机最好带自动集尘，边刷要防缠绕。":
               "团队重点关注降低成本、提高效率。扫地机最好带自动吸尘，侧刷要防缠绕。"}
        return RTTResult([rtt.get(s, s) for s in segs], [""] * len(segs), [], {}, 2, 0.01)


PATCHES = [
    {"find": "人员分析", "replace": "人力盘点", "type": "说人话"},
    {"find": "降低成本、提高效率", "replace": "降本增效", "type": "说人话"},
    {"find": "自动吸尘", "replace": "自动集尘", "type": "保真", "misread": "读者会以为它只是吸尘，而不是带集尘座"},
]


def make(tmp_path):
    return Pipeline(PipelineConfig(store_dir=str(tmp_path), audit_rules="v5", telemetry=False),
                    translator_factory=lambda c: Echo())


def test_finalize_with_term_reject_revokes_jargon_but_keeps_real_fix(tmp_path):
    pipe = make(tmp_path)
    prep = pipe.prepare(ORIGINAL)
    prompt, sus = pipe.term_review(prep.draft_id, PATCHES)
    assert len(sus) == 3 and "3. 自动吸尘 -> 自动集尘" in prompt
    fin = pipe.finalize(prep.draft_id, PATCHES, term_reject="[1, 2]")
    assert fin.term_suspects == 3 and fin.term_rejected == 2 and fin.applied == 1
    assert "人员分析" in fin.final_text and "降低成本、提高效率" in fin.final_text and "自动集尘" in fin.final_text


def test_finalize_without_term_reject_keeps_backward_compatibility(tmp_path):
    pipe = make(tmp_path)
    fin = pipe.finalize(pipe.prepare(ORIGINAL).draft_id, PATCHES)
    assert fin.applied == 3 and fin.term_rejected == 0


def test_run_uses_reviewer_for_micro_filter(tmp_path):
    import json

    calls = []

    def reviewer(prompt):
        calls.append(prompt)
        return json.dumps(PATCHES, ensure_ascii=False) if len(calls) == 1 else "[1, 2]"

    prep, fin = make(tmp_path).run(ORIGINAL, reviewer)
    assert len(calls) == 2 and "意思完全不变" in calls[1]
    assert fin.term_rejected == 2 and "自动集尘" in fin.final_text and "人力盘点" not in fin.final_text


def test_no_term_review_for_older_rules(tmp_path):
    pipe = Pipeline(PipelineConfig(store_dir=str(tmp_path), audit_rules="v4", telemetry=False),
                    translator_factory=lambda c: Echo())
    assert pipe.term_review(pipe.prepare(ORIGINAL).draft_id, PATCHES) is None


def test_mcp_finalize_two_step_term_review(tmp_path):
    import io
    import json

    from no_ai_fluff.mcp_server import MCPServer

    srv = MCPServer(make(tmp_path))
    out = io.StringIO()

    def call(mid, name, args):
        out.seek(0); out.truncate()
        srv.serve(io.StringIO(json.dumps({"jsonrpc": "2.0", "id": mid, "method": "tools/call",
                                          "params": {"name": name, "arguments": args}}, ensure_ascii=False) + "\n"), out)
        return json.loads(out.getvalue())["result"]

    draft_id = call(1, "prepare_rtt_draft", {"text": ORIGINAL})["structuredContent"]["draft_id"]
    r = call(2, "finalize_patched_delivery", {"draft_id": draft_id, "patches": PATCHES})
    assert r["structuredContent"]["status"] == "needs_term_review" and len(r["structuredContent"]["pairs"]) == 3
    r = call(3, "finalize_patched_delivery", {"draft_id": draft_id, "patches": PATCHES, "term_review": [1, 2]})
    fin = r["structuredContent"]
    assert fin["term_rejected"] == 2 and "自动集尘" in fin["final_text"] and "降本增效" not in fin["final_text"]
