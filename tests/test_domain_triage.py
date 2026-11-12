"""v16 宿主领域分诊握手：生产路径不连外部 API，缺的分诊判定由宿主模型补上；往返翻译只跑一次。"""
import io
import json

from no_ai_fluff.mcp_server import MCPServer
from no_ai_fluff.patcher import AUDIT_INSTRUCTIONS_V8, AUDIT_INSTRUCTIONS_V16_DENSE, DEFAULT_AUDIT_RULES
from no_ai_fluff.pipeline import Pipeline, PipelineConfig
from no_ai_fluff.telegraph_router import DOMAIN_SPEC, TelegraphRouter, parse_host_triage

JARGON = "入口收紧准入门槛，挂载契约门禁和安全边界；中间层编排多个分支，控制预算、并发水位与背压，避免上下文污染。"
TRIP = "注意：五一园林人多，门票提前约，早上早点进，别追网红店排队，晚上逛平江路。"
TEXT = f"{JARGON}\n\n{TRIP}\n\n我们要打通链路，形成闭环。"


def make(fake_translator, tmp_path, router=None, calls=None):
    calls = calls if calls is not None else []

    def factory(cfg):
        calls.append(1)
        return fake_translator()

    pipe = Pipeline(PipelineConfig(store_dir=str(tmp_path / "d"), telemetry=False), translator_factory=factory, router=router)
    return pipe, calls


def test_default_is_v16():
    assert DEFAULT_AUDIT_RULES == "v16"


def test_prepare_asks_host_then_dense_without_rerunning_rtt(fake_translator, tmp_path):
    pipe, calls = make(fake_translator, tmp_path)
    prep = pipe.prepare(TEXT)
    assert prep.needs_domain_triage and prep.audit_prompt == ""
    assert len(prep.triage_sentences) == 2 and prep.triage_sentences[0].startswith("入口收紧")
    assert '"technical"' in prep.triage_prompt and "1. 入口收紧" in prep.triage_prompt
    assert len(calls) == 1
    done = pipe.complete_prepare(prep.draft_id, [True, False])
    assert not done.needs_domain_triage and len(calls) == 1  # 往返翻译不重跑
    assert done.audit_prompt.startswith(AUDIT_INSTRUCTIONS_V16_DENSE)
    assert "[电报式短句群]" in done.radar_hints
    assert "[套话/黑话]" not in done.radar_hints  # v16：运行时不跑静态黑话词表
    assert done.stats.get("domain_triage") == "host" and done.stats.get("domain_dense") is True
    # 判定已写进本地缓存：同一段文字再来一次，一步直通
    again = pipe.prepare(TEXT)
    assert not again.needs_domain_triage and again.audit_prompt.startswith(AUDIT_INSTRUCTIONS_V16_DENSE)
    assert again.stats.get("domain_triage") == "cache"


def test_all_false_falls_back_to_v8_without_wordlist(fake_translator, tmp_path):
    pipe, _ = make(fake_translator, tmp_path)
    prep = pipe.prepare(TEXT)
    done = pipe.complete_prepare(prep.draft_id, False)  # 单个布尔：整篇同一判断
    assert done.audit_prompt.startswith(AUDIT_INSTRUCTIONS_V8)
    assert "[电报式短句群]" not in done.radar_hints and "[套话/黑话]" not in done.radar_hints
    assert done.stats.get("domain_dense") is False


def test_unparseable_triage_is_conservative_and_not_cached(fake_translator, tmp_path):
    pipe, _ = make(fake_translator, tmp_path)
    prep = pipe.prepare(TEXT)
    done = pipe.complete_prepare(prep.draft_id, "说不清")
    assert done.audit_prompt.startswith(AUDIT_INSTRUCTIONS_V16_DENSE)  # 保守：按技术语境走强化通道
    assert pipe.prepare(TEXT).needs_domain_triage  # 解析失败的判定不写缓存


def test_parse_host_triage_forms():
    assert parse_host_triage(True, 3) == [True, True, True]
    assert parse_host_triage([False, True], 3) == [False, True, None]
    assert parse_host_triage('[{"id": 2, "technical": false}]', 2) == [None, False]
    assert parse_host_triage({"technical": False}, 2) == [False, False]
    assert parse_host_triage("否", 1) == [False]
    assert parse_host_triage("[true, false]", 2) == [True, False]
    assert parse_host_triage(None, 2) == [None, None]


def test_run_uses_same_reviewer_for_triage_and_audit(fake_translator, tmp_path):
    pipe, calls = make(fake_translator, tmp_path)
    seen = []

    def reviewer(prompt):
        seen.append(prompt)
        if '"technical"' in prompt:
            return '[{"id": 1, "technical": false}, {"id": 2, "technical": false}]'
        return "[]"

    prep, fin = pipe.run(TEXT, reviewer=reviewer)
    assert len(seen) == 2 and '"technical"' in seen[0] and seen[1].startswith(AUDIT_INSTRUCTIONS_V8)
    assert prep.audit_prompt == seen[1] and not prep.needs_domain_triage
    assert fin.final_text == TEXT and not fin.fallback and len(calls) == 1


def test_run_triage_error_still_audits(fake_translator, tmp_path):
    pipe, _ = make(fake_translator, tmp_path)
    seen = []

    def reviewer(prompt):
        seen.append(prompt)
        if '"technical"' in prompt:
            raise RuntimeError("boom")
        return "[]"

    prep, fin = pipe.run(TEXT, reviewer=reviewer)
    assert len(seen) == 2 and seen[1].startswith(AUDIT_INSTRUCTIONS_V16_DENSE)
    assert "domain triage error" in (fin.fallback_reason or "")


def test_injected_router_resolves_in_one_step(fake_translator, tmp_path):
    asked = []

    def chat(prompt):
        asked.append(prompt)
        return json.dumps([{"id": 1, "technical": True}, {"id": 2, "technical": False}]), {}

    pipe, _ = make(fake_translator, tmp_path, router=TelegraphRouter(chat, tmp_path / "r", spec=DOMAIN_SPEC))
    prep = pipe.prepare(TEXT)
    assert not prep.needs_domain_triage and len(asked) == 1
    assert prep.audit_prompt.startswith(AUDIT_INSTRUCTIONS_V16_DENSE)


def test_no_telegraph_means_no_handshake(fake_translator, tmp_path):
    pipe, _ = make(fake_translator, tmp_path)
    prep = pipe.prepare("这个方案的开销很大，我们下周再讨论。")
    assert not prep.needs_domain_triage and prep.audit_prompt.startswith(AUDIT_INSTRUCTIONS_V8)


def _rpc(srv, args):
    msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "prepare_rtt_draft", "arguments": args}}
    out = io.StringIO()
    srv.serve(io.StringIO(json.dumps(msg, ensure_ascii=False) + "\n"), out)
    return json.loads(out.getvalue())["result"]


def test_mcp_domain_triage_handshake(fake_translator, tmp_path):
    pipe, calls = make(fake_translator, tmp_path)
    srv = MCPServer(pipe)
    r1 = _rpc(srv, {"text": TEXT})
    sc = r1["structuredContent"]
    assert sc["status"] == "needs_domain_triage" and [s["id"] for s in sc["sentences"]] == [1, 2]
    assert sc["draft_id"] in r1["content"][0]["text"] and "domain_triage" in r1["content"][0]["text"]
    r2 = _rpc(srv, {"draft_id": sc["draft_id"], "domain_triage": [{"id": 1, "technical": True}, {"id": 2, "technical": False}]})
    assert not r2.get("isError") and r2["structuredContent"]["draft_id"] == sc["draft_id"]
    assert "finalize_patched_delivery" in r2["content"][0]["text"]
    assert AUDIT_INSTRUCTIONS_V16_DENSE.splitlines()[0] in r2["content"][0]["text"]
    assert len(calls) == 1
    assert _rpc(srv, {"draft_id": "nope", "domain_triage": True})["isError"]
    assert _rpc(srv, {"draft_id": sc["draft_id"]})["isError"]  # 只给 draft_id 不给判断、也没有 text
    schema = [t for t in srv.tools if t["name"] == "prepare_rtt_draft"][0]["inputSchema"]
    assert "required" not in schema and {"draft_id", "domain_triage"} <= set(schema["properties"])
